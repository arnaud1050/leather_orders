"""
The two Settings POST routes that write the invoice letterhead:
`/settings/company` (name, address, GST/PST/QST/NEQ) and
`/settings/invoicing` (prefix, payment instructions).

These feed tax gating and invoice numbering yet had no route test — the
gap billing/REQUIREMENTS.md P10 names ("host-side form validation of
province/prefix has no test"). The letterhead itself lives on the billing
module's BillingProfile, not on Company, so the assertions read it back
through that module rather than off the tenant row.
"""

from billing.services import invoicing
from models import Company, db


def _profile(company):
    return invoicing.profile_for(company.id)


# --- /settings/company -----------------------------------------------------

def test_update_company_writes_name_and_every_letterhead_field(logged_in, company):
    logged_in.post("/settings/company", data={
        "name": "By Madame",
        "street": "12 rue Saint-Paul",
        "city": "Montreal",
        "province": "qc",
        "postal_code": "h2y 1g3",
        "gst_number": "111 RT0001",
        "pst_number": "PST-222",
        "qst_number": "QST-333",
        "neq": "1234567890",
    })

    assert db.session.get(Company, company.id).name == "By Madame"
    profile = _profile(company)
    assert profile.display_name == "By Madame"
    assert profile.street == "12 rue Saint-Paul"
    assert profile.city == "Montreal"
    assert profile.province == "QC"           # upper-cased
    assert profile.postal_code == "H2Y 1G3"   # upper-cased
    assert profile.gst_number == "111 RT0001"
    assert profile.pst_number == "PST-222"
    assert profile.qst_number == "QST-333"
    assert profile.neq == "1234567890"


def test_update_company_stores_a_valid_province_upper_cased(logged_in, company):
    logged_in.post("/settings/company", data={"name": "By Monsieur", "province": "bc"})

    assert _profile(company).province == "BC"


def test_update_company_rejects_an_unknown_province(logged_in, company):
    """An unrecognised code must clear the province, never guess one — the
    province decides the tax charged, so a silent guess would misprice
    invoices. Mirrors the client-address rule in test_core_app.py."""
    invoicing.update_profile(company.id, company.name, province="QC")
    db.session.commit()

    logged_in.post("/settings/company", data={"name": "By Monsieur", "province": "ZZ"})

    assert _profile(company).province is None


def test_update_company_blank_name_leaves_the_existing_name(logged_in, company):
    logged_in.post("/settings/company", data={"name": "   "})

    assert db.session.get(Company, company.id).name == "By Monsieur"


def test_update_company_blank_fields_clear_the_letterhead(logged_in, company):
    invoicing.update_profile(
        company.id, company.name, street="Old St", gst_number="OLD RT0001")
    db.session.commit()

    logged_in.post("/settings/company", data={
        "name": "By Monsieur", "street": "", "gst_number": "",
    })

    profile = _profile(company)
    assert profile.street is None
    assert profile.gst_number is None


def test_update_company_requires_a_login(app):
    response = app.test_client().post("/settings/company", data={"name": "Hijack"})

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


# --- /settings/invoicing ---------------------------------------------------

def test_update_invoicing_sets_prefix_and_payment_instructions(logged_in, company):
    logged_in.post("/settings/invoicing", data={
        "invoice_prefix": "atl",
        "payment_instructions": "E-transfer to pay@example.com",
    })

    profile = _profile(company)
    assert profile.invoice_prefix == "ATL"  # upper-cased
    assert profile.payment_instructions == "E-transfer to pay@example.com"


def test_update_invoicing_truncates_a_long_prefix_to_ten_chars(logged_in, company):
    logged_in.post("/settings/invoicing", data={"invoice_prefix": "ABCDEFGHIJKLMNOP"})

    assert _profile(company).invoice_prefix == "ABCDEFGHIJ"


def test_update_invoicing_blank_prefix_leaves_the_existing_one(logged_in, company):
    logged_in.post("/settings/invoicing", data={"invoice_prefix": "  "})

    assert _profile(company).invoice_prefix == "BM"  # from the company fixture


def test_update_invoicing_blank_payment_instructions_clears_them(logged_in, company):
    invoicing.update_profile(company.id, company.name, payment_instructions="Cash only")
    db.session.commit()

    logged_in.post("/settings/invoicing", data={"payment_instructions": ""})

    assert _profile(company).payment_instructions is None


def test_update_invoicing_requires_a_login(app):
    response = app.test_client().post(
        "/settings/invoicing", data={"invoice_prefix": "ZZ"})

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
