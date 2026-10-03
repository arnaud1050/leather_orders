"""
The invoice logo: what an upload has to be, where it's kept, who can see
it, and how it reaches the PDF.

The rule underneath most of this file: **what is stored is never the
upload.** The image is decoded and written back out as a PNG of this
module's own making, so the bytes embedded in every invoice afterwards
carry nothing the uploader put there except pixels.

`BILLING_LOGO_DIR` is pointed at a temp directory in `conftest.py`, before
`app` is imported — nothing here touches the real `data/`.
"""

import base64
import io
import os

import pytest
from PIL import Image

from billing import config, logos, pdf
from billing.documents import Branding
from billing.services import invoicing
from models import db
from tests.test_invoice_pdf import sample_doc

UPLOAD = "/settings/invoicing/logo"
DELETE = "/settings/invoicing/logo/delete"


def image_bytes(fmt="PNG", size=(300, 100), mode="RGB", color=(31, 78, 121)):
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, format=fmt)
    return out.getvalue()


def opened(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png))


def profile_of(company):
    return invoicing.profile_for(company.id)


def upload(client, data, filename="logo.png", follow=False):
    return client.post(
        UPLOAD, data={"logo": (io.BytesIO(data), filename)},
        content_type="multipart/form-data", follow_redirects=follow,
    )


# --- What counts as a logo ------------------------------------------------

def test_a_png_is_accepted():
    result = logos.normalise(image_bytes("PNG"))

    assert opened(result).format == "PNG"
    assert opened(result).size == (300, 100)


def test_a_jpeg_is_accepted_and_stored_as_png():
    result = logos.normalise(image_bytes("JPEG"))

    assert opened(result).format == "PNG"


def test_transparency_survives():
    """The whole point of asking for a transparent PNG on the band."""
    result = logos.normalise(image_bytes("PNG", mode="RGBA", color=(255, 255, 255, 0)))

    image = opened(result)
    assert image.mode == "RGBA"
    assert image.getpixel((0, 0))[3] == 0


def test_transparent_margins_are_trimmed():
    """A logo exported with empty space around it would otherwise print
    smaller than the same logo exported tight."""
    padded = Image.new("RGBA", (600, 400), (0, 0, 0, 0))
    padded.paste((255, 255, 255, 255), (200, 150, 400, 250))
    out = io.BytesIO()
    padded.save(out, format="PNG")

    image = opened(logos.normalise(out.getvalue()))

    assert image.size == (200, 100)
    assert image.getpixel((0, 0)) == (255, 255, 255, 255)


def test_an_opaque_image_is_not_cropped():
    """Only transparency counts as margin — a white border on a JPEG is
    part of the picture, and guessing otherwise would eat real logos."""
    assert opened(logos.normalise(image_bytes("JPEG", color=(255, 255, 255)))).size == (300, 100)


def test_a_fully_transparent_image_is_left_alone():
    blank = image_bytes("PNG", mode="RGBA", color=(0, 0, 0, 0))

    assert opened(logos.normalise(blank)).size == (300, 100)


def test_a_large_image_is_scaled_down_keeping_its_shape():
    result = logos.normalise(image_bytes("PNG", size=(3000, 1000)))

    assert opened(result).size == (1200, 400)


def test_a_small_image_is_not_scaled_up():
    result = logos.normalise(image_bytes("PNG", size=(120, 40)))

    assert opened(result).size == (120, 40)


def test_what_is_stored_is_a_fresh_encoding_not_the_upload():
    """Anything riding along with the pixels is left behind."""
    tampered = image_bytes("PNG") + b"<script>alert(1)</script>"

    result = logos.normalise(tampered)

    assert b"<script>" not in result
    assert opened(result).size == (300, 100)


@pytest.mark.parametrize("data", [
    b"",
    b"not an image at all",
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    b"%PDF-1.7 pretending",
    image_bytes("PNG")[:40],          # truncated
], ids=["empty", "text", "svg", "pdf", "truncated"])
def test_anything_that_is_not_a_readable_png_or_jpeg_is_refused(data):
    with pytest.raises(logos.LogoError):
        logos.normalise(data)


@pytest.mark.parametrize("fmt", ["GIF", "BMP", "WEBP", "TIFF"])
def test_other_image_formats_are_refused(fmt):
    with pytest.raises(logos.LogoError, match="PNG or JPEG"):
        logos.normalise(image_bytes(fmt))


