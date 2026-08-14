"""
Sales tax: the rate table, what gets charged, and what deliberately isn't.

The expected rates below are written out a second time rather than read
from `PROVINCE_TAXES` — a test that imports the constant it's checking
passes no matter what the constant says. They are pinned against the CRA's
own published table ("Charge and collect the GST/HST — which rate to
charge"), verified 2026-07-30. If the CRA changes a rate, this file is
supposed to fail; update it *from the CRA*, not from the code.

The other half of the file covers the rule that decides whether a tax
applies at all: a company only charges a tax it holds the registration
for, and tax follows the *client's* province, not the studio's.
"""

import pytest

from billing.tax import PROVINCE_TAXES, TaxLine, status_for, taxes_for
from billing.services import invoicing
from models import Client, Order, OrderLine, db

# Registrations as `taxes_for` wants them: a plain mapping, no ORM. A
# seller holding all three, so these tests exercise the rates rather than
# the gating (which has its own tests below).
ALL_REGISTRATIONS = {
    "gst_number": "123456789 RT0001",
    "pst_number": "PST-1234",
    "qst_number": "1234567890 TQ0001",
}

# (province, GST-or-HST rate, provincial rate or None) — straight off the
# CRA table. "N/A"/"0%" in the PST column both mean "no provincial tax we
# would ever charge", so both are None here.
CRA_RATES = [
    ("AB", 0.05, None),
    ("BC", 0.05, 0.07),
    ("MB", 0.05, 0.07),
    ("NB", 0.15, None),
    ("NL", 0.15, None),
    ("NT", 0.05, None),
    ("NS", 0.14, None),      # reduced from 15% on 2025-04-01
    ("NU", 0.05, None),
    ("ON", 0.13, None),
    ("PE", 0.15, None),
    ("QC", 0.05, 0.09975),
    ("SK", 0.05, 0.06),
    ("YT", 0.05, None),
]

FEDERAL_LABELS = {"GST", "HST"}


def federal_rule(province):
    rules = [r for r in PROVINCE_TAXES[province] if r.label in FEDERAL_LABELS]
    assert len(rules) == 1, f"{province} should have exactly one federal rule"
    return rules[0]


def provincial_rule(province):
    rules = [r for r in PROVINCE_TAXES[province] if r.label not in FEDERAL_LABELS]
    assert len(rules) <= 1, f"{province} should charge at most one provincial tax"
    return rules[0] if rules else None


# --- The rate table, pinned against the CRA -------------------------------

def test_every_province_and_territory_is_covered():
    assert set(PROVINCE_TAXES) == {p for p, _, _ in CRA_RATES}
    assert len(PROVINCE_TAXES) == 13


@pytest.mark.parametrize("province,expected,_provincial", CRA_RATES)
def test_gst_hst_rate_matches_the_cra(province, expected, _provincial):
    assert federal_rule(province).rate == pytest.approx(expected)


@pytest.mark.parametrize("province,_federal,expected", CRA_RATES)
def test_provincial_rate_matches_the_cra(province, _federal, expected):
    rule = provincial_rule(province)
    if expected is None:
        assert rule is None, f"{province} should charge no provincial tax"
    else:
        assert rule is not None, f"{province} should charge a provincial tax"
        assert rule.rate == pytest.approx(expected)


def test_nova_scotia_is_the_reduced_rate():
    """The one rate that changed recently, and the one most likely to be
    stale in anyone's memory. 15% here would over-bill every NS client."""
    assert federal_rule("NS").rate == pytest.approx(0.14)


@pytest.mark.parametrize("province", ["NB", "NL", "NS", "ON", "PE"])
def test_hst_provinces_charge_one_combined_tax(province):
    """HST replaces GST rather than stacking on top of it — a second line
    would double-charge the federal portion."""
    rules = PROVINCE_TAXES[province]
    assert len(rules) == 1
    assert rules[0].label == "HST"


