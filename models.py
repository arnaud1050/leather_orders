"""
SQLAlchemy models + seed data.

Company is the tenant boundary: everything else (users, clients, and
transitively orders/documents) hangs off a company_id. Today only one
company is seeded ("By Monsieur"), but scoping queries by company_id from
the start means adding a second tenant later is additive, not a rewrite.
"""

import re
from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.ext.hybrid import hybrid_property
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


def format_address(street, city, province, postal_code) -> str | None:
    """Address as it prints: street, then "City, PROV  Postal".

    Two spaces before the postal code is the Canada Post convention.
    Returns None when nothing is filled in, so callers can skip the block
    entirely rather than printing an empty line. Shared by Company and
    Client, which store the same four parts.
    """
    locality = ", ".join(part for part in (city, province) if part)
    if postal_code:
        locality = f"{locality}  {postal_code}".strip()
    return "\n".join(line for line in (street, locality) if line) or None


class Company(db.Model):
    __tablename__ = "companies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    # Leading segment of every invoice number this company issues
    # ("BM" -> BM-2026-0001). Per-company because the number sequence is
    # per-company too — see next_invoice_number() below.
    invoice_prefix = db.Column(db.String(10), nullable=False, default="INV")

    # What an invoice has to say about who issued it. Named Canadian
    # fields rather than a generic list: they need to be labelled
    # correctly ("GST/HST", "QST", "NEQ"), and a second country would want
    # its own labels anyway. Copied onto each invoice when it's issued —
    # see Invoice.issuer.
    street = db.Column(db.String(200))
    city = db.Column(db.String(120))
    province = db.Column(db.String(2))  # two-letter code; see PROVINCES in app.py
    postal_code = db.Column(db.String(10))
    gst_number = db.Column(db.String(40))
    # BC/Saskatchewan PST or Manitoba RST — one field because a seller is
    # realistically registered in at most one of them. If that ever stops
    # being true, this is the point to switch registrations over to a
    # label+value list rather than adding pst_number_2.
    pst_number = db.Column(db.String(40))
    qst_number = db.Column(db.String(40))
    neq = db.Column(db.String(40))
    # Free text printed near the total: where to send an e-transfer, who
    # to make a cheque out to. The reason this matters here and not on a
    # Square-hosted invoice is that cash and e-transfer have no payment
    # page to send anyone to.
    payment_instructions = db.Column(db.Text)

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
    invoices = db.relationship("Invoice", back_populates="company")

    @property
    def formatted_address(self) -> str | None:
        return format_address(self.street, self.city, self.province, self.postal_code)


