"""
The public API: profiles, numbering, issuing, and resolved documents.

The rule this file implements, and the reason `freeze` exists at all:
**once an invoice leaves draft, nothing that happens afterwards may change
what it says.** Not the seller's settings, not the buyer's province, not
the subject's line items. A draft, which nobody has seen, tracks all of
them.

`Billable` comes from the host's adapter — see `billing/documents.py`.
Nothing here imports a host model.
"""

from dataclasses import dataclass
from datetime import date

from models import db

from billing import config, tax
from billing.documents import Billable, InvoiceDocument, IssuerDetails
from billing.models import BillingProfile, Invoice, InvoiceTaxLine, next_invoice_number

__all__ = [
    "Amounts", "amounts_for", "create_invoice", "document_for", "documents_for",
    "get_invoice", "invoice_for_subject", "list_invoices", "next_number",
    "profile_for", "set_status", "update_profile",
]


@dataclass(frozen=True)
class Amounts:
    """Resolved money for a subject: frozen if issued, live if not."""

    subtotal: float
    tax_lines: list[tax.TaxLine]
    amount_paid: float
    tax_status: str

    @property
    def tax_total(self) -> float:
        return sum(line.amount for line in self.tax_lines)

    @property
    def total(self) -> float:
        return self.subtotal + self.tax_total

    @property
    def balance_due(self) -> float:
        return self.total - self.amount_paid

    @property
    def is_settled(self) -> bool:
        return self.balance_due < 0.005


# --- Profiles -------------------------------------------------------------

def profile_for(company_id: int, display_name: str = "") -> BillingProfile:
    """This tenant's billing profile, created empty on first use.

    Created rather than returned-as-None so a host never has to special-case
    "not set up yet"; an empty profile simply prints no letterhead.
    """
    profile = BillingProfile.query.filter_by(company_id=company_id).first()
    if profile is None:
        profile = BillingProfile(company_id=company_id, display_name=display_name)
        db.session.add(profile)
        db.session.flush()
    elif display_name:
        # Only when the caller actually supplied one — a bare
        # profile_for(company_id) must not blank the stored name.
        profile.display_name = display_name
    return profile


def update_profile(company_id: int, display_name: str = "", **fields) -> BillingProfile:
    """Set letterhead fields. Unknown keys are ignored rather than raising,
    so a host form can post whatever it renders.

    Editing these never touches invoices already issued — those carry their
    own frozen copy.
    """
    profile = profile_for(company_id, display_name)
    editable = {
        "invoice_prefix", "street", "city", "province", "postal_code",
        "gst_number", "pst_number", "qst_number", "neq", "payment_instructions",
    }
    for key, value in fields.items():
        if key in editable:
            setattr(profile, key, value)
    if not profile.invoice_prefix:
        profile.invoice_prefix = "INV"
    return profile


def next_number(company_id: int, display_name: str = "", today: date | None = None) -> str:
    profile = profile_for(company_id, display_name)
    return next_invoice_number(company_id, profile.invoice_prefix, today)


# --- Lookups --------------------------------------------------------------

def get_invoice(company_id: int, invoice_id: int) -> Invoice | None:
    return Invoice.query.filter_by(id=invoice_id, company_id=company_id).first()


def invoice_for_subject(company_id: int, subject_id: int) -> Invoice | None:
    return Invoice.query.filter_by(company_id=company_id, subject_id=subject_id).first()


def list_invoices(company_id: int) -> list[Invoice]:
    return (
        Invoice.query.filter_by(company_id=company_id)
        .order_by(Invoice.issued_date.desc(), Invoice.number.desc())
        .all()
    )


def invoiced_subject_ids(company_id: int) -> set[int]:
    """For a host asking "what haven't I invoiced yet?"."""
    return {
        row[0] for row in
        db.session.query(Invoice.subject_id).filter(Invoice.company_id == company_id)
    }


# --- Money ----------------------------------------------------------------

def amounts_for(
    billable: Billable, issuer: IssuerDetails, invoice: Invoice | None = None
) -> Amounts:
    """Resolve what a subject is worth.

    Frozen once its invoice has been issued, live before that — which is
    what stops an edit to the subject's lines from changing a number the
    buyer has already been given.
    """
    issued = invoice is not None and invoice.status != "draft"
    if issued and invoice.is_frozen:
        lines = invoice.frozen_tax_lines
        subtotal = invoice.issued_subtotal
    elif issued:
        # Issued before freezing existed: it went out with no tax on it.
        lines, subtotal = [], billable.subtotal
    else:
        subtotal = billable.subtotal
        lines = tax.taxes_for(
            billable.tax_province, issuer.tax_registrations, subtotal
        )
    return Amounts(
        subtotal=subtotal,
        tax_lines=lines,
        amount_paid=billable.amount_paid,
        tax_status=tax.status_for(
            billable.tax_province, issuer.tax_registrations, lines
        ),
    )