def test_an_upload_over_the_size_cap_is_refused(monkeypatch):
    monkeypatch.setattr(config, "LOGO_MAX_BYTES", 100)

    with pytest.raises(logos.LogoError, match="too large"):
        logos.normalise(image_bytes("PNG"))


def test_an_image_with_enormous_dimensions_is_refused_before_decoding(monkeypatch):
    """A small file can declare a huge canvas; decoding is what costs."""
    monkeypatch.setattr(logos, "MAX_PIXELS", 1000)

    with pytest.raises(logos.LogoError, match="dimensions"):
        logos.normalise(image_bytes("PNG", size=(100, 100)))


# --- Keeping it -----------------------------------------------------------

def test_setting_a_logo_stores_a_file_for_that_company(company):
    invoicing.set_logo(company.id, image_bytes())

    profile = profile_of(company)
    assert profile.has_logo
    path = invoicing.logo_path(company.id)
    assert os.path.dirname(path).endswith(os.path.join("", str(company.id)))
    assert opened(open(path, "rb").read()).format == "PNG"


def test_the_stored_name_is_generated_never_taken_from_anyone(company):
    invoicing.set_logo(company.id, image_bytes())

    name = profile_of(company).logo_filename
    assert name.endswith(".png")
    assert len(name) == 36  # 32 hex characters + ".png"


def test_replacing_a_logo_removes_the_old_file(company):
    invoicing.set_logo(company.id, image_bytes())
    old_path = invoicing.logo_path(company.id)

    invoicing.set_logo(company.id, image_bytes(color=(200, 0, 0)))

    new_path = invoicing.logo_path(company.id)
    assert new_path != old_path
    assert not os.path.exists(old_path)
    assert os.path.exists(new_path)


def test_a_refused_upload_leaves_the_existing_logo_alone(company):
    invoicing.set_logo(company.id, image_bytes())
    before = profile_of(company).logo_filename

    with pytest.raises(invoicing.LogoError):
        invoicing.set_logo(company.id, b"not an image")

    assert profile_of(company).logo_filename == before
    assert os.path.exists(invoicing.logo_path(company.id))


def test_removing_a_logo_deletes_the_file(company):
    invoicing.set_logo(company.id, image_bytes())
    path = invoicing.logo_path(company.id)

    invoicing.remove_logo(company.id)

    assert not profile_of(company).has_logo
    assert invoicing.logo_path(company.id) is None
    assert not os.path.exists(path)


def test_removing_a_logo_that_is_not_there_is_harmless(company):
    invoicing.remove_logo(company.id)

    assert not profile_of(company).has_logo


def test_logos_are_per_company(company, other_company):
    invoicing.set_logo(company.id, image_bytes())

    assert invoicing.logo_path(other_company.id) is None
    assert invoicing.branding_for(other_company.id).logo_data_uri is None


def test_one_companys_filename_does_not_open_anothers_file(company, other_company):
    invoicing.set_logo(company.id, image_bytes())
    name = profile_of(company).logo_filename

    assert logos.path_for(other_company.id, name) is None


@pytest.mark.parametrize("name", [
    "../1/secret.png", "..\\..\\app.py", "/etc/passwd", "../../data/atelier.db",
])
def test_a_stored_name_that_points_elsewhere_opens_nothing(company, name):
    """The row is data: a path built from it is checked before use."""
    assert logos.path_for(company.id, name) is None
    assert logos.read(company.id, name) is None


# --- Reaching the PDF -----------------------------------------------------

def test_branding_carries_the_logo_embedded(company):
    invoicing.set_logo(company.id, image_bytes())

    uri = invoicing.branding_for(company.id).logo_data_uri

    assert uri.startswith("data:image/png;base64,")
    embedded = base64.b64decode(uri.split(",", 1)[1])
    assert embedded == open(invoicing.logo_path(company.id), "rb").read()


def test_branding_without_a_logo_has_none(company):
    assert invoicing.branding_for(company.id).logo_data_uri is None


def test_a_logo_whose_file_has_gone_is_simply_left_off(company):
    """An invoice without its logo is still an invoice — a missing file
    must not stop an export."""
    invoicing.set_logo(company.id, image_bytes())
    os.remove(invoicing.logo_path(company.id))

    branding = invoicing.branding_for(company.id)

    assert branding.logo_data_uri is None


