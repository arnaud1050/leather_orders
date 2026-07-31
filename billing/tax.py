"""
Canadian sales tax rates and the rule for what gets charged.

**This file imports nothing.** No Flask, no SQLAlchemy, no models — it is
plain data plus one pure function, so it can be lifted into any project
(or called from a script, or a notebook) without dragging an ORM behind
it. Keep it that way: anything here that needs a database belongs in
`billing/services/`.

Rates verified 2026-07-30 against the CRA's published table ("Charge and
collect the GST/HST — which rate to charge"). `tests/test_tax.py` pins
every one of them against an independently written copy of that table, so
a typo here fails the suite rather than quietly mis-billing someone.
Nova Scotia is 14% (reduced from 15% on 2025-04-01) — check that one first
if these ever look stale. Not tax advice; re-confirm before a period
closes.
"""

from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "PROVINCE_TAXES", "PROVINCES", "TaxLine", "TaxRule", "status_for", "taxes_for",
]


@dataclass(frozen=True)
class TaxRule:
    """One tax that a province levies.

    `registration_field` is the key the seller's registration numbers are
    looked up under: a tax is only charged when the seller actually holds
    that registration, which is what makes a small supplier with no GST
    number charge no GST, and a studio that never registered in BC charge
    no BC PST. It falls out of the data instead of needing a separate
    "do we charge tax" switch.
    """

    label: str
    rate: float
    registration_field: str


_GST = TaxRule("GST", 0.05, "gst_number")

# Two rules decide what a buyer pays:
#   1. Their province picks the row (destination-based, which is how
#      place-of-supply works for goods shipped to a customer).
#   2. The seller must hold the matching registration — see TaxRule.
#
# HST replaces GST rather than stacking on it, and is collected under the
# federal GST/HST registration, so it hangs off `gst_number`.
PROVINCE_TAXES: dict[str, tuple[TaxRule, ...]] = {
    "AB": (_GST,),
    "BC": (_GST, TaxRule("PST", 0.07, "pst_number")),
    "MB": (_GST, TaxRule("RST", 0.07, "pst_number")),
    "NB": (TaxRule("HST", 0.15, "gst_number"),),
    "NL": (TaxRule("HST", 0.15, "gst_number"),),
    "NS": (TaxRule("HST", 0.14, "gst_number"),),
    "NT": (_GST,),
    "NU": (_GST,),
    "ON": (TaxRule("HST", 0.13, "gst_number"),),
    "PE": (TaxRule("HST", 0.15, "gst_number"),),
    "QC": (_GST, TaxRule("QST", 0.09975, "qst_number")),
    "SK": (_GST, TaxRule("PST", 0.06, "pst_number")),
    "YT": (_GST,),
}

# Codes to full names, for the dropdowns a host application will need.
# Ordered the way Canada Post lists them.
PROVINCES: dict[str, str] = {
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "NT": "Northwest Territories",
    "NS": "Nova Scotia",
    "NU": "Nunavut",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "YT": "Yukon",
}


@dataclass(frozen=True)
class TaxLine:
    """One tax as it appears on a document: name, rate applied, money."""

    label: str
    rate: float
    amount: float

    @property
    def rate_percent(self) -> str:
        """Rate for display, without trailing zeros (5%, 9.975%)."""
        return f"{self.rate * 100:.3f}".rstrip("0").rstrip(".")


def taxes_for(
    province: str | None,
    registrations: Mapping[str, str | None] | None,
    subtotal: float,
) -> list[TaxLine]:
    """Taxes owed on `subtotal` for a buyer in `province`.

    `registrations` maps a TaxRule's `registration_field` to the seller's
    number for it; a missing or empty value means that tax isn't charged.

    Returns an empty list when the province is unknown or unrecognised —
    charging nothing visibly beats guessing a rate. Callers should say so
    rather than treating it as "no tax applies"; the host app surfaces it
    through `Order.tax_status`.

    Each tax is computed on the pre-tax subtotal, never compounded on
    another tax, and rounded to the cent so a document's total matches the
    sum of its own printed lines.
    """
    held = registrations or {}
    return [
        TaxLine(rule.label, rule.rate, round(subtotal * rule.rate, 2))
        for rule in PROVINCE_TAXES.get(province or "", ())
        if held.get(rule.registration_field)
    ]


def status_for(
    province: str | None,
    registrations: Mapping[str, str | None] | None,
    charged: list[TaxLine],
) -> str:
    """Why nothing was charged, when nothing was.

    `"ok"` means tax was calculated normally. The other three are reasons a
    host can show the user instead of silently billing zero:
    `no_buyer_province`, `unknown_province`, `not_registered`.
    """
    if charged:
        return "ok"
    if not (province or "").strip():
        return "no_buyer_province"
    if province not in PROVINCE_TAXES:
        return "unknown_province"
    return "not_registered"
