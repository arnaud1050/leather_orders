"""
SQLAlchemy models + first-boot bootstrap.

Company is the tenant boundary: everything else (users, clients, and
transitively orders/documents) hangs off a company_id. Scoping every query
by company_id from the start is what made the second tenant additive rather
than a rewrite — `create_company()` below provisions one, and `/admin` is
where a platform admin calls it from (see admin/CLAUDE.md).

`seed_if_empty()` creates the *first* company, its admin user and the
per-company option lists — no sample clients, orders or invoices, so a
production deployment starts empty. The demo dataset lives in
`sample_data.py`, loaded on demand by `scripts/seed_sample_data.py`.

Both go through `create_company()`, deliberately: one provisioning path
means the tenth tenant gets exactly what the first one did.
"""

import re
from datetime import date

import sqlalchemy as sa
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.ext.hybrid import hybrid_property
from werkzeug.security import check_password_hash, generate_password_hash

# billing.documents imports nothing but billing.tax, which imports nothing
# at all — so this is safe here even though billing.models imports `db`
# back out of this file. Anything deeper in billing (services, models) is
# imported lazily inside methods below, for exactly that reason.
from billing.documents import format_address

db = SQLAlchemy()

# The studio is in Vancouver, so that's the default a new company gets rather
# than UTC — a time only means something to the person reading it in their own
# zone. TIME_ZONES in app.py is the list offered at /settings.
DEFAULT_TIMEZONE = "America/Vancouver"


class Company(db.Model):
    """The tenant.

    The invoice letterhead (address, registration numbers, number prefix,
    payment instructions) used to live here and now belongs to the billing
    module — `billing.services.invoicing.profile_for(company_id)`. A
    company is called the same thing whether or not it invoices, so only
    the name stayed.
    """

    __tablename__ = "companies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)

    # A tenant is switched off, never deleted — it owns invoices, and an
    # issued invoice is not ours to erase (hard rules 8 and 11). Deactivating
    # blocks sign-in for every user under it and greys the row in /admin; it
    # changes no order, no invoice and no figure. Reactivating is the exact
    # inverse, which is the whole point of doing it this way.
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    # IANA zone name. Timestamps are stored as naive UTC everywhere (see the
    # communications module's docstring); this is only how they're *rendered*,
    # so changing it re-labels history rather than rewriting it.
    timezone = db.Column(db.String(60), nullable=False, default=DEFAULT_TIMEZONE)

    # JSON list of {"key", "visible"} dicts, in display order, for the Orders
    # list columns — see ORDER_COLUMNS / _order_columns_for() in app.py. Null
    # until a company saves a preference, at which point every column reads
    # the default order and stays visible.
    order_columns = db.Column(db.Text)

    # JSON list of {"key", "cards"} dicts, in display order, for the Analytics
    # page's sections and the cards inside each — see ANALYTICS_SECTIONS /
    # _analytics_layout_for() in app.py. Null until a company drags something,
    # at which point every section and card reads the default order. Layout
    # only: nothing here changes what a number means.
    analytics_layout = db.Column(db.Text)

    users = db.relationship("User", back_populates="company")
    clients = db.relationship("Client", back_populates="company")
    source_options = db.relationship(
        "SourceOption", back_populates="company",
        order_by="SourceOption.sort_order",
    )
    order_types = db.relationship(
        "OrderType", back_populates="company",
        order_by="OrderType.sort_order",
    )


