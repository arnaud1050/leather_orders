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

import re

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
SECTION_ANCHOR = "/settings/invoicing#invoice-appearance"


def test_update_appearance_saves_the_layout_and_the_colour(logged_in, company):
    response = logged_in.post(APPEARANCE, data={
        "invoice_template": "banded",
        "primary_color": "#1F4E79",
    })

    assert response.status_code == 302
    profile = _profile(company)
    assert profile.invoice_template == "banded"
    assert profile.primary_color == "#1f4e79"   # normalised to lower case


def test_saving_returns_to_the_appearance_section(logged_in, company):
    """Not the top of the page: the section is near the bottom, and landing
    above it hid both the result and any refusal."""
    response = logged_in.post(APPEARANCE, data={"invoice_template": "banded"})

    assert response.headers["Location"].endswith(SECTION_ANCHOR)


def test_update_appearance_refuses_a_colour_that_is_not_plain_hex(logged_in, company):
    """It ends up in a stylesheet, so nothing but #rrggbb is stored."""
    logged_in.post(APPEARANCE, data={"primary_color": "#1f4e79"})

    response = logged_in.post(APPEARANCE, data={
        "invoice_template": "banded",
        "primary_color": "red;} body{display:none",
    }, follow_redirects=True)

    profile = _profile(company)
    assert profile.primary_color == "#1f4e79"     # left as it was
    assert profile.invoice_template == "banded"   # the valid field still saved
    body = response.get_data(as_text=True)
    assert "Not saved: the colour" in body


def test_a_refusal_is_shown_inside_the_appearance_section(logged_in, company):
    response = logged_in.post(
        APPEARANCE, data={"primary_color": "nope"}, follow_redirects=True)

    body = response.get_data(as_text=True)
    section = body.split('id="invoice-appearance"')[1]
    assert "Not saved" in section
    assert "Not saved" not in body.split('id="invoice-appearance"')[0]


def test_update_appearance_refuses_an_unknown_layout(logged_in, company):
    logged_in.post(APPEARANCE, data={"invoice_template": "banded"})

    response = logged_in.post(
        APPEARANCE, data={"invoice_template": "fancy"}, follow_redirects=True)

    assert _profile(company).invoice_template == "banded"
    assert "Not saved: the layout" in response.get_data(as_text=True)


def test_update_appearance_leaves_alone_what_the_form_did_not_send(logged_in, company):
    logged_in.post(APPEARANCE, data={
        "invoice_template": "banded", "primary_color": "#1f4e79",
    })

    logged_in.post(APPEARANCE, data={"primary_color": "#222222"})

    profile = _profile(company)
    assert profile.invoice_template == "banded"
    assert profile.primary_color == "#222222"


def test_there_is_no_secondary_colour_any_more(logged_in, company):
    logged_in.post(APPEARANCE, data={"secondary_color": "#8a6d3b"})

    body = logged_in.get("/settings/invoicing").get_data(as_text=True)
    assert "secondary" not in body.lower().split('id="invoice-appearance"')[1] \
        .replace("btn-secondary", "")
    assert not hasattr(_profile(company), "secondary_color")


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


def _section(client):
    body = client.get("/settings/invoicing").get_data(as_text=True)
    return body.split('id="invoice-appearance"')[1].split("</section>")[0]


def test_the_layouts_are_radio_buttons_named_classic_and_banded(logged_in, company):
    section = _section(logged_in)

    assert "<select" not in section
    radios = re.findall(r'<input type="radio" name="invoice_template" value="(\w+)"', section)
    assert radios == ["classic", "banded"]
    assert ">Classic<" in section
    assert ">Banded<" in section


def test_each_layout_has_a_thumbnail(logged_in, company):
    section = _section(logged_in)

    assert 'class="layout-thumb layout-thumb--classic"' in section
    assert 'class="layout-thumb layout-thumb--banded"' in section


def test_the_saved_layout_is_the_checked_one(logged_in, company):
    logged_in.post(APPEARANCE, data={"invoice_template": "banded"})

    section = _section(logged_in)

    checked = re.findall(r'value="(\w+)" class="layout-option__input"\s+checked', section)
    assert checked == ["banded"]


def test_the_page_shows_the_saved_colour(logged_in, company):
    logged_in.post(APPEARANCE, data={"primary_color": "#1f4e79"})

    section = _section(logged_in)

    assert 'name="primary_color" value="#1f4e79"' in section
    # Drives the thumbnails and the logo tile, with readable text on it.
    assert "--look-primary: #1f4e79" in logged_in.get(
        "/settings/invoicing").get_data(as_text=True)
    assert "--look-on-primary: #ffffff" in logged_in.get(
        "/settings/invoicing").get_data(as_text=True)


def test_the_page_shows_the_defaults_before_anything_is_chosen(logged_in, company):
    section = _section(logged_in)

    checked = re.findall(r'value="(\w+)" class="layout-option__input"\s+checked', section)
    assert checked == ["classic"]
    assert 'name="primary_color" value="#1c1a17"' in section


def test_the_accent_colour_is_a_swatch_that_opens_a_picker(logged_in, company):
    section = _section(logged_in)

    assert ">Accent colour<" in section
    assert re.search(r'<button type="button" class="color-picker__current" data-color-toggle\s+'
                     r'aria-expanded="false" aria-controls="invoice-colour-panel"', section)
    assert 'id="invoice-colour-panel" data-color-panel hidden' in section
    # No preset swatches, and no browser-native picker: the page draws its own.
    assert "data-color=" not in section
    assert 'type="color"' not in section


def test_the_introduction_is_short(logged_in, company):
    section = _section(logged_in)
    intro = section.split('<p class="detail-note">')[1].split("</p>")[0]

    assert " ".join(intro.split()) == (
        "How your invoices look when you download them as a PDF. A change here "
        "applies to every invoice straight away, including ones already sent.")


def test_the_logo_comes_before_the_layout(logged_in, company):
    section = _section(logged_in)

    assert section.index(">Logo<") < section.index(">Layout<") < section.index(">Accent colour<")


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
