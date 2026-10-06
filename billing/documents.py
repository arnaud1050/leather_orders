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

from billing import config
from billing.tax import TaxLine

__all__ = [
    "DISCOUNT_KINDS", "Billable", "Branding", "Discount", "InvoiceDocument",
    "IssuerDetails", "LineItem", "PartyDetails", "PaymentRecord", "clean_color",
    "clean_discount", "format_address",
]

_HEX_DIGITS = frozenset("0123456789abcdef")


def clean_color(value) -> str | None:
    """`#rrggbb`, lower-cased — or None for anything else.

    Deliberately narrow: this is the only tenant-typed value that is ever
    written into a stylesheet, so nothing but seven known characters gets
    through. No names, no `rgb()`, no three-digit shorthand.
    """
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if len(value) == 7 and value[0] == "#" and set(value[1:]) <= _HEX_DIGITS:
        return value
    return None


@dataclass(frozen=True)
class Branding:
    """How a tenant's invoices look: which layout, its colour, and the
    logo.

    Read live at render time, never frozen onto an invoice — the freeze
    contract covers what a document says, not how it's dressed.

    The fields hold whatever was stored; the properties are what a template
    may use. A value that isn't a known layout or a clean colour resolves to
    the default, so a bad row can't break an export or reach the CSS.
    """

    template: str | None = None
    primary_color: str | None = None
    # The logo, already as a `data:` URI — built by the service from the
    # PNG bytes the host hands it (`set_logo_source`), so a template can
    # put it straight into an <img> and the renderer has nothing to fetch.
    logo_data_uri: str | None = None
    # The optional page footer. The text is printed as HTML (escaped),
    # never written into the stylesheet, so it needs no cleaning here.
    footer_enabled: bool = False
    footer_background: str | None = None
    footer_text_color: str | None = None
    footer_text: str | None = None

    @property
    def template_key(self) -> str:
        if self.template in config.INVOICE_TEMPLATES:
            return self.template
        return config.DEFAULT_INVOICE_TEMPLATE

    @property
    def primary(self) -> str:
        return clean_color(self.primary_color) or config.DEFAULT_PRIMARY_COLOR


    @property
    def has_footer(self) -> bool:
        return bool(self.footer_enabled)

    @property
    def footer_bg(self) -> str:
        return clean_color(self.footer_background) or config.DEFAULT_FOOTER_BACKGROUND

    @property
    def footer_fg(self) -> str:
        return clean_color(self.footer_text_color) or config.DEFAULT_FOOTER_TEXT_COLOR

    @property
    def on_primary(self) -> str:
        """Text colour that stays readable on the primary: white on a dark
        band, near-black on a pale one (WCAG relative luminance)."""
        def channel(offset: int) -> float:
            c = int(self.primary[offset:offset + 2], 16) / 255
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

        luminance = 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5)
        # 0.179 is where contrast against white and against black is equal.
        return "#1c1a17" if luminance > 0.179 else "#ffffff"


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
    # Where the seller is, for taxing goods collected in person. Not printed
    # separately (it's already in `address`) and not frozen: an issued
    # invoice carries its tax lines, so it never needs to recompute them.
    province: str | None = None

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


# "10% off" or "$20 off" — the two ways a whole-subject discount is given.
DISCOUNT_KINDS = ("percent", "amount")
# Long enough for "Returning client — spring promotion", short enough to
# sit on one line of the totals block.
DISCOUNT_LABEL_MAX = 60


@dataclass(frozen=True)
class Discount:
    """One discount on the whole subject, taken off before tax.

    A discount given at the time of sale lowers the amount sales tax is
    charged on, so it comes off the line items' total and `taxes_for` sees
    the net. Build one with `clean_discount`, which is what guarantees
    `kind` and `value` are usable.
    """

    kind: str  # one of DISCOUNT_KINDS
    value: float  # a percentage (10 = 10%) or a dollar amount
    label: str | None = None

    def amount_on(self, items_total: float) -> float:
        """The dollars taken off `items_total`, rounded to the cent so the
        printed lines add up, and never more than there is to discount."""
        raw = items_total * self.value / 100 if self.kind == "percent" else self.value
        return round(min(max(raw, 0.0), max(items_total, 0.0)), 2)

    @property
    def description(self) -> str:
        """How the discount line reads: its label, or "Discount", with the
        rate after it when it's a percentage — "Returning client (10%)"."""
        name = self.label or "Discount"
        if self.kind == "percent":
            return f"{name} ({self.value:g}%)"
        return name


def clean_discount(kind, value, label=None) -> Discount | None:
    """A usable `Discount`, or None for "no discount".

    Anything that can't be one — an unknown kind; a missing, zero, negative
    or non-finite value — is no discount rather than an error, so a host can
    hand over whatever it stored. A percentage is capped at 100.
    """
    if kind not in DISCOUNT_KINDS:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not (0 < value < float("inf")):
        return None
    if kind == "percent":
        value = min(value, 100.0)
    label = " ".join((label or "").split())[:DISCOUNT_LABEL_MAX] or None
    return Discount(kind, value, label)


@dataclass(frozen=True)
class Billable:
    """A thing an invoice can be raised against, as billing sees it.

    `subject_id` is the host's own id for it (an Order here). `tax_province`
    is the *buyer's* province — tax is destination-based, so it comes from
    the payer, not the seller. `picked_up` (collected at the seller's
    premises) and `outside_canada` (an export) change which province that
    is; see `tax.place_of_supply`.
    """

    subject_id: int
    description: str
    payer: PartyDetails
    tax_province: str | None
    lines: list[LineItem] = field(default_factory=list)
    payments: list[PaymentRecord] = field(default_factory=list)
    url: str | None = None  # host link, e.g. the order's page
    picked_up: bool = False
    outside_canada: bool = False
    discount: Discount | None = None

    @property
    def items_total(self) -> float:
        """The line items at full price, before any discount."""
        return sum(line.total for line in self.lines)

    @property
    def discount_amount(self) -> float:
        return self.discount.amount_on(self.items_total) if self.discount else 0.0

    @property
    def subtotal(self) -> float:
        """What tax is charged on: the line items less the discount."""
        return self.items_total - self.discount_amount

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
    untaxed: tuple[str, ...] = ()
    taxed_elsewhere: tuple[str, ...] = ()
    tax_province: str | None = None
    seller_province: str | None = None
    # Dollars taken off before tax, and how that line reads. `subtotal` is
    # already net of it; `items_total` adds it back for the "Subtotal" row.
    discount: float = 0.0
    discount_description: str | None = None

    @property
    def items_total(self) -> float:
        return self.subtotal + self.discount

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
