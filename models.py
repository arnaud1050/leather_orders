"""
SQLAlchemy models + first-boot bootstrap.

Company is the tenant boundary: everything else (users, clients, and
transitively orders/documents) hangs off a company_id. Today only one
company is created on first boot, but scoping queries by company_id from
the start means adding a second tenant later is additive, not a rewrite.

`seed_if_empty()` below creates *only* that company, its admin user and the
per-company option lists — no sample clients, orders or invoices, so a
production deployment starts empty. The demo dataset lives in
`sample_data.py`, loaded on demand by `scripts/seed_sample_data.py`.
"""

import re

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
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    # How this person signs off an email. Per user rather than per company
    # or per mailbox, because a signature is written by a person: two people
    # sharing one studio@ address each want their own, and a company-level
    # one would be wrong the day a second user exists. Used by the compose
    # box and appended to AI-drafted replies — see `signature_block`.
    signature = db.Column(db.Text)

    company = db.relationship("Company", back_populates="users")

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
        return sum(order.total for order in self.orders)


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

    if "price" in existing.get("orders", ()):
        _migrate_order_price_to_lines()

    for table in _SPLIT_ADDRESS_TABLES:
        if "address" in existing.get(table, ()):
            _migrate_free_text_address(table)


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


def seed_if_empty(admin_password: str = "changeme") -> None:
    """Create the one company an empty database needs, and nothing else.

    Bootstrap only — a company, its admin user, and the two option lists the
    app's own forms read from. **No clients, orders or invoices**: a fresh
    deployment is meant to be genuinely empty, so the first real order
    entered is order #1. The demo dataset lives in `sample_data.py` and is
    loaded on purpose by running `scripts/seed_sample_data.py`.

    The company's name is a starting value only — it's editable from
    /settings, and this never overwrites it.

    Runs on every boot and returns immediately once a company exists, so
    it's never a reset mechanism.
    """
    if Company.query.count() > 0:
        return

    from billing.services import invoicing

    company = Company(name="By Monsieur")
    db.session.add(company)
    db.session.flush()  # assigns company.id

    # The letterhead belongs to the billing module now, so it's created
    # through that module's API rather than as columns on Company. Empty
    # apart from the name: the address and tax registrations are things
    # only the studio can fill in, from /settings, and a placeholder that
    # looks plausible would be worse than a blank that prompts for one.
    # `update_profile` supplies the "INV" number prefix.
    invoicing.update_profile(company.id, display_name=company.name)

    admin = User(company_id=company.id, username="admin")
    admin.set_password(admin_password)
    db.session.add(admin)

    for i, label in enumerate(_DEFAULT_SOURCE_OPTIONS):
        db.session.add(SourceOption(company_id=company.id, label=label, sort_order=i))

    for i, label in enumerate(_DEFAULT_ORDER_TYPES):
        db.session.add(OrderType(company_id=company.id, label=label, sort_order=i))

    db.session.commit()
