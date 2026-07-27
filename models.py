"""
SQLAlchemy models + seed data.

Company is the tenant boundary: everything else (users, clients, and
transitively orders/documents) hangs off a company_id. Today only one
company is seeded ("By Monsieur"), but scoping queries by company_id from
the start means adding a second tenant later is additive, not a rewrite.
"""

from datetime import date

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.ext.hybrid import hybrid_property
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


class Company(db.Model):
    __tablename__ = "companies"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)

    users = db.relationship("User", back_populates="company")
    clients = db.relationship("Client", back_populates="company")
    source_options = db.relationship(
        "SourceOption", back_populates="company",
        order_by="SourceOption.sort_order",
    )


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


class Client(db.Model):
    __tablename__ = "clients"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120))
    phone = db.Column(db.String(40))
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
    def is_returning(self):
        """A second order marks a client as a repeat customer."""
        return len(self.orders) >= 2

    @property
    def lifetime_value(self):
        return sum(order.price for order in self.orders)


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    item = db.Column(db.String(200), nullable=False)
    start = db.Column(db.Date, nullable=False)
    due = db.Column(db.Date, nullable=False)
    price = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), nullable=False)
    notes = db.Column(db.Text)

    client = db.relationship("Client", back_populates="orders")
    documents = db.relationship("Document", back_populates="order")
    payments = db.relationship("Payment", back_populates="order", cascade="all, delete-orphan")

    @property
    def amount_paid(self):
        return sum(payment.amount for payment in self.payments)

    @property
    def balance_due(self):
        return self.price - self.amount_paid


class Payment(db.Model):
    """A single payment recorded against an order — deposit, balance, or
    anything in between. Deliberately generic (just an amount + a date):
    different clients/studios run different deposit schemes (e.g. Joe's is
    ~50% up front, order due balance at pickup), so nothing here assumes a
    fixed split or a fixed number of payments per order.
    """
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    paid_date = db.Column(db.Date, nullable=False)

    order = db.relationship("Order", back_populates="payments")


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    label = db.Column(db.String(80), nullable=False)
    filename = db.Column(db.String(200), nullable=False)

    order = db.relationship("Order", back_populates="documents")


# ---------------------------------------------------------------------------
# Seed data — same sample clients/orders the in-memory prototype used to
# hardcode, now inserted into SQLite on first run. Placeholder documents
# ("Mockup" + "Invoice") are attached to every order, matching the old
# _SAMPLE_DOCUMENTS behavior.
# ---------------------------------------------------------------------------

_SAMPLE_CLIENTS = [
    {"id": 1, "first_name": "Marie", "last_name": "Alarie", "email": "m.alarie@example.com", "phone": "514-555-0142"},
    {"id": 2, "first_name": "Sarah", "last_name": "Okafor", "email": "s.okafor@example.com", "phone": "604-555-0198"},
    {"id": 3, "first_name": "Ryan", "last_name": "Chen", "email": "r.chen@example.com", "phone": "778-555-0110"},
    {"id": 4, "first_name": "Lucas", "last_name": "Beaumont", "email": "l.beaumont@example.com", "phone": "438-555-0176"},
    {"id": 5, "first_name": "Anna", "last_name": "Novak", "email": "a.novak@example.com", "phone": "416-555-0133"},
    {"id": 6, "first_name": "Thomas", "last_name": "Iverson", "email": "t.iverson@example.com", "phone": "604-555-0121"},
    {"id": 7, "first_name": "Pierre", "last_name": "Dubois", "email": "p.dubois@example.com", "phone": "514-555-0187"},
    {"id": 8, "first_name": "Hannah", "last_name": "Solberg", "email": "h.solberg@example.com", "phone": "778-555-0165"},
    {"id": 9, "first_name": "Giulia", "last_name": "Marchetti", "email": "g.marchetti@example.com", "phone": "416-555-0154"},
    {"id": 10, "first_name": "Nadia", "last_name": "Petrova", "email": "n.petrova@example.com", "phone": "604-555-0109"},
]

