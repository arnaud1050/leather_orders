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

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = [
    "PROVINCE_TAXES", "PROVINCES", "TaxLine", "TaxRule", "normalize_province",
    "place_of_supply", "status_for", "taxes_elsewhere", "taxes_for",
    "unregistered_taxes",
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


# Everything that may be read as a province, normalised (see `_fold`). Built
# from PROVINCES so the codes and names can't drift from it, plus the spellings
# people actually type. Accents are folded, so "Québec" needs no entry of its
# own; these are the forms folding alone doesn't reach.
_PROVINCE_ALIASES = {
    "newfoundland": "NL",
    "labrador": "NL",
    "nfld": "NL",
    "pei": "PE",
    "p e i": "PE",
    "prince edward island": "PE",
    "quebec province": "QC",
    "british columbia": "BC",
    "b c": "BC",
    "northwest territories": "NT",
    "nwt": "NT",
    "n w t": "NT",
    "yukon territory": "YT",
    "ontario canada": "ON",
}


def _fold(value: str) -> str:
    """Lowercased, accent-stripped, single-spaced, punctuation dropped.

    So "Québec", "QUEBEC" and "quebec" are one key, and "P.E.I." reaches the
    "p e i" alias above.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = "".join(ch if ch.isalnum() else " " for ch in stripped)
    return " ".join(cleaned.split()).lower()


_PROVINCE_LOOKUP: dict[str, str] = {
    **{_fold(code): code for code in PROVINCES},
    **{_fold(name): code for code, name in PROVINCES.items()},
    **{_fold(alias): code for alias, code in _PROVINCE_ALIASES.items()},
}


def normalize_province(value: str | None) -> str | None:
    """A two-letter province code, or None if this isn't one.

    For turning whatever a human (or a web form) wrote into something
    `taxes_for` can use — "Quebec", "québec", "QC" and "P.E.I." all resolve.

    **Anything unrecognised returns None rather than a best guess**, and that
    is the whole point of the function. A province column picks the tax rate,
    so storing a raw "Quebec" matches no key in `PROVINCE_TAXES` and charges
    the client nothing — permanently, once an invoice is issued and frozen.
    (The column is declared two characters wide, which SQLite does not
    enforce; on the Postgres or MySQL this app is meant to move to, the same
    value truncates or is rejected outright. Wrong either way, differently.)
    "Out of country", a postal code, a full sentence and an empty string all
    land here and all return None, leaving the field blank for a person to
    answer.

    Matching is exact against the folded table on purpose: no prefix or
    fuzzy matching, which is what would let "Nova Scotia office" or
    "not in Canada" resolve to somewhere real.
    """
    folded = _fold(value or "")
    if not folded:
        return None
    return _PROVINCE_LOOKUP.get(folded)


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


def place_of_supply(
    buyer_province: str | None,
    seller_province: str | None,
    *,
    picked_up: bool = False,
    outside_canada: bool = False,
) -> str | None:
    """The province whose taxes apply, or None when none does.

    Goods shipped to the buyer are taxed where they're delivered — the
    buyer's province. Goods the buyer collects in person are delivered at
    the seller's premises, so they're taxed in the *seller's* province,
    wherever the buyer lives (and even if the buyer lives abroad). Goods
    shipped out of the country are exported: no Canadian sales tax.
    """
    if picked_up:
        return seller_province
    if outside_canada:
        return None
    return buyer_province


def taxes_elsewhere(
    province: str | None,
    seller_province: str | None,
    charged: list[TaxLine],
) -> list[str]:
    """Labels of charged PST/RST taxes belonging to a province the seller
    isn't in — e.g. `["PST"]` for BC PST charged by a Saskatchewan studio.

    One `pst_number` gates BC PST, Saskatchewan PST and Manitoba RST alike,
    so a studio registered in one of them charges all three. Selling into
    another province can require registering there too, so it may be right;
    this only flags it for a person to confirm. Unknown when the seller's
    own province isn't on file, so nothing is flagged then.
    """
    if not seller_province or province == seller_province:
        return []
    charged_labels = {line.label for line in charged}
    return [
        rule.label
        for rule in PROVINCE_TAXES.get(province or "", ())
        if rule.registration_field == "pst_number" and rule.label in charged_labels
    ]


def unregistered_taxes(
    province: str | None,
    registrations: Mapping[str, str | None] | None,
) -> list[str]:
    """Labels of taxes this province levies that weren't charged because the
    seller holds no registration for them — e.g. `["PST"]` for a BC buyer
    when no PST number is on file.

    `status_for` can't say this: it reports "ok" whenever *any* tax was
    charged, so a BC order billed GST only looks fine. Callers show this as
    a warning; it never changes the amounts.
    """
    held = registrations or {}
    return [
        rule.label
        for rule in PROVINCE_TAXES.get(province or "", ())
        if not held.get(rule.registration_field)
    ]


def status_for(
    province: str | None,
    registrations: Mapping[str, str | None] | None,
    charged: list[TaxLine],
    *,
    picked_up: bool = False,
    outside_canada: bool = False,
) -> str:
    """Why nothing was charged, when nothing was.

    `province` is the one `place_of_supply` resolved, and the two flags are
    the ones passed to it. `"ok"` means tax was calculated normally;
    `"outside_canada"` means none is owed (an export). The rest are reasons
    a host can show the user instead of silently billing zero:
    `no_seller_province` (picked up, but the seller's province isn't on
    file), `no_buyer_province`, `unknown_province`, `not_registered`.
    """
    if charged:
        return "ok"
    if picked_up and not (province or "").strip():
        return "no_seller_province"
    if outside_canada and not picked_up:
        return "outside_canada"
    if not (province or "").strip():
        return "no_buyer_province"
    if province not in PROVINCE_TAXES:
        return "unknown_province"
    return "not_registered"