class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    company = db.relationship("Company", back_populates="users")

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
    status = db.Column(db.String(20), nullable=False)
    notes = db.Column(db.Text)
    # Optional — a company with no OrderTypes defined never shows the
    # dropdown at all (see new_order()/order_page() in app.py), so this
    # stays nullable rather than needing a fallback "Uncategorized" row.
    order_type_id = db.Column(db.Integer, db.ForeignKey("order_types.id"))

    client = db.relationship("Client", back_populates="orders")
    order_type = db.relationship("OrderType", back_populates="orders")
    documents = db.relationship("Document", back_populates="order")
    lines = db.relationship(
        "OrderLine", back_populates="order", cascade="all, delete-orphan",
        order_by="OrderLine.sort_order",
    )
    payments = db.relationship("Payment", back_populates="order", cascade="all, delete-orphan")
    invoice = db.relationship(
        "Invoice", back_populates="order", uselist=False,
        cascade="all, delete-orphan",
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
    def tax_lines(self) -> list[TaxLine]:
        """Taxes on this order: frozen once invoiced, live before that."""
        if self.is_issued:
            return self.invoice.frozen_tax_lines
        return taxes_for(self.client.province, self.client.company, self.subtotal)

    @property
    def tax_total(self):
        return sum(line.amount for line in self.tax_lines)

    @property
    def total(self):
        """What's actually billed: line items plus tax.

        An issued invoice reports what it was issued for, so editing line
        items afterwards can't silently change a number the client has
        already been given.
        """
        if self.is_issued:
            return self.invoice.subtotal + self.tax_total
        return self.subtotal + self.tax_total

    @property
    def tax_status(self):
        """Why there's no tax, when there isn't — for showing a warning.

        "none" means tax was calculated normally (possibly to zero).
        """
        if self.tax_lines:
            return "ok"
        if not (self.client.province or "").strip():
            return "no_client_province"
        if self.client.province not in PROVINCE_TAXES:
            return "unknown_province"
        return "not_registered"

    @property
    def amount_paid(self):
        return sum(payment.amount for payment in self.payments)

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
# Sales tax
#
# !! VERIFY THESE RATES BEFORE ISSUING A REAL INVOICE !!
# They are the author's best understanding and are NOT tax advice. Rates
# do change — Nova Scotia's HST in particular was reduced recently, so
# confirm it specifically. This table is the single place to correct them.
#
# Two rules decide what actually gets charged:
#   1. The *client's* province picks the row (destination-based, which is
#      how place-of-supply works for goods shipped to a customer). A client
#      with no province on file is charged nothing — see taxes_for.
#   2. A tax is only charged if the company holds the matching
#      registration. A studio under the small-supplier threshold has no
#      gst_number and so charges no GST; one that never registered in BC
#      charges no BC PST. That falls out of `registration_field` rather
#      than needing a separate "do we charge tax" switch.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TaxRule:
    label: str
    rate: float
    registration_field: str  # Company attribute that must be set to charge it


_GST = TaxRule("GST", 0.05, "gst_number")

PROVINCE_TAXES: dict[str, tuple[TaxRule, ...]] = {
    "AB": (_GST,),
    "BC": (_GST, TaxRule("PST", 0.07, "pst_number")),
    "MB": (_GST, TaxRule("RST", 0.07, "pst_number")),
    # HST is collected under the federal GST/HST registration, so it hangs
    # off gst_number rather than a provincial one.
    "NB": (TaxRule("HST", 0.15, "gst_number"),),
    "NL": (TaxRule("HST", 0.15, "gst_number"),),
    "NS": (TaxRule("HST", 0.14, "gst_number"),),  # reduced recently — confirm
    "NT": (_GST,),
    "NU": (_GST,),
    "ON": (TaxRule("HST", 0.13, "gst_number"),),
    "PE": (TaxRule("HST", 0.15, "gst_number"),),
    "QC": (_GST, TaxRule("QST", 0.09975, "qst_number")),
    "SK": (_GST, TaxRule("PST", 0.06, "pst_number")),
    "YT": (_GST,),
}


@dataclass(frozen=True)
class TaxLine:
    """One tax as it appears on an invoice: what it's called, the rate
    applied, and the money it comes to."""

    label: str
    rate: float
    amount: float

    @property
    def rate_percent(self) -> str:
        """Rate for display, without trailing zeros (5%, 9.975%)."""
        return f"{self.rate * 100:.3f}".rstrip("0").rstrip(".")


def taxes_for(province: str | None, company: "Company", subtotal: float) -> list[TaxLine]:
    """Taxes owed on `subtotal` for a client in `province`.

    Empty when the province is unknown or unrecognised — better to charge
    nothing visibly than to guess a rate. Callers should surface that to
    the user rather than treating it as "no tax applies" (see
    Order.tax_status).
    """
    rules = PROVINCE_TAXES.get(province or "", ())
    return [
        TaxLine(rule.label, rule.rate, round(subtotal * rule.rate, 2))
        for rule in rules
        if getattr(company, rule.registration_field, None)
    ]


@dataclass(frozen=True)
class IssuerDetails:
    """Who issued an invoice, as it should print on the document."""

    name: str
    address: str | None = None
    gst_number: str | None = None
    pst_number: str | None = None
    qst_number: str | None = None
    neq: str | None = None
    payment_instructions: str | None = None

    @classmethod
    def from_company(cls, company: "Company") -> "IssuerDetails":
        # address is the formatted block, not the parts: a snapshot only
        # has to reproduce what was printed, so freezing one string beats
        # mirroring four columns onto every invoice.
        return cls(
            name=company.name,
            address=company.formatted_address,
            gst_number=company.gst_number,
            pst_number=company.pst_number,
            qst_number=company.qst_number,
            neq=company.neq,
            payment_instructions=company.payment_instructions,
        )

    @property
    def registrations(self) -> list[tuple[str, str]]:
        """(label, number) pairs to print, skipping any that are unset.

        Tax registrations first, NEQ last — it identifies the enterprise,
        it isn't a tax account.
        """
        pairs = [
            ("GST/HST", self.gst_number),
            ("PST/RST", self.pst_number),
            ("QST", self.qst_number),
            ("NEQ", self.neq),
        ]
        return [(label, value) for label, value in pairs if value]


class Invoice(db.Model):
    """The billing record for an order, and the owner of its number.

    Numbering is the app's job, not the payment processor's: one sequence
    per company (see next_invoice_number) means a cash sale and a card sale
    get numbers from the same run, which is the whole point of being able
    to reconcile them. `status` only tracks the states the app can't work
    out for itself — whether the invoice has actually been sent, and
    whether it's been voided. Paid-ness is derived from the order's
    payments instead of being a fourth stored state that could disagree
    with them (see display_status).
    """
    __tablename__ = "invoices"
    __table_args__ = (
        db.UniqueConstraint("company_id", "number", name="uq_invoice_company_number"),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, unique=True)
    number = db.Column(db.String(40), nullable=False)
    issued_date = db.Column(db.Date, nullable=False)
    due_date = db.Column(db.Date)
    status = db.Column(db.String(20), nullable=False, default="draft")
    notes = db.Column(db.Text)

    # Frozen copy of the company's details, written when the invoice stops
    # being a draft (freeze_issuer). Reprinting an invoice has to match
    # what the client actually received, even if the studio has since
    # moved or re-registered — so these can't be read live off Company.
    issuer_name = db.Column(db.String(120))
    issuer_address = db.Column(db.Text)
    issuer_gst_number = db.Column(db.String(40))
    issuer_pst_number = db.Column(db.String(40))
    issuer_qst_number = db.Column(db.String(40))
    issuer_neq = db.Column(db.String(40))
    issuer_payment_instructions = db.Column(db.Text)
    # The money, frozen at the same moment as the issuer details. Doubles
    # as the "has this been frozen" marker (see is_frozen) — an invoice
    # issued before tax existed freezes with zero tax rows, which is
    # exactly what its client received.
    issued_subtotal = db.Column(db.Float)

    company = db.relationship("Company", back_populates="invoices")
    order = db.relationship("Order", back_populates="invoice")
    tax_rows = db.relationship(
        "InvoiceTaxLine", back_populates="invoice", cascade="all, delete-orphan",
        order_by="InvoiceTaxLine.sort_order",
    )

    @property
    def is_frozen(self) -> bool:
        return self.issued_subtotal is not None

    @property
    def subtotal(self) -> float:
        """Pre-tax value: what it was issued for, or live while a draft."""
        return self.issued_subtotal if self.is_frozen else self.order.subtotal

    @property
    def frozen_tax_lines(self) -> list[TaxLine]:
        return [TaxLine(row.label, row.rate, row.amount) for row in self.tax_rows]

    @property
    def issuer(self) -> IssuerDetails:
        """Company details as this invoice should print them.

        A draft hasn't been issued to anyone yet, so it tracks whatever
        settings say today — fix a typo in the GST number and every draft
        picks it up. Anything past draft shows the frozen copy instead.
        """
        if self.status == "draft" or self.issuer_name is None:
            return IssuerDetails.from_company(self.company)
        return IssuerDetails(
            name=self.issuer_name,
            address=self.issuer_address,
            gst_number=self.issuer_gst_number,
            pst_number=self.issuer_pst_number,
            qst_number=self.issuer_qst_number,
            neq=self.issuer_neq,
            payment_instructions=self.issuer_payment_instructions,
        )

    def freeze(self) -> None:
        """Freeze everything the client will see: who issued it, and the
        money. Call on the draft -> issued transition only."""
        self.freeze_issuer()
        self.issued_subtotal = self.order.subtotal
        self.tax_rows = [
            InvoiceTaxLine(label=line.label, rate=line.rate, amount=line.amount, sort_order=i)
            for i, line in enumerate(
                taxes_for(self.order.client.province, self.company, self.order.subtotal)
            )
        ]

    def freeze_issuer(self) -> None:
        """Copy today's company details onto this invoice.

        Call on the draft -> issued transition only. Re-running it on an
        already-issued invoice would rewrite history, which is the exact
        thing the snapshot exists to prevent.
        """
        details = IssuerDetails.from_company(self.company)
        self.issuer_name = details.name
        self.issuer_address = details.address
        self.issuer_gst_number = details.gst_number
        self.issuer_pst_number = details.pst_number
        self.issuer_qst_number = details.qst_number
        self.issuer_neq = details.neq
        self.issuer_payment_instructions = details.payment_instructions

    @property
    def display_status(self):
        """Status key for display: stored state, except that a fully-paid
        invoice reports "paid" without anyone having to remember to set it."""
        if self.status == "void":
            return "void"
        if self.order.is_settled and self.order.total > 0:
            return "paid"
        return self.status

    @property
    def is_outstanding(self):
        """Issued, not voided, and still owed money."""
        return self.status != "void" and not self.order.is_settled


class InvoiceTaxLine(db.Model):
    """One tax line frozen onto an issued invoice.

    A real table rather than JSON so tax collected can be summed straight
    out of the database — which is what a GST/QST remittance needs.
    """
    __tablename__ = "invoice_tax_lines"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"), nullable=False)
    label = db.Column(db.String(20), nullable=False)
    rate = db.Column(db.Float, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    invoice = db.relationship("Invoice", back_populates="tax_rows")


def next_invoice_number(company: Company, today: date | None = None) -> str:
    """Next number in `PREFIX-YEAR-0001` form for this company and year.

    Derived from the highest existing number rather than a count, so voided
    or deleted invoices don't cause a later invoice to reuse a number. The
    unique constraint on (company_id, number) is the real guard — two
    simultaneous requests would collide there rather than silently issuing
    the same number twice.
    """
    today = today or date.today()
    prefix = f"{company.invoice_prefix}-{today.year}-"
    last = (
        Invoice.query.filter(
            Invoice.company_id == company.id,
            Invoice.number.like(f"{prefix}%"),
        )
        .order_by(Invoice.number.desc())  # zero-padded, so string order == numeric order
        .first()
    )
    sequence = int(last.number.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{sequence:04d}"


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    label = db.Column(db.String(80), nullable=False)
    filename = db.Column(db.String(200), nullable=False)

    order = db.relationship("Order", back_populates="documents")


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
_ADDED_COLUMNS = [
    ("companies", "invoice_prefix", "VARCHAR(10) NOT NULL DEFAULT 'INV'"),
    ("companies", "street", "VARCHAR(200)"),
    ("companies", "city", "VARCHAR(120)"),
    ("companies", "province", "VARCHAR(2)"),
    ("companies", "postal_code", "VARCHAR(10)"),
    ("companies", "gst_number", "VARCHAR(40)"),
    ("companies", "pst_number", "VARCHAR(40)"),
    ("companies", "qst_number", "VARCHAR(40)"),
    ("companies", "neq", "VARCHAR(40)"),
    ("companies", "payment_instructions", "TEXT"),
    ("clients", "street", "VARCHAR(200)"),
    ("clients", "city", "VARCHAR(120)"),
    ("clients", "province", "VARCHAR(2)"),
    ("clients", "postal_code", "VARCHAR(10)"),
    ("payments", "method", "VARCHAR(20) NOT NULL DEFAULT 'cash'"),
    ("payments", "reference", "VARCHAR(120)"),
    ("invoices", "issuer_name", "VARCHAR(120)"),
    ("invoices", "issuer_address", "TEXT"),
    ("invoices", "issuer_gst_number", "VARCHAR(40)"),
    ("invoices", "issuer_pst_number", "VARCHAR(40)"),
    ("invoices", "issuer_qst_number", "VARCHAR(40)"),
    ("invoices", "issuer_neq", "VARCHAR(40)"),
    ("invoices", "issuer_payment_instructions", "TEXT"),
    ("invoices", "issued_subtotal", "FLOAT"),
    ("orders", "order_type_id", "INTEGER"),
]

# Free-text address columns replaced by street/city/province/postal_code.
# Same table-by-table treatment: move what's there into `street`, drop the
# old column. See _migrate_free_text_address.
_SPLIT_ADDRESS_TABLES = ("companies", "clients")


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

    _backfill_invoice_issuers()


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


def _backfill_invoice_issuers() -> None:
    """Freeze invoices that were issued before there was anything to freeze.

    Two halves, and they're deliberately different:

    - Issuer details: today's company settings are the only approximation
      available, so freeze those rather than let Invoice.issuer keep
      falling back to live values.
    - Money: freeze the subtotal, but with **no tax rows**. These invoices
      were issued before tax was calculated at all, so zero tax is what
      their clients actually received — inventing tax for them now would
      change amounts that have already been billed.
    """
    stale = Invoice.query.filter(
        Invoice.status != "draft",
        sa.or_(Invoice.issuer_name.is_(None), Invoice.issued_subtotal.is_(None)),
    ).all()
    for invoice in stale:
        if invoice.issuer_name is None:
            invoice.freeze_issuer()
        if invoice.issued_subtotal is None:
            invoice.issued_subtotal = invoice.order.subtotal
    if stale:
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
# Seed data — same sample clients/orders the in-memory prototype used to
# hardcode, now inserted into SQLite on first run. Placeholder documents
# ("Mockup" + "Invoice") are attached to every order, matching the old
# _SAMPLE_DOCUMENTS behavior.
# ---------------------------------------------------------------------------

# Provinces are spread across QC / BC / ON on purpose: tax is charged at
# the client's province's rate, so the sample data exercises GST+QST,
# GST+PST and HST. Two clients (6, 8) have no address at all, which is
# what an order with uncalculable tax looks like — see Order.tax_status.
_SAMPLE_CLIENTS = [
    {"id": 1, "first_name": "Marie", "last_name": "Alarie", "email": "m.alarie@example.com", "phone": "514-555-0142", "street": "1240 rue Saint-Denis", "city": "Montréal", "province": "QC", "postal_code": "H2X 3J5"},
    {"id": 2, "first_name": "Sarah", "last_name": "Okafor", "email": "s.okafor@example.com", "phone": "604-555-0198", "street": "780 Bute St, Apt 1104", "city": "Vancouver", "province": "BC", "postal_code": "V6E 1Y9"},
    {"id": 3, "first_name": "Ryan", "last_name": "Chen", "email": "r.chen@example.com", "phone": "778-555-0110", "street": "3355 Cambie St", "city": "Vancouver", "province": "BC", "postal_code": "V5Z 2W6"},
    {"id": 4, "first_name": "Lucas", "last_name": "Beaumont", "email": "l.beaumont@example.com", "phone": "438-555-0176", "street": "55 avenue Laurier O", "city": "Montréal", "province": "QC", "postal_code": "H2T 2N4"},
    {"id": 5, "first_name": "Anna", "last_name": "Novak", "email": "a.novak@example.com", "phone": "416-555-0133", "street": "914 Queen St W", "city": "Toronto", "province": "ON", "postal_code": "M6J 1G6"},
    {"id": 6, "first_name": "Thomas", "last_name": "Iverson", "email": "t.iverson@example.com", "phone": "604-555-0121", "street": None, "city": None, "province": None, "postal_code": None},
    {"id": 7, "first_name": "Pierre", "last_name": "Dubois", "email": "p.dubois@example.com", "phone": "514-555-0187", "street": "203 rue Ontario E", "city": "Montréal", "province": "QC", "postal_code": "H2X 1H5"},
    {"id": 8, "first_name": "Hannah", "last_name": "Solberg", "email": "h.solberg@example.com", "phone": "778-555-0165", "street": None, "city": None, "province": None, "postal_code": None},
    {"id": 9, "first_name": "Giulia", "last_name": "Marchetti", "email": "g.marchetti@example.com", "phone": "416-555-0154", "street": "62 Ossington Ave", "city": "Toronto", "province": "ON", "postal_code": "M6J 2Y7"},
    {"id": 10, "first_name": "Nadia", "last_name": "Petrova", "email": "n.petrova@example.com", "phone": "604-555-0109", "street": None, "city": None, "province": None, "postal_code": None},
]

# Dates lean toward the end of July (2026-07-26 "today" at the time this was
# written) so the timeline's default window has plenty to show around today.
# client_id 1 and 3 each get a second order below, so they show up as
# "returning" clients in the seeded data.
#
# "lines" is (description, quantity, unit_price) — an order's value is the
# sum of these, there's no separate price field. "payments" is optional per
# order: orders without it have no deposit recorded yet (matches real
# orders that haven't been confirmed with a deposit). Where present,
# amounts are rough ~50% deposits, not full payment, except the one
# delivered order (fully settled, as it would be by pickup). Payment
# methods are mixed across cash / e-transfer / Square on purpose, since
# reconciling those three against one invoice is the point.
_SAMPLE_ORDERS = [
    {"id": 1, "client_id": 1, "item": "Full-grain briefcase", "start": date(2026, 7, 1), "due": date(2026, 7, 15), "status": "delivered", "notes": "Horween Chromexcel, brass hardware", "order_type": "Custom Order",
     "lines": [("Full-grain briefcase, Horween Chromexcel", 1, 760.00), ("Brass hardware upgrade", 1, 90.00)],
     "payments": [(425.00, date(2026, 7, 1), "square", "sq:9F2K-4471"), (425.00, date(2026, 7, 15), "cash", None)]},
    {"id": 2, "client_id": 2, "item": "Weekender duffel", "start": date(2026, 7, 8), "due": date(2026, 7, 29), "status": "in_progress", "notes": "Waxed canvas panels + veg-tan trim",
     "lines": [("Weekender duffel, veg-tan trim", 1, 560.00), ("Waxed canvas panels", 1, 60.00)],
     "payments": [(310.00, date(2026, 7, 8), "etransfer", "e-tfr CA8821")]},
    {"id": 3, "client_id": 3, "item": "Bifold wallet (monogram)", "start": date(2026, 7, 15), "due": date(2026, 7, 24), "status": "ready", "notes": "Hand-stitched, gold foil initials",
     "lines": [("Bifold wallet, hand-stitched", 1, 110.00), ("Gold foil monogram", 1, 30.00)],
     "payments": [(70.00, date(2026, 7, 15), "cash", None)]},
    {"id": 4, "client_id": 4, "item": "Messenger bag", "start": date(2026, 7, 18), "due": date(2026, 7, 30), "status": "rush", "notes": "Client travels on the 31st", "order_type": "Custom Order",
     "lines": [("Messenger bag", 1, 430.00), ("Rush surcharge", 1, 50.00)],
     "payments": [(240.00, date(2026, 7, 18), "square", "sq:7T1B-9930")]},
    {"id": 5, "client_id": 5, "item": "Belt, 38mm", "start": date(2026, 7, 20), "due": date(2026, 7, 27), "status": "in_progress", "notes": "English bridle leather",
     "lines": [("Belt, 38mm English bridle", 1, 95.00)]},
    {"id": 6, "client_id": 6, "item": "Camera strap", "start": date(2026, 7, 19), "due": date(2026, 7, 25), "status": "ready", "notes": "Padded, nickel rivets",
     "lines": [("Camera strap, padded", 1, 95.00), ("Nickel rivets", 1, 15.00)],
     "payments": [(55.00, date(2026, 7, 19), "etransfer", "e-tfr CA9014")]},
    {"id": 7, "client_id": 7, "item": "Tote bag", "start": date(2026, 7, 17), "due": date(2026, 8, 1), "status": "in_progress", "notes": "Natural veg-tan, will patina", "order_type": "White Label",
     "lines": [("Tote bag, natural veg-tan", 1, 310.00)]},
    {"id": 8, "client_id": 1, "item": "Passport holder (x2)", "start": date(2026, 7, 22), "due": date(2026, 7, 28), "status": "in_progress", "notes": "Gift for anniversary",
     "lines": [("Passport holder", 2, 65.00)],
     "payments": [(65.00, date(2026, 7, 22), "cash", None)]},
    {"id": 9, "client_id": 8, "item": "Watch strap", "start": date(2026, 7, 24), "due": date(2026, 7, 29), "status": "rush", "notes": "Custom buckle from client's own", "order_type": "Custom Order",
     "lines": [("Watch strap, client's own buckle", 1, 85.00)]},
    {"id": 10, "client_id": 9, "item": "Laptop sleeve", "start": date(2026, 7, 23), "due": date(2026, 7, 31), "status": "in_progress", "notes": "13-inch, felt lining",
     "lines": [("Laptop sleeve, 13-inch", 1, 145.00), ("Felt lining", 1, 20.00)]},
    {"id": 11, "client_id": 10, "item": "Card holder", "start": date(2026, 7, 26), "due": date(2026, 8, 2), "status": "in_progress", "notes": "Minimalist, 3-slot",
     "lines": [("Card holder, 3-slot", 1, 75.00)]},
    {"id": 12, "client_id": 3, "item": "Travel journal cover", "start": date(2026, 7, 21), "due": date(2026, 7, 27), "status": "ready", "notes": "Refillable, brass corners", "order_type": "Consulting/Sampling",
     "lines": [("Travel journal cover, refillable", 1, 105.00), ("Brass corners", 1, 15.00)],
     "payments": [(60.00, date(2026, 7, 21), "etransfer", "e-tfr CA9127")]},
]

# Only some orders are invoiced — matching reality, where an invoice gets
# raised when work is confirmed rather than the moment an order is booked.
# order 1 is fully paid (so it renders as "Paid" without the status saying
# so), 2 and 4 are sent-and-partly-paid, 12 is still a draft.
_SAMPLE_INVOICES = [
    {"order_id": 1, "number": "BM-2026-0001", "issued_date": date(2026, 7, 1), "due_date": date(2026, 7, 15), "status": "sent", "notes": None},
    {"order_id": 2, "number": "BM-2026-0002", "issued_date": date(2026, 7, 8), "due_date": date(2026, 7, 29), "status": "sent", "notes": "50% deposit taken on issue."},
    {"order_id": 4, "number": "BM-2026-0003", "issued_date": date(2026, 7, 18), "due_date": date(2026, 7, 30), "status": "sent", "notes": "Rush order — balance due at pickup."},
    {"order_id": 12, "number": "BM-2026-0004", "issued_date": date(2026, 7, 21), "due_date": None, "status": "draft", "notes": None},
]

_SAMPLE_DOCUMENTS = [
    {"label": "Mockup", "filename": "mockup_v1.pdf"},
    {"label": "Invoice", "filename": "invoice_draft.pdf"},
]

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
    """Populate a fresh database with the sample "By Monsieur" tenant."""
    if Company.query.count() > 0:
        return

    # Address and registration numbers here are placeholders in the right
    # shape, NOT the studio's real ones — same caveat as prices and lead
    # times. Replace them from /settings before issuing anything real.
    # pst_number is deliberately left unset: a Quebec seller charges QST,
    # not PST, and it keeps the "blank registrations don't print" path in
    # the sample data.
    company = Company(
        name="By Monsieur",
        invoice_prefix="BM",
        street="4820 rue Sainte-Catherine E, Studio 3",
        city="Montréal",
        province="QC",
        postal_code="H1V 1M6",
        gst_number="123456789 RT0001",
        qst_number="1234567890 TQ0001",
        neq="1234567890",
        payment_instructions=(
            "E-transfer to payments@example.com — no security question needed.\n"
            "Cash accepted at pickup. Cheques payable to By Monsieur."
        ),
    )
    db.session.add(company)
    db.session.flush()  # assigns company.id

    admin = User(company_id=company.id, username="admin")
    admin.set_password(admin_password)
    db.session.add(admin)

    for i, label in enumerate(_DEFAULT_SOURCE_OPTIONS):
        db.session.add(SourceOption(company_id=company.id, label=label, sort_order=i))

    order_types = {}
    for i, label in enumerate(_DEFAULT_ORDER_TYPES):
        order_type = OrderType(company_id=company.id, label=label, sort_order=i)
        db.session.add(order_type)
        order_types[label] = order_type
    db.session.flush()  # assigns order_type.id, needed below

    for c in _SAMPLE_CLIENTS:
        client = Client(
            id=c["id"], company_id=company.id,
            first_name=c["first_name"], last_name=c["last_name"],
            email=c["email"], phone=c["phone"],
            street=c["street"], city=c["city"],
            province=c["province"], postal_code=c["postal_code"],
        )
        db.session.add(client)

    for o in _SAMPLE_ORDERS:
        order_type = order_types.get(o.get("order_type"))
        order = Order(
            id=o["id"], client_id=o["client_id"], item=o["item"],
            start=o["start"], due=o["due"],
            status=o["status"], notes=o["notes"],
            order_type_id=order_type.id if order_type else None,
        )
        db.session.add(order)
        db.session.flush()  # assigns order.id if not already set
        for i, (description, quantity, unit_price) in enumerate(o["lines"]):
            db.session.add(OrderLine(
                order_id=order.id, description=description,
                quantity=quantity, unit_price=unit_price, sort_order=i,
            ))
        for doc in _SAMPLE_DOCUMENTS:
            db.session.add(Document(order_id=order.id, label=doc["label"], filename=doc["filename"]))
        for amount, paid_date, method, reference in o.get("payments", []):
            db.session.add(Payment(
                order_id=order.id, amount=amount, paid_date=paid_date,
                method=method, reference=reference,
            ))

    invoices = [Invoice(company_id=company.id, **inv) for inv in _SAMPLE_INVOICES]
    db.session.add_all(invoices)
    db.session.flush()  # so invoice.order / invoice.company resolve below

    # Freeze exactly the way the app does at the draft -> issued
    # transition, so the sample data isn't in a state the app can't reach.
    for invoice in invoices:
        if invoice.status != "draft":
            invoice.freeze()

    db.session.commit()
