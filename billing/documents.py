"""
The boundary between this module and whatever application hosts it.

Billing needs to know four things about the thing being billed: what the
lines are, where the buyer is (for tax), who to address it to, and what
has been paid. It gets those as the plain dataclasses below rather than
by importing the host's models — which is what stops `billing` from ever
depending on `Order`, `Client` or `Payment`.

The host writes one adapter that builds a `Billable`; see
`billing_adapter.py` in this project for the reference implementation.

Nothing here touches the database.
"""

from dataclasses import dataclass, field
from datetime import date

from billing.tax import TaxLine

__all__ = [
    "Billable", "InvoiceDocument", "IssuerDetails", "LineItem",
    "PartyDetails", "PaymentRecord", "format_address",
]


def format_address(street, city, province, postal_code) -> str | None:
    """Address as it prints: street, then "City, PROV  Postal".

    Two spaces before the postal code is the Canada Post convention.
    Returns None when nothing is filled in, so callers can skip the block
    instead of printing an empty line.
    """
    locality = ", ".join(part for part in (city, province) if part)
    if postal_code:
        locality = f"{locality}  {postal_code}".strip()
    return "\n".join(line for line in (street, locality) if line) or None


@dataclass(frozen=True)
class LineItem:
    description: str
    quantity: int
    unit_price: float

    @property
    def total(self) -> float:
        return self.quantity * self.unit_price


@dataclass(frozen=True)
class PaymentRecord:
    amount: float
    paid_date: date
    method: str
    reference: str | None = None


@dataclass(frozen=True)
class PartyDetails:
    """Who the document is addressed to."""

    name: str
    address: str | None = None
    email: str | None = None
    phone: str | None = None
    url: str | None = None  # host link, e.g. the client's page

    @property
    def contact_lines(self) -> list[str]:
        return [line for line in (self.address, self.email, self.phone) if line]


@dataclass(frozen=True)
class IssuerDetails:
    """Who issued the document, as it should print."""

    name: str
    address: str | None = None
    gst_number: str | None = None
    pst_number: str | None = None
    qst_number: str | None = None
    neq: str | None = None
    payment_instructions: str | None = None

    @property
    def registrations(self) -> list[tuple[str, str]]:
        """(label, number) pairs to print, skipping any that are unset.

        Tax accounts first, NEQ last — it identifies the enterprise, not a
        tax account.
        """
        pairs = [
            ("GST/HST", self.gst_number),
            ("PST/RST", self.pst_number),
            ("QST", self.qst_number),
            ("NEQ", self.neq),
        ]
        return [(label, value) for label, value in pairs if value]

    @property
    def tax_registrations(self) -> dict[str, str | None]:
        """The mapping `taxes_for` expects."""
        return {
            "gst_number": self.gst_number,
            "pst_number": self.pst_number,
            "qst_number": self.qst_number,
        }


@dataclass(frozen=True)
class Billable:
    """A thing an invoice can be raised against, as billing sees it.

    `subject_id` is the host's own id for it (an Order here). `tax_province`
    is the *buyer's* province — tax is destination-based, so it comes from
    the payer, not the seller.
    """

    subject_id: int
    description: str
    payer: PartyDetails
    tax_province: str | None
    lines: list[LineItem] = field(default_factory=list)
    payments: list[PaymentRecord] = field(default_factory=list)
    url: str | None = None  # host link, e.g. the order's page

    @property
    def subtotal(self) -> float:
        return sum(line.total for line in self.lines)

    @property
    def amount_paid(self) -> float:
        return sum(payment.amount for payment in self.payments)


@dataclass(frozen=True)
class InvoiceDocument:
    """Everything needed to render one invoice, with the money resolved.

    Built by `services.invoicing.document_for`, which decides whether the
    figures come from the frozen snapshot or from live data. Templates read
    this and never compute anything themselves.
    """

    number: str
    status: str
    display_status: str
    issued_date: date
    due_date: date | None
    notes: str | None
    issuer: IssuerDetails
    payer: PartyDetails
    subject_description: str
    subject_url: str | None
    lines: list[LineItem]
    payments: list[PaymentRecord]
    subtotal: float
    tax_lines: list[TaxLine]
    amount_paid: float
    is_frozen: bool
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
        """Float-tolerant: a cent of rounding shouldn't leave a document
        looking permanently unpaid."""
        return self.balance_due < 0.005

    @property
    def shows_payment_instructions(self) -> bool:
        return bool(
            self.issuer.payment_instructions
            and not self.is_settled
            and self.status != "void"
        )