class User(db.Model, UserMixin):
    """A person who signs in — either a tenant's user or platform staff.

    **Email is the identity, not a username.** Usernames were globally
    unique, which is fine for one tenant and impossible for many — the
    second studio to sign up also wants to be `admin`. Email is naturally
    unique across the whole platform, so it stays the single unique column
    and `full_name` is free to repeat. It's also the only identifier a
    password-reset flow could ever use, so switching now costs one
    migration instead of two.

    **The two kinds of user are mutually exclusive**, and that's the whole
    shape of the thing:

    * A **tenant user** has a `company_id` and no platform rights. They see
      their studio and nothing else.
    * A **platform admin** has `is_platform_admin` and **no company at
      all**. They see `/admin` and nothing else — no timeline, no clients,
      no invoices, because those questions have no answer for somebody who
      isn't in a studio.

    Nothing enforces this at the database level (SQLite has no partial
    check we'd want to migrate around), so `is_tenant_user` /
    `is_staff` below are the readable form of it and
    `admin.services` refuses to create anything else. The invariant matters
    because it's what stops the person who administers the installation
    from quietly being a member of one customer's company.
    """

    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    # Null for platform staff — see the class docstring. Every *tenant*
    # query still filters on it (hard rule 1); the app simply refuses to
    # serve a tenant route to somebody who hasn't got one, rather than
    # letting a null leak into 155 call sites as "no filter".
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"))
    # Stored lower-cased and stripped (see `normalise_email`) so that
    # "Marie@..." and "marie@..." can't become two accounts. The uniqueness
    # is a separate index rather than an inline UNIQUE, because SQLite can
    # neither add a UNIQUE column by ALTER nor drop one later — which is
    # exactly the corner the old `username` column painted us into.
    email = db.Column(db.String(255), nullable=False, index=True, unique=True)
    # Display only — what settings pages and the admin area call this person.
    # Deliberately not unique: two companies may each have a "Studio Admin",
    # and that's none of the platform's business.
    full_name = db.Column(db.String(120))
    password_hash = db.Column(db.String(255), nullable=False)
    # Same hide-don't-delete reasoning as Company.is_active: a user has
    # written emails and drafted invoices, so the row has to stay. Flask-Login
    # reads this attribute off UserMixin, so overriding it with a real column
    # means `login_user()` refuses a deactivated account on its own — the
    # login route checks it too, only so the message says something useful.
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    # Platform staff: may reach /admin, provision companies, and impersonate.
    # Always paired with a null company_id — see the class docstring. The
    # way staff look at a studio's data is to impersonate somebody inside
    # it, which borrows that person's company_id and so goes through the
    # ordinary filtered code path rather than around it.
    is_platform_admin = db.Column(db.Boolean, nullable=False, default=False)
    # How this person signs off an email. Per user rather than per company
    # or per mailbox, because a signature is written by a person: two people
    # sharing one studio@ address each want their own, and a company-level
    # one would be wrong the day a second user exists. Used by the compose
    # box and appended to AI-drafted replies — see `signature_block`.
    signature = db.Column(db.Text)

    company = db.relationship("Company", back_populates="users")

    @property
    def is_staff(self) -> bool:
        """Platform staff — reaches /admin, belongs to no studio."""
        return self.company_id is None

    @property
    def is_tenant_user(self) -> bool:
        """A studio's own user — reaches the app, never /admin.

        Written as "has a company" rather than "isn't a platform admin"
        because the company is the thing every tenant route actually needs;
        a user with no company has no answer to give the timeline, and
        that's the condition worth testing.
        """
        return self.company_id is not None

    @property
    def display_name(self) -> str:
        """What to call this person on screen.

        Falls back to the email rather than to an empty string: a settings
        page reading "Changes the password for **_____**" is worse than one
        naming an address the reader recognises.
        """
        return (self.full_name or "").strip() or self.email

    @property
    def signature_block(self) -> str:
        """The signature as it's appended to a message body — a blank line
        and then the signature, or nothing at all when none is set.

        A property rather than string-building at each call site, because
        there are three (the compose box, the AI draft, and whatever comes
        next) and "sometimes two newlines, sometimes none" is exactly the
        kind of detail that drifts apart between them.
        """
        signature = (self.signature or "").strip()
        return f"\n\n{signature}" if signature else ""

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


def normalise_email(value: str) -> str:
    """The one place an address is folded to its stored form.

    Called on every write *and* on the login lookup, so the two can't drift
    apart — a user who registered as "Marie@Example.com" and types
    "marie@example.com" is the same person, and a database that thinks
    otherwise is a support ticket.
    """
    return (value or "").strip().lower()


client_sources = db.Table(
    "client_sources",
    db.Column("client_id", db.Integer, db.ForeignKey("clients.id"), primary_key=True),
    db.Column("source_option_id", db.Integer, db.ForeignKey("source_options.id"), primary_key=True),
)


