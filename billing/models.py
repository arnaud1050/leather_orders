"""
This module's own tables: the seller's billing profile, and invoices.

`db` is the host's SQLAlchemy handle — the same arrangement
`communications/models.py` uses. It's the one import from outside the
module, and it's a connection, not a domain concept.

Nothing here reaches into the host's models. An invoice knows a
`subject_id` (see config.SUBJECT_FK) and nothing else about what it bills
for; the figures come in as a `Billable` from the host's adapter.
"""

from datetime import date

from models import db

from billing import config
from billing.documents import IssuerDetails, format_address
from billing.tax import TaxLine

__all__ = ["BillingProfile", "Invoice", "InvoiceTaxLine", "next_invoice_number"]


class BillingProfile(db.Model):
    """Everything the seller puts on an invoice, per tenant.

    Owned here rather than on the host's Company so the module stands up on
    its own: a new project creates a profile per tenant and needs no
    billing columns on its own tenant model.

    Registration numbers are named Canadian fields rather than a generic
    label/value list, so they can be labelled correctly on the document.
    `pst_number` covers BC PST, Saskatchewan PST and Manitoba RST — a
    seller is realistically registered in at most one. If someone ever
    needs two at once, that's the signal to move registrations to a
    label/value list rather than adding `pst_number_2`.
    """

    __tablename__ = "billing_profiles"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"),
                           nullable=False, unique=True)

    # Leading segment of every invoice number this tenant issues
    # ("BM" -> BM-2026-0001).
    invoice_prefix = db.Column(db.String(10), nullable=False, default="INV")

    street = db.Column(db.String(200))
    city = db.Column(db.String(120))
    province = db.Column(db.String(2))  # two-letter code; see tax.PROVINCES
    postal_code = db.Column(db.String(10))

    gst_number = db.Column(db.String(40))
    pst_number = db.Column(db.String(40))
    qst_number = db.Column(db.String(40))
    neq = db.Column(db.String(40))

    # Free text printed under "How to pay". It exists because cash and
    # e-transfer have no hosted payment page to send anyone to.
    payment_instructions = db.Column(db.Text)

    @property
    def formatted_address(self) -> str | None:
        return format_address(self.street, self.city, self.province, self.postal_code)

    @property
    def issuer(self) -> IssuerDetails:
        return IssuerDetails(
            name=self.display_name,
            address=self.formatted_address,
            gst_number=self.gst_number,
            pst_number=self.pst_number,
            qst_number=self.qst_number,
            neq=self.neq,
            payment_instructions=self.payment_instructions,
        )

    # The seller's name lives on the host's tenant model, not here — a
    # company is called the same thing whether or not it invoices. The
    # host sets this when it loads the profile; see services.profile_for.
    display_name: str = ""


class Invoice(db.Model):
    """The billing record for one subject, and the owner of its number.

    Numbering is this module's job, not a payment processor's: one sequence
    per tenant means a cash sale and a card sale get numbers from the same
    run, which is what makes them reconcilable.

    `status` stores only what can't be worked out — draft / sent / void.
    Paid-ness is derived from payments so it can't disagree with them.
    """

    __tablename__ = "invoices"
    __table_args__ = (
        db.UniqueConstraint("company_id", "number", name="uq_invoice_company_number"),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    # The host's id for what's being billed. Named generically because this
    # module doesn't care what it is; the FK target is configurable.
    subject_id = db.Column(db.Integer, db.ForeignKey(config.SUBJECT_FK),
                           nullable=False, unique=True)
    number = db.Column(db.String(40), nullable=False)
    issued_date = db.Column(db.Date, nullable=False)
    due_date = db.Column(db.Date)
    status = db.Column(db.String(20), nullable=False, default="draft")
    notes = db.Column(db.Text)

    # Frozen copy of the seller's details, written when the invoice stops
    # being a draft. Reprinting has to match what the client received, even
    # if the seller has since moved or re-registered — so these can't be
    # read live off the profile.
    issuer_name = db.Column(db.String(120))
    issuer_address = db.Column(db.Text)
    issuer_gst_number = db.Column(db.String(40))
    issuer_pst_number = db.Column(db.String(40))
    issuer_qst_number = db.Column(db.String(40))
    issuer_neq = db.Column(db.String(40))
    issuer_payment_instructions = db.Column(db.Text)
    # The money, frozen at the same moment. Doubles as the "has this been
    # frozen" marker — an invoice issued before tax existed freezes with
    # zero tax rows, which is exactly what its client received.
    issued_subtotal = db.Column(db.Float)

    tax_rows = db.relationship(
        "InvoiceTaxLine", back_populates="invoice", cascade="all, delete-orphan",
        order_by="InvoiceTaxLine.sort_order",
    )

    @property
    def is_frozen(self) -> bool:
        return self.issued_subtotal is not None

    @property
    def frozen_tax_lines(self) -> list[TaxLine]:
        return [TaxLine(row.label, row.rate, row.amount) for row in self.tax_rows]

    @property
    def frozen_issuer(self) -> IssuerDetails | None:
        """The snapshot, or None if this invoice was never frozen."""
        if self.issuer_name is None:
            return None
        return IssuerDetails(
            name=self.issuer_name,
            address=self.issuer_address,
            gst_number=self.issuer_gst_number,
            pst_number=self.issuer_pst_number,
            qst_number=self.issuer_qst_number,
            neq=self.issuer_neq,
            payment_instructions=self.issuer_payment_instructions,
        )

    def apply_issuer(self, issuer: IssuerDetails) -> None:
        self.issuer_name = issuer.name
        self.issuer_address = issuer.address
        self.issuer_gst_number = issuer.gst_number
        self.issuer_pst_number = issuer.pst_number
        self.issuer_qst_number = issuer.qst_number
        self.issuer_neq = issuer.neq
        self.issuer_payment_instructions = issuer.payment_instructions


class InvoiceTaxLine(db.Model):
    """One tax line frozen onto an issued invoice.

    A real table rather than JSON so tax collected can be summed straight
    out of the database — which is what a remittance needs.
    """

    __tablename__ = "invoice_tax_lines"

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.id"), nullable=False)
    label = db.Column(db.String(20), nullable=False)
    rate = db.Column(db.Float, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    invoice = db.relationship("Invoice", back_populates="tax_rows")


def next_invoice_number(company_id: int, prefix: str, today: date | None = None) -> str:
    """Next number in `PREFIX-YEAR-0001` form for this tenant and year.

    Derived from the highest existing number rather than a count, so
    *voiding* an invoice never frees its number — the row stays. The unique
    constraint on (company_id, number) is the real guard: two simultaneous
    requests collide there rather than silently issuing the same number.

    Known limit: deleting the most recent invoice does hand its number
    back, since there is then no higher number to read. Fine for a draft
    nobody has seen; fix it (a per-tenant high-water mark) before exposing
    invoice deletion.
    """
    today = today or date.today()
    number_prefix = f"{prefix}-{today.year}-"
    last = (
        Invoice.query.filter(
            Invoice.company_id == company_id,
            Invoice.number.like(f"{number_prefix}%"),
        )
        .order_by(Invoice.number.desc())  # zero-padded, so string order == numeric
        .first()
    )
    sequence = int(last.number.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{number_prefix}{sequence:04d}"