# Dates lean toward the end of July (2026-07-26 "today" at the time this was
# written) so the timeline's default window has plenty to show around today.
# client_id 1 and 3 each get a second order below, so they show up as
# "returning" clients in the seeded data.
#
# "payments" is optional per order — orders without it have no deposit
# recorded yet (matches real orders that haven't been confirmed with a
# deposit). Where present, amounts are rough ~50% deposits, not full
# payment, except the one delivered order (fully settled, as it would be
# by pickup).
_SAMPLE_ORDERS = [
    {"id": 1, "client_id": 1, "item": "Full-grain briefcase", "start": date(2026, 7, 1), "due": date(2026, 7, 15), "price": 850.00, "status": "delivered", "notes": "Horween Chromexcel, brass hardware", "payments": [(425.00, date(2026, 7, 1)), (425.00, date(2026, 7, 15))]},
    {"id": 2, "client_id": 2, "item": "Weekender duffel", "start": date(2026, 7, 8), "due": date(2026, 7, 29), "price": 620.00, "status": "in_progress", "notes": "Waxed canvas panels + veg-tan trim", "payments": [(310.00, date(2026, 7, 8))]},
    {"id": 3, "client_id": 3, "item": "Bifold wallet (monogram)", "start": date(2026, 7, 15), "due": date(2026, 7, 24), "price": 140.00, "status": "ready", "notes": "Hand-stitched, gold foil initials", "payments": [(70.00, date(2026, 7, 15))]},
    {"id": 4, "client_id": 4, "item": "Messenger bag", "start": date(2026, 7, 18), "due": date(2026, 7, 30), "price": 480.00, "status": "rush", "notes": "Client travels on the 31st", "payments": [(240.00, date(2026, 7, 18))]},
    {"id": 5, "client_id": 5, "item": "Belt, 38mm", "start": date(2026, 7, 20), "due": date(2026, 7, 27), "price": 95.00, "status": "in_progress", "notes": "English bridle leather"},
    {"id": 6, "client_id": 6, "item": "Camera strap", "start": date(2026, 7, 19), "due": date(2026, 7, 25), "price": 110.00, "status": "ready", "notes": "Padded, nickel rivets", "payments": [(55.00, date(2026, 7, 19))]},
    {"id": 7, "client_id": 7, "item": "Tote bag", "start": date(2026, 7, 17), "due": date(2026, 8, 1), "price": 310.00, "status": "in_progress", "notes": "Natural veg-tan, will patina"},
    {"id": 8, "client_id": 1, "item": "Passport holder (x2)", "start": date(2026, 7, 22), "due": date(2026, 7, 28), "price": 130.00, "status": "in_progress", "notes": "Gift for anniversary", "payments": [(65.00, date(2026, 7, 22))]},
    {"id": 9, "client_id": 8, "item": "Watch strap", "start": date(2026, 7, 24), "due": date(2026, 7, 29), "price": 85.00, "status": "rush", "notes": "Custom buckle from client's own"},
    {"id": 10, "client_id": 9, "item": "Laptop sleeve", "start": date(2026, 7, 23), "due": date(2026, 7, 31), "price": 165.00, "status": "in_progress", "notes": "13-inch, felt lining"},
    {"id": 11, "client_id": 10, "item": "Card holder", "start": date(2026, 7, 26), "due": date(2026, 8, 2), "price": 75.00, "status": "in_progress", "notes": "Minimalist, 3-slot"},
    {"id": 12, "client_id": 3, "item": "Travel journal cover", "start": date(2026, 7, 21), "due": date(2026, 7, 27), "price": 120.00, "status": "ready", "notes": "Refillable, brass corners", "payments": [(60.00, date(2026, 7, 21))]},
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


def seed_if_empty(admin_password: str = "changeme") -> None:
    """Populate a fresh database with the sample "By Monsieur" tenant."""
    if Company.query.count() > 0:
        return

    company = Company(name="By Monsieur")
    db.session.add(company)
    db.session.flush()  # assigns company.id

    admin = User(company_id=company.id, username="admin")
    admin.set_password(admin_password)
    db.session.add(admin)

    for i, label in enumerate(_DEFAULT_SOURCE_OPTIONS):
        db.session.add(SourceOption(company_id=company.id, label=label, sort_order=i))

    for c in _SAMPLE_CLIENTS:
        client = Client(
            id=c["id"], company_id=company.id,
            first_name=c["first_name"], last_name=c["last_name"],
            email=c["email"], phone=c["phone"],
        )
        db.session.add(client)

    for o in _SAMPLE_ORDERS:
        order = Order(
            id=o["id"], client_id=o["client_id"], item=o["item"],
            start=o["start"], due=o["due"], price=o["price"],
            status=o["status"], notes=o["notes"],
        )
        db.session.add(order)
        db.session.flush()  # assigns order.id if not already set
        for doc in _SAMPLE_DOCUMENTS:
            db.session.add(Document(order_id=order.id, label=doc["label"], filename=doc["filename"]))
        for amount, paid_date in o.get("payments", []):
            db.session.add(Payment(order_id=order.id, amount=amount, paid_date=paid_date))

    db.session.commit()
