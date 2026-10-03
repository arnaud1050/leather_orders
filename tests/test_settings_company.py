"""
The Settings POST routes that write the billing profile:
`/settings/company` (name, address, GST/PST/QST/NEQ),
`/settings/invoicing` (prefix, payment instructions) and
`/settings/invoicing/appearance` (PDF layout and colours).

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


# --- /settings/invoicing/appearance ----------------------------------------

APPEARANCE = "/settings/invoicing/appearance"


def test_update_appearance_saves_the_layout_and_both_colours(logged_in, company):
    response = logged_in.post(APPEARANCE, data={
        "invoice_template": "banded",
        "primary_color": "#1F4E79",
        "secondary_color": "#8a6d3b",
    })

    assert response.status_code == 302
    profile = _profile(company)
    assert profile.invoice_template == "banded"
    assert profile.primary_color == "#1f4e79"   # normalised to lower case
    assert profile.secondary_color == "#8a6d3b"


def test_update_appearance_refuses_a_colour_that_is_not_plain_hex(logged_in, company):
    """These end up in a stylesheet, so nothing but #rrggbb is stored."""
    logged_in.post(APPEARANCE, data={"primary_color": "#1f4e79"})

    response = logged_in.post(APPEARANCE, data={
        "primary_color": "red;} body{display:none",
        "secondary_color": "#8a6d3b",
    }, follow_redirects=True)

    profile = _profile(company)
    assert profile.primary_color == "#1f4e79"    # left as it was
    assert profile.secondary_color == "#8a6d3b"  # the valid one still saved
    body = response.get_data(as_text=True)
    assert "Not saved" in body
    assert "primary colour" in body


def test_update_appearance_refuses_an_unknown_layout(logged_in, company):
    logged_in.post(APPEARANCE, data={"invoice_template": "banded"})

    response = logged_in.post(
        APPEARANCE, data={"invoice_template": "fancy"}, follow_redirects=True)

    assert _profile(company).invoice_template == "banded"
    assert "Not saved" in response.get_data(as_text=True)


def test_update_appearance_leaves_alone_what_the_form_did_not_send(logged_in, company):
    logged_in.post(APPEARANCE, data={
        "invoice_template": "banded",
        "primary_color": "#1f4e79",
        "secondary_color": "#8a6d3b",
    })

    logged_in.post(APPEARANCE, data={"secondary_color": "#222222"})

    profile = _profile(company)
    assert profile.invoice_template == "banded"
    assert profile.primary_color == "#1f4e79"
    assert profile.secondary_color == "#222222"


def test_update_appearance_does_not_touch_the_letterhead(logged_in, company):
    logged_in.post(APPEARANCE, data={"invoice_template": "banded"})

    profile = _profile(company)
    assert profile.invoice_prefix == "BM"
    assert profile.gst_number == "123 RT0001"
    assert db.session.get(Company, company.id).name == "By Monsieur"


def test_update_appearance_is_per_company(logged_in, company, other_company):
    logged_in.post(APPEARANCE, data={
        "invoice_template": "banded", "primary_color": "#1f4e79",
    })

    theirs = _profile(other_company)
    assert theirs.invoice_template is None
    assert theirs.primary_color is None


def test_update_appearance_requires_a_login(app):
    response = app.test_client().post(APPEARANCE, data={"invoice_template": "banded"})

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_the_settings_page_shows_the_saved_appearance(logged_in, company):
    logged_in.post(APPEARANCE, data={
        "invoice_template": "banded",
        "primary_color": "#1f4e79",
        "secondary_color": "#8a6d3b",
    })

    body = logged_in.get("/settings/invoicing").get_data(as_text=True)

    assert "Invoice appearance" in body
    assert '<option value="banded" selected>' in body
    assert 'name="primary_color" value="#1f4e79"' in body
    assert 'name="secondary_color" value="#8a6d3b"' in body


def test_the_settings_page_shows_the_defaults_before_anything_is_chosen(
    logged_in, company
):
    body = logged_in.get("/settings/invoicing").get_data(as_text=True)

    assert '<option value="classic" selected>' in body
    assert 'name="primary_color" value="#1c1a17"' in body
    assert 'name="secondary_color" value="#7e7a78"' in body


def test_the_preview_link_appears_only_where_a_pdf_can_be_rendered(
    logged_in, company, monkeypatch
):
    from billing import pdf

    monkeypatch.setattr(pdf, "available", lambda: True)
    assert "/invoices/preview.pdf" in logged_in.get(
        "/settings/invoicing").get_data(as_text=True)

    monkeypatch.setattr(pdf, "available", lambda: False)
    assert "/invoices/preview.pdf" not in logged_in.get(
        "/settings/invoicing").get_data(as_text=True)