@pytest.mark.parametrize("province", ["NB", "NL", "NS", "ON", "PE"])
def test_hst_hangs_off_the_federal_registration(province):
    """HST is collected under the GST/HST registration, so a studio with a
    GST number charges it — there's no separate provincial account."""
    assert PROVINCE_TAXES[province][0].registration_field == "gst_number"


def test_manitoba_uses_its_own_name_for_the_tax():
    """Manitoba calls it RST. The CRA lists it in the PST column, but the
    label is what gets printed on the invoice."""
    assert provincial_rule("MB").label == "RST"


def test_quebec_qst_is_gated_on_the_qst_registration():
    assert provincial_rule("QC").label == "QST"
    assert provincial_rule("QC").registration_field == "qst_number"


@pytest.mark.parametrize("province", ["BC", "SK", "MB"])
def test_pst_provinces_share_one_registration_field(province):
    """One pst_number covers BC/SK/MB — a seller registers in at most one."""
    assert provincial_rule(province).registration_field == "pst_number"


# --- What actually gets charged -------------------------------------------

@pytest.fixture
def registered(company):
    """A seller registered for everything, so the order-level tests below
    exercise calculation rather than a missing number. The letterhead lives
    in the billing module now, so it's set through its API."""
    invoicing.update_profile(company.id, company.name, **ALL_REGISTRATIONS)
    db.session.flush()
    return company


def test_quebec_client_is_charged_gst_and_qst(registered):
    lines = taxes_for("QC", ALL_REGISTRATIONS, 1000.0)
    assert [(t.label, t.amount) for t in lines] == [("GST", 50.0), ("QST", 99.75)]


def test_ontario_client_is_charged_one_hst_line(registered):
    assert [(t.label, t.amount) for t in taxes_for("ON", ALL_REGISTRATIONS, 1000.0)] == [
        ("HST", 130.0)
    ]


def test_alberta_client_is_charged_gst_only(registered):
    assert [t.label for t in taxes_for("AB", ALL_REGISTRATIONS, 100.0)] == ["GST"]


def test_a_tax_is_not_charged_without_its_registration():
    """The small-supplier / not-registered-here case. A BC buyer of a
    seller with no PST number pays GST but no PST."""
    held = {"gst_number": "123456789 RT0001", "pst_number": None}
    assert [t.label for t in taxes_for("BC", held, 100.0)] == ["GST"]


def test_a_seller_registered_for_nothing_charges_nothing():
    assert taxes_for("QC", {}, 100.0) == []
    assert taxes_for("QC", None, 100.0) == []


def test_status_explains_why_nothing_was_charged():
    assert status_for("QC", ALL_REGISTRATIONS, taxes_for("QC", ALL_REGISTRATIONS, 10)) == "ok"
    assert status_for(None, ALL_REGISTRATIONS, []) == "no_buyer_province"
    assert status_for("ZZ", ALL_REGISTRATIONS, []) == "unknown_province"
    assert status_for("QC", {}, []) == "not_registered"


def test_no_province_charges_nothing(registered):
    """Better to visibly charge nothing than to guess a rate."""
    assert taxes_for(None, ALL_REGISTRATIONS, 100.0) == []
    assert taxes_for("", ALL_REGISTRATIONS, 100.0) == []


def test_an_unrecognised_province_charges_nothing(registered):
    assert taxes_for("ZZ", ALL_REGISTRATIONS, 100.0) == []


def test_amounts_are_rounded_to_the_cent(registered):
    """9.975% of 850 is 84.7875 — it has to land on a real money value or
    the invoice total won't match the sum of its own lines."""
    qst = [t for t in taxes_for("QC", ALL_REGISTRATIONS, 850.0) if t.label == "QST"][0]
    assert qst.amount == 84.79


def test_each_tax_is_computed_on_the_subtotal_not_compounded(registered):
    """QST applies to the pre-tax subtotal, not to subtotal+GST."""
    lines = taxes_for("QC", ALL_REGISTRATIONS, 100.0)
    assert [t.amount for t in lines] == [5.0, 9.98]  # not 9.975% of 105


