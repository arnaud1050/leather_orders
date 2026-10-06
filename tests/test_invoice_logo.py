"""
The logo on the invoice: how it reaches the PDF and what each layout does
with it (billing/REQUIREMENTS.md L7, L8).

The logo itself — what an upload has to be, where it's kept, who sees it —
is the studio's brand now, tested in `tests/test_brand.py`. Billing only
receives PNG bytes from the source the host registers
(`invoicing.set_logo_source`, wired to `brand.logo_png` in app.py) and
embeds them.
"""

import base64
import io
import os

import pytest
from PIL import Image

import brand
from billing import config, pdf
from billing.documents import Branding
from billing.services import invoicing
from tests.test_invoice_pdf import sample_doc


def image_bytes(fmt="PNG", size=(300, 100), mode="RGB", color=(31, 78, 121)):
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, format=fmt)
    return out.getvalue()


# --- Reaching the PDF (L7) ----------------------------------------------------

def test_branding_carries_the_logo_embedded(company):
    brand.set_logo(company.id, image_bytes())

    uri = invoicing.branding_for(company.id).logo_data_uri

    assert uri.startswith("data:image/png;base64,")
    embedded = base64.b64decode(uri.split(",", 1)[1])
    assert embedded == open(brand.logo_path(company.id), "rb").read()


def test_branding_without_a_logo_has_none(company):
    assert invoicing.branding_for(company.id).logo_data_uri is None


def test_a_logo_whose_file_has_gone_is_simply_left_off(company):
    """An invoice without its logo is still an invoice — a missing file
    must not stop an export."""
    brand.set_logo(company.id, image_bytes())
    os.remove(brand.logo_path(company.id))

    assert invoicing.branding_for(company.id).logo_data_uri is None


def test_each_company_gets_its_own_logo(company, other_company):
    brand.set_logo(company.id, image_bytes())

    assert invoicing.branding_for(other_company.id).logo_data_uri is None


def test_without_a_source_invoices_print_no_logo(company, monkeypatch):
    """Billing owns no logo: unwired, it simply prints none."""
    brand.set_logo(company.id, image_bytes())
    monkeypatch.setattr(invoicing, "_logo_source", None)

    assert invoicing.branding_for(company.id).logo_data_uri is None


def test_the_logo_keeps_the_layout_and_colour_beside_it(company):
    invoicing.update_profile(
        company.id, invoice_template="banded", primary_color="#1f4e79")
    brand.set_logo(company.id, image_bytes())

    branding = invoicing.branding_for(company.id)

    assert branding.template_key == "banded"
    assert branding.primary == "#1f4e79"
    assert branding.logo_data_uri


# --- On the document (L8) -------------------------------------------------------

LOGO_URI = "data:image/png;base64," + base64.b64encode(image_bytes()).decode()


@pytest.fixture
def doc():
    return sample_doc()


@pytest.mark.parametrize("template", sorted(config.INVOICE_TEMPLATES))
def test_every_layout_prints_the_logo(app, doc, template):
    html = pdf.render_html(doc, Branding(template=template, logo_data_uri=LOGO_URI))

    assert f'<img class="logo" src="{LOGO_URI}"' in html


@pytest.mark.parametrize("template", sorted(config.INVOICE_TEMPLATES))
def test_the_seller_is_still_named_in_words_beside_a_logo(app, doc, template):
    """A logo may be a mark with no name in it; the invoice still has to
    say who issued it."""
    html = pdf.render_html(doc, Branding(template=template, logo_data_uri=LOGO_URI))

    assert "By Monsieur" in html.split("<body>")[1]


@pytest.mark.parametrize("template", sorted(config.INVOICE_TEMPLATES))
def test_no_logo_no_image(app, doc, template):
    assert "<img" not in pdf.render_html(doc, Branding(template=template))


@pytest.mark.parametrize("template", sorted(config.INVOICE_TEMPLATES))
def test_the_logo_is_the_only_thing_a_document_references(app, doc, template):
    html = pdf.render_html(doc, Branding(template=template, logo_data_uri=LOGO_URI))

    for reference in ("http://", "https://", "<link", "url(", "@import", "file:"):
        assert reference not in html, reference
    assert html.count("<img") == 1


@pytest.mark.skipif(not pdf.available(), reason="WeasyPrint/Pango isn't installed here")
@pytest.mark.parametrize("template", sorted(config.INVOICE_TEMPLATES))
def test_a_real_pdf_renders_with_a_logo(app, doc, template):
    plain = pdf.render_pdf(doc, Branding(template=template))
    with_logo = pdf.render_pdf(doc, Branding(template=template, logo_data_uri=LOGO_URI))

    assert with_logo.startswith(b"%PDF-")
    # The image really is in there, not silently dropped by the fetcher.
    assert len(with_logo) > len(plain)
    assert b"/Image" in with_logo


# --- Settings → Invoicing points at the logo, and warns (BL14) --------------------

def test_the_invoicing_page_points_to_the_brand_section(logged_in, company):
    body = logged_in.get("/settings/invoicing").get_data(as_text=True)
    section = body.split('id="invoice-appearance"')[1]

    assert 'href="/settings/general#brand"' in section
    assert 'name="logo"' not in body      # no upload here any more
    assert "No logo yet" in section


def test_the_layout_thumbnails_show_the_studio_logo(logged_in, company):
    brand.set_logo(company.id, image_bytes())

    body = logged_in.get("/settings/invoicing").get_data(as_text=True)

    assert 'class="layout-thumb__logo" src="/settings/brand/logo.png?v=' in body
    assert "Your invoices print your logo" in body


def _mark(color):
    """A frame of `color` on a transparent canvas: keeps transparency inside
    it after trimming, the way lettering does."""
    from PIL import ImageDraw

    image = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((50, 20, 250, 80), outline=color, width=10)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


@pytest.mark.parametrize("template, colour, mark, warned", [
    ("classic", "#1f4e79", (255, 255, 255, 255), True),    # white on white paper
    ("classic", "#1f4e79", (20, 20, 20, 255), False),      # black on white: fine
    ("banded", "#1f4e79", (255, 255, 255, 255), False),    # white on navy: fine
    ("banded", "#f2e8d5", (255, 255, 255, 255), True),     # white on cream
    ("banded", "#1f2a33", (20, 20, 20, 255), True),        # black on near-black
])
def test_a_logo_the_saved_look_would_hide_is_warned_about(
        logged_in, company, template, colour, mark, warned):
    invoicing.update_profile(company.id, invoice_template=template, primary_color=colour)
    brand.set_logo(company.id, _mark(mark))
    from models import db
    db.session.commit()

    body = logged_in.get("/settings/invoicing").get_data(as_text=True)

    assert ("invoice-look__logo-warning" in body) is warned


def test_an_opaque_logo_is_never_warned_about(logged_in, company):
    invoicing.update_profile(company.id, invoice_template="classic")
    brand.set_logo(company.id, image_bytes("JPEG", color=(255, 255, 255)))
    from models import db
    db.session.commit()

    assert "invoice-look__logo-warning" not in logged_in.get(
        "/settings/invoicing").get_data(as_text=True)