def display_status(invoice: Invoice, amounts: Amounts) -> str:
    """Stored state, except that a fully-paid invoice reports "paid"
    without anyone having to remember to set it."""
    if invoice.status == "void":
        return "void"
    if amounts.is_settled and amounts.total > 0:
        return "paid"
    return invoice.status


def is_outstanding(invoice: Invoice, amounts: Amounts) -> bool:
    """Issued, not voided, and still owed money."""
    return invoice.status != "void" and not amounts.is_settled


def document_for(
    company_id: int, invoice: Invoice, billable: Billable, display_name: str = ""
) -> InvoiceDocument:
    """Everything a template needs to render one invoice.

    A draft reads the seller's details live — nobody has seen it, so fixing
    a typo in the GST number should reach it. Anything past draft reads the
    frozen copy.
    """
    frozen = invoice.frozen_issuer
    issuer = (
        profile_for(company_id, display_name).issuer
        if invoice.status == "draft" or frozen is None
        else frozen
    )
    amounts = amounts_for(billable, issuer, invoice)
    return InvoiceDocument(
        number=invoice.number,
        status=invoice.status,
        display_status=display_status(invoice, amounts),
        issued_date=invoice.issued_date,
        due_date=invoice.due_date,
        notes=invoice.notes,
        issuer=issuer,
        payer=billable.payer,
        subject_description=billable.description,
        subject_url=billable.url,
        lines=billable.lines,
        payments=billable.payments,
        subtotal=amounts.subtotal,
        tax_lines=amounts.tax_lines,
        amount_paid=amounts.amount_paid,
        is_frozen=invoice.is_frozen,
        tax_status=amounts.tax_status,
    )


def documents_for(
    company_id: int, resolve, display_name: str = ""
) -> list[InvoiceDocument]:
    """Every invoice for a tenant, resolved.

    `resolve` is the host's adapter: subject_id -> Billable. Passed in
    rather than registered globally so this stays a plain function call.
    """
    return [
        document_for(company_id, invoice, resolve(invoice.subject_id), display_name)
        for invoice in list_invoices(company_id)
    ]


# --- Issuing --------------------------------------------------------------

def create_invoice(
    company_id: int, billable: Billable, due_date: date | None = None,
    display_name: str = "", today: date | None = None,
) -> Invoice:
    """Raise a draft invoice, assigning the next number.

    Returns the existing one if the subject already has an invoice — a
    double-submitted button must not burn a second number.
    """
    existing = invoice_for_subject(company_id, billable.subject_id)
    if existing is not None:
        return existing

    invoice = Invoice(
        company_id=company_id,
        subject_id=billable.subject_id,
        number=next_number(company_id, display_name, today),
        issued_date=today or date.today(),
        due_date=due_date,
        status="draft",
    )
    db.session.add(invoice)
    db.session.flush()
    return invoice


def freeze(company_id: int, invoice: Invoice, billable: Billable,
           display_name: str = "") -> None:
    """Snapshot the seller's details and the money onto this invoice.

    Call on the draft -> issued transition only. Re-running it on an
    already-issued invoice rewrites history, which is the exact thing the
    snapshot exists to prevent — `set_status` enforces that.
    """
    issuer = profile_for(company_id, display_name).issuer
    invoice.apply_issuer(issuer)
    invoice.issued_subtotal = billable.subtotal
    invoice.tax_rows = [
        InvoiceTaxLine(label=line.label, rate=line.rate, amount=line.amount, sort_order=i)
        for i, line in enumerate(
            tax.taxes_for(billable.tax_province, issuer.tax_registrations,
                          billable.subtotal)
        )
    ]


def set_status(
    company_id: int, invoice: Invoice, status: str | None, billable: Billable,
    notes: str | None = None, due_date: date | None = None, display_name: str = "",
) -> None:
    """Apply a status change, freezing on the way out of draft.

    The before/after comparison is the whole protection: freeze too eagerly
    and re-saving a sent invoice re-stamps it with today's settings.
    An unrecognised status is ignored rather than stored.
    """
    was_draft = invoice.status == "draft"
    if status in config.SETTABLE_STATUSES:
        invoice.status = status
    if was_draft and invoice.status != "draft":
        freeze(company_id, invoice, billable, display_name)
    invoice.notes = (notes or "").strip() or None
    invoice.due_date = due_date


# --- Reporting ------------------------------------------------------------

def tax_collected(company_id: int, since: date | None = None,
                  until: date | None = None) -> list[tuple[str, float]]:
    """(label, amount) per tax across issued invoices — a remittance total.

    This is why InvoiceTaxLine is a table and not a JSON blob.
    """
    query = (
        db.session.query(InvoiceTaxLine.label, db.func.sum(InvoiceTaxLine.amount))
        .join(Invoice, InvoiceTaxLine.invoice_id == Invoice.id)
        .filter(Invoice.company_id == company_id, Invoice.status != "void")
    )
    if since is not None:
        query = query.filter(Invoice.issued_date >= since)
    if until is not None:
        query = query.filter(Invoice.issued_date <= until)
    return [(label, total or 0.0) for label, total in query.group_by(InvoiceTaxLine.label)]