def test_rate_percent_is_printable():
    assert TaxLine("GST", 0.05, 1.0).rate_percent == "5"
    assert TaxLine("QST", 0.09975, 1.0).rate_percent == "9.975"
    assert TaxLine("HST", 0.13, 1.0).rate_percent == "13"


# --- Order-level behaviour ------------------------------------------------

def make_order(client_row, unit_price=100.0):
    from datetime import date

    row = Order(
        client_id=client_row.id, item="Test order",
        start=date(2026, 7, 1), due=date(2026, 7, 15), status="confirmed",
    )
    db.session.add(row)
    db.session.flush()
    db.session.add(OrderLine(
        order_id=row.id, description="Thing", quantity=1, unit_price=unit_price,
    ))
    db.session.flush()
    return row


def test_order_total_is_subtotal_plus_tax(registered, client_record):
    order = make_order(client_record, 1000.0)          # client_record is QC
    assert order.subtotal == 1000.0
    assert order.tax_total == pytest.approx(149.75)
    assert order.total == pytest.approx(1149.75)


def test_tax_follows_the_client_province_not_the_company(registered, client_record):
    """The whole point of destination-based tax: the studio is in Quebec,
    the client is in Ontario, so Ontario's rate applies."""
    registered.province = "QC"
    client_record.province = "ON"
    db.session.flush()
    order = make_order(client_record, 100.0)
    assert [t.label for t in order.tax_lines] == ["HST"]


def test_two_clients_in_different_provinces_are_taxed_differently(registered, company):
    alberta = Client(company_id=company.id, first_name="A", last_name="B", province="AB")
    ontario = Client(company_id=company.id, first_name="C", last_name="D", province="ON")
    db.session.add_all([alberta, ontario])
    db.session.flush()
    assert make_order(alberta, 100.0).total == pytest.approx(105.0)
    assert make_order(ontario, 100.0).total == pytest.approx(113.0)


def test_balance_due_is_tax_inclusive(registered, client_record):
    """A deposit covering the pre-tax price does not settle the order."""
    from datetime import date

    from models import Payment

    order = make_order(client_record, 1000.0)
    db.session.add(Payment(
        order_id=order.id, amount=1000.0, paid_date=date(2026, 7, 1), method="cash",
    ))
    db.session.flush()
    assert order.balance_due == pytest.approx(149.75)
    assert not order.is_settled


def test_lifetime_value_is_tax_inclusive(registered, client_record):
    make_order(client_record, 1000.0)
    assert client_record.lifetime_value == pytest.approx(1149.75)


# --- tax_status: why there's no tax, when there isn't ---------------------

def test_tax_status_is_ok_when_tax_applies(registered, client_record):
    assert make_order(client_record).tax_status == "ok"


def test_tax_status_flags_a_client_with_no_province(registered, company):
    row = Client(company_id=company.id, first_name="No", last_name="Address")
    db.session.add(row)
    db.session.flush()
    assert make_order(row).tax_status == "no_buyer_province"


def test_tax_status_flags_an_unrecognised_province(registered, company):
    row = Client(company_id=company.id, first_name="Bad", last_name="Province",
                 province="ZZ")
    db.session.add(row)
    db.session.flush()
    assert make_order(row).tax_status == "unknown_province"


def test_tax_status_flags_a_missing_registration(company, client_record):
    """Distinct from the others: the province is known and taxable, the
    studio just isn't registered to collect there."""
    invoicing.update_profile(company.id, company.name,
                             gst_number=None, qst_number=None, pst_number=None)
    db.session.flush()
    assert make_order(client_record).tax_status == "not_registered"


def test_a_taxless_order_still_totals_its_subtotal(company, client_record):
    invoicing.update_profile(company.id, company.name,
                             gst_number=None, qst_number=None, pst_number=None)
    db.session.flush()
    order = make_order(client_record, 100.0)
    assert order.tax_lines == []
    assert order.total == 100.0