class SourceOption(db.Model):
    """A company-configurable "how did you hear about us" choice.

    Never hard-deleted once at least one client references it (see
    can_delete below) — stats/reporting need history to stay intact.
    Instead it's hidden (is_active=False): it stops appearing as a
    selectable option on the client page, but stays visible/readable on
    any client that already had it checked.
    """
    __tablename__ = "source_options"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    label = db.Column(db.String(120), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    # At most one option per company can carry a free-text box alongside its
    # checkbox on the client page (see /settings/clients), written into
    # Client.other_source_detail. "Other, please specify" is the obvious
    # use, not the only one — this is a plain boolean rather than a fixed
    # "Other" label match, since it's really "pair a text box with this
    # option", usable on whichever one actually needs it.
    is_other = db.Column(db.Boolean, nullable=False, default=False)

    company = db.relationship("Company", back_populates="source_options")
    clients = db.relationship("Client", secondary=client_sources, back_populates="sources")

    @property
    def can_delete(self):
        return len(self.clients) == 0


class OrderType(db.Model):
    """A company-configurable order category (e.g. "Custom Order", "White
    Label", "Consulting/Sampling"). Same hide-don't-delete shape as
    SourceOption, for the same reason: once an order references one, it
    needs to stay meaningful in the orders list / timeline pill rather than
    disappearing. Optional per order — a company that hasn't defined any
    types yet just doesn't get the dropdown (see new_order()/order_page()
    in app.py).
    """
    __tablename__ = "order_types"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    label = db.Column(db.String(120), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    company = db.relationship("Company", back_populates="order_types")
    orders = db.relationship("Order", back_populates="order_type")

    @property
    def can_delete(self):
        return len(self.orders) == 0


class Client(db.Model):
    __tablename__ = "clients"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120))
    phone = db.Column(db.String(40))
    # Billing address, structured like Company's — and structured for a
    # concrete reason, not tidiness: sales tax is charged at the client's
    # province's rate (see taxes_for), so `province` has to be a real
    # field rather than something buried in free text.
    street = db.Column(db.String(200))
    city = db.Column(db.String(120))
    province = db.Column(db.String(2))  # two-letter code; see PROVINCES in app.py
    postal_code = db.Column(db.String(10))
    # Populated when a client originates from the bymonsieur.ca contact
    # form (via a Make.com webhook, see /api/leads) rather than being added
    # by staff. Blank for manually-created clients.
    inquiry_type = db.Column(db.String(120))
    first_message = db.Column(db.Text)
    # Free text behind the SourceOption marked is_other — the written
    # answer for whichever option a company has paired with a text box.
    # Blank whenever that option isn't one of this client's sources; not
    # cleared automatically if the option later loses its text box, so
    # nothing already on file is lost, it just stops rendering anywhere.
    other_source_detail = db.Column(db.String(200))
    # Free-form staff notes about the client (not shown to the client, no
    # relation to an order's own notes) — same free-text Text column and
    # "quick edit -> modal, more room -> page" absence as Order.notes; there's
    # no timeline modal for clients to omit it from in the first place, so
    # it's just on the one edit form the client page already has.
    notes = db.Column(db.Text)
    # Hide, don't delete — a client is referenced by orders, invoices and
    # email threads, so there is no delete here at all, not even a
    # can_delete escape hatch like SourceOption's.
    #
    # Hiding is a statement about the *roster* and nothing else: it takes
    # the client off /clients and out of the new-order dropdown, and
    # touches none of their orders, invoices, payments or mail. An order is
    # time the studio actually spent and money it was actually owed, and no
    # decision about a contact list gets to retract that — which is the
    # same argument behind cancelled orders staying in the lists and hidden
    # SourceOptions still counting in the analytics breakdown.
    is_hidden = db.Column(db.Boolean, nullable=False, default=False)

    company = db.relationship("Company", back_populates="clients")
    orders = db.relationship("Order", back_populates="client")
    sources = db.relationship("SourceOption", secondary=client_sources, back_populates="clients")

    @hybrid_property
    def name(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def formatted_address(self) -> str | None:
        return format_address(self.street, self.city, self.province, self.postal_code)

    @property
    def is_returning(self):
        """A second order marks a client as a repeat customer."""
        return len(self.orders) >= 2

    @property
    def lifetime_value(self):
        """What this client's kept orders are worth.

        Cancelled orders are excluded: work that was called off was never
        business done, and counting it makes a client look better than they
        were — which matters, because this figure ranks the "Top 5 paying
        clients" and the timeline's highest-paying-client sort.

        Still `total` and not `amount_paid`, so this stays "value of orders
        placed", tax-inclusive, not "money received" — Analytics' Revenue
        reads `Payment` rows for that.
        """
        return sum(
            order.total for order in self.orders if order.status != "cancelled"
        )


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    item = db.Column(db.String(200), nullable=False)
    start = db.Column(db.Date, nullable=False)
    due = db.Column(db.Date, nullable=False)
    # Optional — when the client actually collects the finished piece, which
    # can slip past the due date. Distinct from `due` (when it's promised
    # done by) rather than reusing it.
    pickup_date = db.Column(db.Date)
    status = db.Column(db.String(20), nullable=False)
    # Urgency, not a lifecycle stage — the two shared an enum once and an
    # order could not be both rush and ready for pickup. Only meaningful
    # while the order is active; see `is_active`.
    is_rush = db.Column(db.Boolean, nullable=False, default=False)
    notes = db.Column(db.Text)
    # Optional — a company with no OrderTypes defined never shows the
    # dropdown at all (see new_order()/order_page() in app.py), so this
    # stays nullable rather than needing a fallback "Uncategorized" row.
    order_type_id = db.Column(db.Integer, db.ForeignKey("order_types.id"))

    client = db.relationship("Client", back_populates="orders")
    order_type = db.relationship("OrderType", back_populates="orders")
    lines = db.relationship(
        "OrderLine", back_populates="order", cascade="all, delete-orphan",
        order_by="OrderLine.sort_order",
    )
    payments = db.relationship("Payment", back_populates="order", cascade="all, delete-orphan")
    # The billing module owns Invoice; the host wires the relationship,
    # because billing must not know that "subject" means "order" here.
    invoice = db.relationship(
        "Invoice", uselist=False, cascade="all, delete-orphan",
        primaryjoin="Order.id == foreign(Invoice.subject_id)",
    )

    # ----- lifecycle -------------------------------------------------
    # All derived from `status` and the dates, never stored: a second
    # column recording "is this active" is a column that can disagree
    # with the status it was derived from (hard rule 10).

    @property
    def is_active(self):
        """Work the studio still owes someone. The timeline shows these
        plus `tentative`; the two final stages come off it."""
        return self.status in ("confirmed", "ready")

    @property
    def display_status(self):
        """The stage's name *for display*, which depends on the calendar.

        `confirmed` and `in_progress` are one stored stage wearing two
        labels: an order is "Confirmed" until its start date arrives and
        "In progress" from then on. Deriving it off `start` rather than
        storing a separate stage means it can never sit at "confirmed"
        three weeks into the work because nobody advanced a dropdown —
        same reasoning as `invoice_status` below.
        """
        if self.status == "confirmed" and self.start <= date.today():
            return "in_progress"
        return self.status

    @property
    def can_delete(self):
        """Hard delete is for orders that never became real.

        Tentative is the only stage that qualifies, and even then not once
        something is attached that deleting would have to make a decision
        about. Everything else follows hard rule 8 — hide, don't delete.

        Three blockers, each for the same reason: the app must not decide
        on the user's behalf.

        * **an invoice or a payment** — money attached is a record worth
          keeping, so the order gets cancelled instead
        * **materials drawn from stock** — deleting can either put that
          stock back or leave it spent, and only the maker knows which. A
          prototype cut from that leather consumed it whether or not the
          order survived. So the user clears the materials on the Materials
          tab first, deciding restock-or-not one row at a time (which is
          exactly what `delete_material()` asks them), and only then can the
          order go. Cancelling is the path that keeps them untouched.

        One-off "Other" costs don't block: they carry no stock, so removing
        them decides nothing — same as the order's own line items.
        """
        # Imported here, not at module scope: inventory.models imports `db`
        # out of this file, so a top-level import would be circular. Same
        # dodge as the billing properties below.
        from inventory import services as inventory_service

        return (
            self.status == "tentative"
            and self.invoice is None
            and not self.payments
            and not inventory_service.list_materials_for_order(self.id)
        )

    @property
    def blocking_materials(self):
        """The materials standing between this order and being deletable.

        Rendered so the order page can say *why* the button is missing and
        point at the tab that fixes it — a disabled control with no
        explanation is the thing this avoids.
        """
        from inventory import services as inventory_service

        if self.status != "tentative":
            return []
        return inventory_service.list_materials_for_order(self.id)

    @property
    def can_rush(self):
        """Rush means "put this one on the bench first", so it only applies
        to work still on its way *to* the bench or on it.

        Narrower than `is_active`, which also covers `ready`: a finished
        piece waiting on its owner can't be hurried by the studio, and
        "rush, ready for pickup" describes nothing. Moving an order to
        `ready` therefore clears the flag rather than carrying it over.
        """
        return self.status == "confirmed"

    @property
    def can_cancel(self):
        """Anything that hasn't finished can be called off — except while
        an issued invoice is outstanding.

        Voiding an invoice is billing's decision and hard rule 11 keeps an
        issued one frozen, so cancelling must not do it as a side effect.
        The user voids first, then cancels.
        """
        return (self.is_active or self.status == "tentative") and not self.is_issued

    @property
    def subtotal(self):
        """Value of the line items, before tax.

        Computed rather than stored (this used to be an Order.price column)
        so it can't drift out of sync with the lines an invoice is actually
        built from — same reasoning as lifetime_value/amount_paid.
        """
        return sum(line.total for line in self.lines)

    @property
    def is_issued(self):
        """True once an invoice has gone out for this order — at which
        point its money is frozen and stops tracking live settings."""
        return self.invoice is not None and self.invoice.status != "draft"

    @property
    def amount_paid(self):
        return sum(payment.amount for payment in self.payments)

    # Everything below is the billing module's answer, not this file's.
    # `billing.services` is imported inside the properties rather than at
    # module scope because billing.models imports `db` from here — a
    # top-level import would be circular. See billing/__init__.py.

    @property
    def _amounts(self):
        from billing.services import invoicing

        from billing_adapter import billable_for

        return invoicing.amounts_for(
            billable_for(self),
            invoicing.profile_for(self.client.company_id, self.client.company.name).issuer,
            self.invoice,
        )

    @property
    def tax_lines(self):
        """Taxes on this order: frozen once invoiced, live before that."""
        return self._amounts.tax_lines

    @property
    def tax_total(self):
        return self._amounts.tax_total

    @property
    def total(self):
        """What's actually billed: line items plus tax.

        An issued invoice reports what it was issued for, so editing line
        items afterwards can't silently change a number the client has
        already been given.
        """
        return self._amounts.total

    @property
    def tax_status(self):
        """Why there's no tax, when there isn't — for showing a warning."""
        return self._amounts.tax_status

    @property
    def invoice_status(self):
        """The invoice's status *for display* — "paid" once payments cover
        it, which the module derives rather than storing."""
        from billing.services import invoicing

        if self.invoice is None:
            return None
        return invoicing.display_status(self.invoice, self._amounts)

    @property
    def balance_due(self):
        return self.total - self.amount_paid

    @property
    def is_settled(self):
        """True once payments cover the order. Float-tolerant: cents of
        rounding shouldn't leave an order looking eternally unpaid."""
        return self.balance_due < 0.005


class OrderLine(db.Model):
    """One billable line on an order — description, quantity, unit price.

    Orders carried a single `item` string and one `price` before this;
    `item` stays as the order's short name (timeline bar label, page
    title), while the lines are what an invoice is actually built from.
    """
    __tablename__ = "order_lines"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    description = db.Column(db.String(200), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    unit_price = db.Column(db.Float, nullable=False, default=0.0)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    order = db.relationship("Order", back_populates="lines")

    @property
    def total(self):
        return self.quantity * self.unit_price


class Payment(db.Model):
    """A single payment recorded against an order — deposit, balance, or
    anything in between. Deliberately generic (just an amount + a date):
    different clients/studios run different deposit schemes (e.g. Joe's is
    ~50% up front, order due balance at pickup), so nothing here assumes a
    fixed split or a fixed number of payments per order.

    `method` is what makes cash, e-transfer and Square reconcilable against
    one invoice: the invoice is the app's record either way, and the method
    only says how the money arrived. `reference` is the matching stub for
    that method — an e-transfer confirmation code, a Square payment id — so
    a line here can be traced back to a bank or Square statement.
    """
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    paid_date = db.Column(db.Date, nullable=False)
    method = db.Column(db.String(20), nullable=False, default="cash")
    reference = db.Column(db.String(120))

    order = db.relationship("Order", back_populates="payments")


# ---------------------------------------------------------------------------
# Migrations. db.create_all() adds missing *tables* but never missing
# columns, and this project deliberately has no Alembic setup — so the
# handful of columns added to existing tables after the fact are applied
# here by hand, at startup, before anything queries them. Every step is a
# no-op once applied, so this is safe to run on every boot (and on a fresh
# database, where create_all has already built the current schema).
# ---------------------------------------------------------------------------

# (table, column, DDL type/constraint) for columns added after that table
# first shipped. Appending here is the way to add another one.
#
# Columns belonging to a *module's* tables are not listed here — see
# billing/migrations.py and communications/migrations.py. The letterhead
# columns that used to sit on `companies` moved out with the billing
# module and are migrated there.
_ADDED_COLUMNS = [
    # Literal in the DDL rather than DEFAULT_TIMEZONE interpolated: this is a
    # record of what shipped, and it must not change if that constant does.
    ("companies", "timezone", "VARCHAR(60) NOT NULL DEFAULT 'America/Vancouver'"),
    ("clients", "street", "VARCHAR(200)"),
    ("clients", "city", "VARCHAR(120)"),
    ("clients", "province", "VARCHAR(2)"),
    ("clients", "postal_code", "VARCHAR(10)"),
    ("payments", "method", "VARCHAR(20) NOT NULL DEFAULT 'cash'"),
    ("payments", "reference", "VARCHAR(120)"),
    ("orders", "order_type_id", "INTEGER"),
    ("orders", "pickup_date", "DATE"),
    ("companies", "order_columns", "TEXT"),
    ("source_options", "is_other", "BOOLEAN NOT NULL DEFAULT 0"),
    ("clients", "other_source_detail", "VARCHAR(200)"),
    ("clients", "notes", "TEXT"),
    ("companies", "analytics_layout", "TEXT"),
    # `users` already shipped, so its new column needs an entry here — and
    # this one belongs to the root, not to ai/, because User is a host model
    # (hard rule 12 sends module columns to the module's own file).
    ("users", "signature", "TEXT"),
    # Rush stopped being a status and became a flag that sits on top of one —
    # an order could never be both "rush" and "ready for pickup" while the two
    # shared an enum. _migrate_order_statuses() below moves the existing rows.
    ("orders", "is_rush", "BOOLEAN NOT NULL DEFAULT 0"),
    # Every client already on file was on the roster before hiding existed,
    # so the default has to be 0 — a migration records what shipped.
    ("clients", "is_hidden", "BOOLEAN NOT NULL DEFAULT 0"),
    # Every company that existed before the platform-admin area was a working
    # tenant, so they all start active. The `users` columns that landed in the
    # same change aren't here — they need the table rebuilt, not extended;
    # see _migrate_users_to_email() below.
    ("companies", "is_active", "BOOLEAN NOT NULL DEFAULT 1"),
]

# Free-text address columns replaced by street/city/province/postal_code.
# `companies` isn't here any more: its address left with the billing
# module, which migrates whatever shape it finds (see
# billing/migrations.py). This runs first, so clients are split before
# anything else reads a province off them.
_SPLIT_ADDRESS_TABLES = ("clients",)


def run_migrations() -> None:
    inspector = sa.inspect(db.engine)
    tables = set(inspector.get_table_names())
    # Snapshot every column up front: reflecting again after an ALTER would
    # hit the inspector's cache anyway, and nothing below depends on a
    # change made by an earlier step.
    existing = {t: {c["name"] for c in inspector.get_columns(t)} for t in tables}

    for table, column, ddl in _ADDED_COLUMNS:
        if table in tables and column not in existing[table]:
            db.session.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
    db.session.commit()

    # Runs after the ALTER loop above, which is what guarantees `signature`
    # exists on a database old enough to predate it — the rebuild copies that
    # column across by name.
    if "username" in existing.get("users", ()):
        _migrate_users_to_email()

    # Runs after that one, and checks the live schema rather than the
    # snapshot above, because the rebuild it may have just done already
    # produces the right shape — this is only for databases that went
    # through the *first* version of it, which wrote company_id NOT NULL.
    if "users" in tables and not _users_company_id_is_nullable():
        _migrate_users_company_nullable()

    if "price" in existing.get("orders", ()):
        _migrate_order_price_to_lines()

    if "orders" in tables:
        _migrate_order_statuses()

    for table in _SPLIT_ADDRESS_TABLES:
        if "address" in existing.get(table, ()):
            _migrate_free_text_address(table)


# The current shape of `users`, as one string, because **two** migrations
# build this table and a version skew between them is a boot failure rather
# than a wrong number. Mirrors what db.create_all() emits for `User`.
#
# Unlike _ADDED_COLUMNS — where each entry is a frozen record of what
# shipped — this one deliberately tracks the model: both callers below are
# "make the table look like it does today", and a rebuild that recreated a
# historical shape would just need rebuilding again.
_USERS_TABLE_DDL = (
    "CREATE TABLE users_new ("
    " id INTEGER NOT NULL,"
    # Nullable: platform staff belong to no company (see `User`).
    " company_id INTEGER,"
    " email VARCHAR(255) NOT NULL,"
    " full_name VARCHAR(120),"
    " password_hash VARCHAR(255) NOT NULL,"
    " signature TEXT,"
    " is_active BOOLEAN NOT NULL DEFAULT 1,"
    " is_platform_admin BOOLEAN NOT NULL DEFAULT 0,"
    " PRIMARY KEY (id),"
    " FOREIGN KEY(company_id) REFERENCES companies (id))"
)


def _swap_in_rebuilt_users() -> None:
    """Replace `users` with the freshly built `users_new`.

    The tail both rebuilds share. SQLite re-parses the whole schema on
    ALTER TABLE RENAME, and communications' `user_id` foreign key is
    briefly dangling between the DROP and the rename;
    `legacy_alter_table` makes the rename a plain schema-text change with
    no re-parse and no reference rewriting, which is the recipe from
    SQLite's own ALTER TABLE documentation.

    The unique index goes on last because dropping the old table dropped
    it. Its name and shape match what SQLAlchemy generates for a column
    declared `index=True, unique=True`, so a rebuilt database and a fresh
    `create_all()` one end up identical.
    """
    db.session.execute(sa.text("DROP TABLE users"))
    db.session.execute(sa.text("PRAGMA legacy_alter_table=ON"))
    db.session.execute(sa.text("ALTER TABLE users_new RENAME TO users"))
    db.session.execute(sa.text("PRAGMA legacy_alter_table=OFF"))
    db.session.execute(sa.text("CREATE UNIQUE INDEX ix_users_email ON users (email)"))
    db.session.commit()


def _users_company_id_is_nullable() -> bool:
    """Read the live schema, not a reflected snapshot.

    `PRAGMA table_info` rather than the inspector because this runs
    *after* `_migrate_users_to_email()` may have rebuilt the table in the
    same pass, and the pragma always reads what's actually there —
    column 3 of each row is `notnull`.
    """
    rows = db.session.execute(sa.text("PRAGMA table_info(users)")).all()
    for row in rows:
        if row[1] == "company_id":
            return row[3] == 0
    return True


def _migrate_users_company_nullable() -> None:
    """Relax `users.company_id` to nullable.

    A second rebuild of the same table, and an avoidable one: the first
    version of the platform-admin area shipped with `company_id INTEGER
    NOT NULL`, because platform staff still belonged to a company then.
    Making the *model* nullable doesn't relax a constraint SQLite has
    already written into the table, and SQLite has no ALTER for it — so
    any database created or migrated in that window needs this, and
    without it `ensure_platform_admin()` fails on insert and takes the
    whole boot down with it.

    Nothing about the rows changes; only the constraint does.
    """
    db.session.execute(sa.text(_USERS_TABLE_DDL))
    db.session.execute(sa.text(
        "INSERT INTO users_new (id, company_id, email, full_name,"
        " password_hash, signature, is_active, is_platform_admin)"
        " SELECT id, company_id, email, full_name, password_hash, signature,"
        " is_active, is_platform_admin FROM users"
    ))
    _swap_in_rebuilt_users()


# Where a username that isn't already an address is parked when the login
# identity moves to email. `.invalid` is reserved by RFC 2606 and can never
# resolve, so a backfilled address is incapable of being someone's real one
# — it reads as a placeholder because it is one, and the platform admin is
# expected to replace it from /admin.
_BACKFILL_EMAIL_DOMAIN = "example.invalid"


def _migrate_users_to_email() -> None:
    """Rebuild `users` with email as the login identity.

    A rebuild rather than an ALTER because SQLite can do neither half of
    what's needed: it cannot add a UNIQUE column, and it cannot drop the
    inline UNIQUE that `username` carried. Copying into a fresh table is
    the documented way round both — and it's what actually *removes* the
    global uniqueness rather than merely leaving it unused, which matters,
    because that constraint is the thing stopping two studios from each
    having an "admin".

    `id` values are preserved, so communications' `users.id` foreign key
    still points at the same people afterwards.

    Two decisions are made here that cannot be made later:

    * **The email backfill.** A username that already looks like an address
      becomes that address; anything else is parked at
      `<username>@example.invalid`. Should two usernames fold onto one
      address (`Admin` and `admin` — possible, since SQLite's UNIQUE was
      case-sensitive), the row id is appended rather than failing the boot.
      An ugly address somebody can edit beats a deployment that won't start.
    Everyone migrated stays a **tenant user** of the company they were
    already in. Nobody is promoted to platform admin here, deliberately: a
    platform admin has no company (see `User`), and silently detaching the
    studio's only login from its studio would be a strange thing for a
    migration to do. `ensure_platform_admin()` creates the staff account
    separately, alongside rather than instead of this one.
    """
    rows = db.session.execute(sa.text(
        "SELECT id, company_id, username, password_hash, signature "
        "FROM users ORDER BY id"
    )).all()

    taken: set[str] = set()
    migrated = []
    for row in rows:
        username = (row.username or "").strip()
        email = username.lower()
        if "@" not in email:
            email = f"{email}@{_BACKFILL_EMAIL_DOMAIN}"
        if email in taken:
            local, _, domain = email.partition("@")
            email = f"{local}+{row.id}@{domain}"
        taken.add(email)
        migrated.append({
            "id": row.id,
            "company_id": row.company_id,
            "email": email,
            # The old username becomes the display name, so nobody's account
            # stops being recognisable on the day this runs.
            "full_name": username or None,
            "password_hash": row.password_hash,
            "signature": row.signature,
        })

    db.session.execute(sa.text(_USERS_TABLE_DDL))
    for values in migrated:
        db.session.execute(sa.text(
            "INSERT INTO users_new (id, company_id, email, full_name,"
            " password_hash, signature, is_active, is_platform_admin)"
            " VALUES (:id, :company_id, :email, :full_name, :password_hash,"
            " :signature, 1, 0)"
        ), values)

    _swap_in_rebuilt_users()


# "…\nCity, PROV  H1V 1M6" — the shape the old free-text addresses were
# written in. Anything that doesn't match is left whole in `street`.
_ADDRESS_TAIL = re.compile(
    r"^(?P<city>[^,]+),\s*(?P<province>[A-Za-z]{2})\s+"
    r"(?P<postal>[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d)\s*$"
)


def _migrate_free_text_address(table: str) -> None:
    """Move a free-text `address` column into street/city/province/postal.

    Best effort: if the last line looks like "City, PROV  Postal" it's
    split out properly, otherwise the whole address lands in `street` and
    reads visibly wrong in the UI until someone re-enters it. Guessing
    harder would risk quietly filing a client in the wrong province, which
    now decides what tax they're charged.
    """
    rows = db.session.execute(
        sa.text(f"SELECT id, address FROM {table} "  # noqa: S608 — table from a fixed tuple
                "WHERE address IS NOT NULL AND address != ''")
    ).all()
    for row_id, address in rows:
        lines = [line.strip() for line in address.splitlines() if line.strip()]
        parsed = _ADDRESS_TAIL.match(lines[-1]) if lines else None
        if parsed and len(lines) > 1:
            values = {
                "street": ", ".join(lines[:-1]),
                "city": parsed["city"].strip(),
                "province": parsed["province"].upper(),
                "postal": parsed["postal"].upper(),
            }
        else:
            values = {"street": address.replace("\n", ", "), "city": None,
                      "province": None, "postal": None}
        values["id"] = row_id
        db.session.execute(
            sa.text(f"UPDATE {table} SET street = :street, city = :city, "  # noqa: S608
                    "province = :province, postal_code = :postal "
                    "WHERE id = :id AND (street IS NULL OR street = '')"),
            values,
        )
    db.session.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN address"))  # noqa: S608
    db.session.commit()


def _migrate_order_statuses() -> None:
    """Move the old four-value status vocabulary onto the lifecycle.

    Two changes land together because they touch the same column:

    * `rush` was never a lifecycle stage, it was urgency wearing one's
      clothes — so an order could not be both rush and ready for pickup.
      It becomes `is_rush` on top of whatever stage the order is actually
      at, and every former rush order lands at `confirmed`.
    * `in_progress` was renamed `confirmed`, because with `tentative` in
      front of it the stage now starts when the deposit lands, not when
      the work does. "In progress" survives as a *display* label that
      `Order.display_status` derives from the start date.

    Idempotent by construction: it only ever reads rows still holding a
    retired value, so a second boot matches nothing.
    """
    retired = db.session.execute(
        sa.text("SELECT COUNT(*) FROM orders WHERE status IN ('rush', 'in_progress')")
    ).scalar()
    if not retired:
        return

    db.session.execute(
        sa.text("UPDATE orders SET status = 'confirmed', is_rush = 1 WHERE status = 'rush'")
    )
    db.session.execute(
        sa.text("UPDATE orders SET status = 'confirmed' WHERE status = 'in_progress'")
    )
    db.session.commit()


def _migrate_order_price_to_lines() -> None:
    """Turn each legacy Order.price into a single OrderLine.

    Dropping the column is part of the migration, not cleanup: it's NOT
    NULL with no default and nothing writes it any more, so leaving it in
    place would make every new order fail to insert.
    """
    legacy = db.session.execute(sa.text("SELECT id, item, price FROM orders")).all()
    already_have_lines = {
        row[0] for row in db.session.execute(sa.text("SELECT DISTINCT order_id FROM order_lines"))
    }
    for order_id, item, price in legacy:
        if order_id in already_have_lines:
            continue
        db.session.add(OrderLine(
            order_id=order_id,
            description=item,
            quantity=1,
            unit_price=price or 0.0,
            sort_order=0,
        ))
    db.session.execute(sa.text("ALTER TABLE orders DROP COLUMN price"))
    db.session.commit()


# ---------------------------------------------------------------------------
# Bootstrap data — the minimum an empty database needs to be a working
# install: one company, one admin user, and the two option lists the app's
# own forms read from. **No sample clients, orders or invoices.** Those live
# in `sample_data.py`, which nothing here imports — they're loaded on purpose
# by `scripts/seed_sample_data.py`, so a production deployment starts empty.
#
# Fixed reference data the app can't work without — province tax rates
# (billing/tax.py), the inventory unit catalog (inventory/config.py) — isn't
# seeded at all: it's code constants, present in every deployment. Anything
# per-company that's fixed rather than configurable is created lazily on
# first use (the billing letterhead via `profile_for()`, the "Each" unit via
# `_ensure_default_unit()`), which covers a company created after this
# function ever ran.
# ---------------------------------------------------------------------------

# Default "how did you hear about us" options, matching the checkboxes on
# the bymonsieur.ca contact form. Editable per-company from /settings once
# seeded — this list is only ever used to seed a brand new company.
_DEFAULT_SOURCE_OPTIONS = [
    "Google Search",
    "Word of Mouth",
    "Craft Market / Open Studio",
    "Instagram",
    "Facebook",
    "LinkedIn",
    "Other",
]

# Default order types. Unlike SourceOption these aren't tied to anything on
# the bymonsieur.ca site — they're just a starting set so the feature isn't
# empty on first run. Fully editable (add/hide/delete) per-company from
# /settings; a company that clears all of them loses the dropdown entirely
# (see new_order()/order_page() in app.py), which is the intended "opt out"
# path for a studio that doesn't categorize orders this way.
_DEFAULT_ORDER_TYPES = [
    "Custom Order",
    "White Label",
    "Consulting/Sampling",
]


def create_company(
    name: str,
    admin_email: str,
    admin_password: str,
    *,
    timezone: str = DEFAULT_TIMEZONE,
    admin_full_name: str | None = None,
) -> tuple["Company", "User"]:
    """Provision a tenant: the company, its billing letterhead, its first
    user and the two option lists the app's own forms read from.

    The user created here is a **tenant user** — a studio's own login, with
    no platform rights. Platform staff are a different kind of account
    entirely and are created by `ensure_platform_admin()`; see `User`.

    **The single provisioning path**, used both by `seed_if_empty()` for the
    first company on an empty database and by the platform admin area for
    every one after it. That's the point of it being a function rather than
    inline code: the tenth studio gets byte-for-byte what the first one got,
    and a starter list added here can't reach one and miss the other.

    What it deliberately does *not* create is anything fixed rather than
    configurable — province tax rates and the inventory unit catalog are
    code constants, and the per-company rows around them are created lazily
    on first use, which already covers a company created here.

    Flushes but does not commit, so a caller can add to the same
    transaction; both current callers commit immediately after.
    """
    from billing.services import invoicing

    company = Company(name=name.strip(), timezone=timezone)
    db.session.add(company)
    db.session.flush()  # assigns company.id

    # The letterhead belongs to the billing module now, so it's created
    # through that module's API rather than as columns on Company. Empty
    # apart from the name: the address and tax registrations are things
    # only the studio can fill in, from /settings, and a placeholder that
    # looks plausible would be worse than a blank that prompts for one.
    # `update_profile` supplies the "INV" number prefix.
    invoicing.update_profile(company.id, display_name=company.name)

    admin = User(
        company_id=company.id,
        email=normalise_email(admin_email),
        full_name=(admin_full_name or "").strip() or None,
    )
    admin.set_password(admin_password)
    db.session.add(admin)

    for i, label in enumerate(_DEFAULT_SOURCE_OPTIONS):
        db.session.add(SourceOption(company_id=company.id, label=label, sort_order=i))

    for i, label in enumerate(_DEFAULT_ORDER_TYPES):
        db.session.add(OrderType(company_id=company.id, label=label, sort_order=i))

    db.session.flush()
    return company, admin


def seed_if_empty(
    admin_password: str = "changeme",
    admin_email: str = "admin@example.invalid",
) -> None:
    """Create the one company an empty database needs, and nothing else.

    Bootstrap only — a company, its admin user, and the two option lists the
    app's own forms read from. **No clients, orders or invoices**: a fresh
    deployment is meant to be genuinely empty, so the first real order
    entered is order #1. The demo dataset lives in `sample_data.py` and is
    loaded on purpose by running `scripts/seed_sample_data.py`.

    The company's name is a starting value only — it's editable from
    /settings, and this never overwrites it. The admin address is the same
    kind of starting value, and the same `.invalid` placeholder the username
    migration uses: it has to be *some* address now that email is the login
    identity, and one that can never be a real mailbox is the honest choice.
    Override it with `ADMIN_EMAIL`, or change it from /admin after signing in.

    That user is a **tenant user** — the studio's own login, with no access
    to /admin. The platform staff account is a separate thing entirely and
    is created by `ensure_platform_admin()`, which app.py calls right after
    this one.

    Runs on every boot and returns immediately once a company exists, so
    it's never a reset mechanism.
    """
    if Company.query.count() > 0:
        return

    create_company(
        "By Monsieur",
        admin_email,
        admin_password,
        admin_full_name="Studio Admin",
    )
    db.session.commit()


def ensure_platform_admin(
    email: str = "platform@example.invalid",
    password: str = "changeme",
) -> "User | None":
    """Make sure the installation has somebody who can reach /admin.

    Separate from `seed_if_empty()` on purpose, and guarded on a different
    question. Seeding asks "is this database empty?"; this asks "is there
    any platform staff?" — and those diverge in exactly the case that
    matters, an existing single-tenant database being migrated, which has a
    company already and so returns early from seeding while having nobody
    who can administer anything.

    Creates a user with **no company**, which is what platform staff are
    (see `User`). Returns the new account, or None when one already
    existed — so it's safe on every boot, and never a way to reset a
    password somebody has since changed.

    The `.invalid` default is the same reasoning as everywhere else here:
    the address has to be *something* before anyone has said what, and one
    that can never be a real mailbox is the honest placeholder. Override
    with `PLATFORM_ADMIN_EMAIL` / `PLATFORM_ADMIN_PASSWORD`.

    **It asks whether a *usable* platform admin exists, not whether the flag
    appears anywhere**, and the difference is a lockout. An earlier version
    of this feature put the flag on a user who still belonged to a company;
    deactivate that company and the account can no longer sign in
    (`load_user` in app.py drops it), leaving an installation with a
    platform admin on paper and nobody who can reach /admin. Counting only
    company-less, active accounts is what makes this function able to
    rescue that database instead of stepping politely aside from it.
    """
    # Normalise away the arrangement the model no longer allows: a user with
    # both a company and the flag. They keep their studio and lose the flag,
    # which is the half that was wrong — see `User`. Done here rather than in
    # run_migrations() because it's a repair for a state only a previous
    # version of *this* function could produce, and it belongs next to the
    # reasoning for it.
    stale = User.query.filter(
        User.is_platform_admin.is_(True), User.company_id.isnot(None),
    ).all()
    for user in stale:
        user.is_platform_admin = False
    if stale:
        db.session.commit()

    usable = User.query.filter(
        User.is_platform_admin.is_(True),
        User.company_id.is_(None),
        User.is_active.is_(True),
    ).count()
    if usable > 0:
        return None

    # An address already taken by a tenant user would fail the unique index
    # and take the whole boot down with it. Stepping aside is better than
    # refusing to start: the staff account still gets created, under a name
    # that says what happened, and /admin can rename it.
    address = normalise_email(email)
    if User.query.filter_by(email=address).first() is not None:
        local, _, domain = address.partition("@")
        address = f"{local}+platform@{domain}"

    admin = User(
        company_id=None,
        email=address,
        full_name="Platform Admin",
        is_platform_admin=True,
    )
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    return admin
