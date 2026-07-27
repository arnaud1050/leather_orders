"""
SQLAlchemy models + seed data.

Company is the tenant boundary: everything else (users, customers, and
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
    customers = db.relationship("Customer", back_populates="company")


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


class Customer(db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    email = db.Column(db.String(120))
    phone = db.Column(db.String(40))

    company = db.relationship("Company", back_populates="customers")
    orders = db.relationship("Order", back_populates="customer")

    @hybrid_property
    def name(self):
        return f"{self.first_name} {self.last_name}"


class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    item = db.Column(db.String(200), nullable=False)
    start = db.Column(db.Date, nullable=False)
    due = db.Column(db.Date, nullable=False)
    price = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), nullable=False)
    notes = db.Column(db.Text)

    customer = db.relationship("Customer", back_populates="orders")
    documents = db.relationship("Document", back_populates="order")


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    label = db.Column(db.String(80), nullable=False)
    filename = db.Column(db.String(200), nullable=False)

    order = db.relationship("Order", back_populates="documents")


# ---------------------------------------------------------------------------
# Seed data — same sample customers/orders the in-memory prototype used to
# hardcode, now inserted into SQLite on first run. Placeholder documents
# ("Mockup" + "Invoice") are attached to every order, matching the old
# _SAMPLE_DOCUMENTS behavior.
# ---------------------------------------------------------------------------

_SAMPLE_CUSTOMERS = [
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
_SAMPLE_ORDERS = [
    {"id": 1, "customer_id": 1, "item": "Full-grain briefcase", "start": date(2026, 7, 1), "due": date(2026, 7, 15), "price": 850.00, "status": "delivered", "notes": "Horween Chromexcel, brass hardware"},
    {"id": 2, "customer_id": 2, "item": "Weekender duffel", "start": date(2026, 7, 8), "due": date(2026, 7, 29), "price": 620.00, "status": "in_progress", "notes": "Waxed canvas panels + veg-tan trim"},
    {"id": 3, "customer_id": 3, "item": "Bifold wallet (monogram)", "start": date(2026, 7, 15), "due": date(2026, 7, 24), "price": 140.00, "status": "ready", "notes": "Hand-stitched, gold foil initials"},
    {"id": 4, "customer_id": 4, "item": "Messenger bag", "start": date(2026, 7, 18), "due": date(2026, 7, 30), "price": 480.00, "status": "rush", "notes": "Client travels on the 31st"},
    {"id": 5, "customer_id": 5, "item": "Belt, 38mm", "start": date(2026, 7, 20), "due": date(2026, 7, 27), "price": 95.00, "status": "in_progress", "notes": "English bridle leather"},
    {"id": 6, "customer_id": 6, "item": "Camera strap", "start": date(2026, 7, 19), "due": date(2026, 7, 25), "price": 110.00, "status": "ready", "notes": "Padded, nickel rivets"},
    {"id": 7, "customer_id": 7, "item": "Tote bag", "start": date(2026, 7, 17), "due": date(2026, 8, 1), "price": 310.00, "status": "in_progress", "notes": "Natural veg-tan, will patina"},
    {"id": 8, "customer_id": 1, "item": "Passport holder (x2)", "start": date(2026, 7, 22), "due": date(2026, 7, 28), "price": 130.00, "status": "in_progress", "notes": "Gift for anniversary"},
    {"id": 9, "customer_id": 8, "item": "Watch strap", "start": date(2026, 7, 24), "due": date(2026, 7, 29), "price": 85.00, "status": "rush", "notes": "Custom buckle from client's own"},
    {"id": 10, "customer_id": 9, "item": "Laptop sleeve", "start": date(2026, 7, 23), "due": date(2026, 7, 31), "price": 165.00, "status": "in_progress", "notes": "13-inch, felt lining"},
    {"id": 11, "customer_id": 10, "item": "Card holder", "start": date(2026, 7, 26), "due": date(2026, 8, 2), "price": 75.00, "status": "in_progress", "notes": "Minimalist, 3-slot"},
    {"id": 12, "customer_id": 3, "item": "Travel journal cover", "start": date(2026, 7, 21), "due": date(2026, 7, 27), "price": 120.00, "status": "ready", "notes": "Refillable, brass corners"},
]

_SAMPLE_DOCUMENTS = [
    {"label": "Mockup", "filename": "mockup_v1.pdf"},
    {"label": "Invoice", "filename": "invoice_draft.pdf"},
]


def seed_if_empty() -> None:
    """Populate a fresh database with the sample "By Monsieur" tenant."""
    if Company.query.count() > 0:
        return

    company = Company(name="By Monsieur")
    db.session.add(company)
    db.session.flush()  # assigns company.id

    admin = User(company_id=company.id, username="admin")
    admin.set_password("changeme")
    db.session.add(admin)

    customers_by_id = {}
    for c in _SAMPLE_CUSTOMERS:
        customer = Customer(
            id=c["id"], company_id=company.id,
            first_name=c["first_name"], last_name=c["last_name"],
            email=c["email"], phone=c["phone"],
        )
        db.session.add(customer)
        customers_by_id[c["id"]] = customer

    for o in _SAMPLE_ORDERS:
        order = Order(
            id=o["id"], customer_id=o["customer_id"], item=o["item"],
            start=o["start"], due=o["due"], price=o["price"],
            status=o["status"], notes=o["notes"],
        )
        db.session.add(order)
        db.session.flush()  # assigns order.id if not already set
        for doc in _SAMPLE_DOCUMENTS:
            db.session.add(Document(order_id=order.id, label=doc["label"], filename=doc["filename"]))

    db.session.commit()