def test_the_logo_keeps_the_layout_and_colours_beside_it(company):
    invoicing.update_profile(
        company.id, invoice_template="banded", primary_color="#1f4e79")
    invoicing.set_logo(company.id, image_bytes())

    branding = invoicing.branding_for(company.id)

    assert branding.template_key == "banded"
    assert branding.primary == "#1f4e79"
    assert branding.logo_data_uri


# --- The settings routes --------------------------------------------------

def test_uploading_a_logo_saves_it(logged_in, company):
    response = upload(logged_in, image_bytes())

    assert response.status_code == 302
    assert profile_of(company).has_logo


def test_uploading_something_else_says_why_and_saves_nothing(logged_in, company):
    response = upload(logged_in, b"not an image", "logo.png", follow=True)

    assert not profile_of(company).has_logo
    assert "couldn&#39;t be read as a PNG or JPEG" in response.get_data(as_text=True)


def test_the_file_name_does_not_make_it_an_image(logged_in, company):
    """Decided by the bytes, never by the extension or the browser's
    content type."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'

    upload(logged_in, svg, "logo.png")

    assert not profile_of(company).has_logo


def test_uploading_nothing_says_so(logged_in, company):
    response = logged_in.post(UPLOAD, data={}, follow_redirects=True)

    assert not profile_of(company).has_logo
    assert "Choose an image file first." in response.get_data(as_text=True)


def test_an_oversized_upload_is_refused(logged_in, company, monkeypatch):
    monkeypatch.setattr(config, "LOGO_MAX_BYTES", 100)

    response = upload(logged_in, image_bytes(), follow=True)

    assert not profile_of(company).has_logo
    assert "too large" in response.get_data(as_text=True)


def test_a_refused_upload_keeps_the_logo_already_there(logged_in, company):
    upload(logged_in, image_bytes())
    before = profile_of(company).logo_filename

    upload(logged_in, b"garbage")

    db.session.expire_all()
    assert profile_of(company).logo_filename == before


def test_deleting_the_logo(logged_in, company):
    upload(logged_in, image_bytes())

    response = logged_in.post(DELETE)

    assert response.status_code == 302
    db.session.expire_all()
    assert not profile_of(company).has_logo


def test_uploading_does_not_touch_the_rest_of_the_look(logged_in, company):
    invoicing.update_profile(
        company.id, invoice_template="banded", primary_color="#1f4e79")
    db.session.commit()

    upload(logged_in, image_bytes())

    profile = profile_of(company)
    assert profile.invoice_template == "banded"
    assert profile.primary_color == "#1f4e79"
    assert profile.invoice_prefix == "BM"


@pytest.mark.parametrize("url", [UPLOAD, DELETE])
def test_the_logo_routes_require_a_login(app, url):
    response = app.test_client().post(url)

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_the_settings_page_offers_an_upload_when_there_is_no_logo(logged_in, company):
    body = logged_in.get("/settings/invoicing").get_data(as_text=True)

    assert 'name="logo"' in body
    assert 'enctype="multipart/form-data"' in body
    assert ">Upload<" in body
    assert "/invoices/logo.png" not in body


def test_the_settings_page_shows_the_logo_with_a_delete_button(logged_in, company):
    upload(logged_in, image_bytes())

    body = logged_in.get("/settings/invoicing").get_data(as_text=True)

    assert "/invoices/logo.png" in body
    assert ">Replace<" in body
    assert f'action="{DELETE}"' in body
    assert ">Delete<" in body       # the app's delete convention, never "Remove"
    assert "Remove" not in body.split("Invoice appearance")[1]


# --- Serving it back ------------------------------------------------------

def test_the_logo_is_served_to_its_own_company(logged_in, company):
    upload(logged_in, image_bytes())

    response = logged_in.get("/invoices/logo.png")

    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert opened(response.data).format == "PNG"
    assert "no-cache" in response.headers.get("Cache-Control", "")


def test_no_logo_is_a_404(logged_in, company):
    assert logged_in.get("/invoices/logo.png").status_code == 404


def test_another_companys_logo_is_never_served(logged_in, company, other_company):
    """There's no id in the URL to tamper with: it's the session's company
    or nothing."""
    invoicing.set_logo(other_company.id, image_bytes())
    db.session.commit()

    assert logged_in.get("/invoices/logo.png").status_code == 404


def test_the_logo_requires_a_login(app):
    response = app.test_client().get("/invoices/logo.png")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


# --- On the document ------------------------------------------------------

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
