"""
The studio's logo (brand/REQUIREMENTS.md BL1–BL15): what an upload has to
be, where it's kept, who can see it, what it reads on, and the move out of
billing. How it then reaches an invoice is `tests/test_invoice_logo.py`.

The rule underneath most of this file: **what is stored is never the
upload.** The image is decoded and written back out as a PNG of this
package's own making, so the bytes embedded in every invoice and shown in
catalog mode carry nothing the uploader put there except pixels.

`BRAND_DIR` (and billing's old `BILLING_LOGO_DIR`, for the migration) point
at temp directories in `conftest.py`, before `app` is imported.
"""

import io
import os
import pathlib

import pytest
import sqlalchemy as sa
from PIL import Image

import brand
from brand import config, logos
from brand.migrations import adopt_billing_logos
from brand.models import CompanyBrand
from models import db

UPLOAD = "/settings/brand/logo"
DELETE = "/settings/brand/logo/delete"
SERVED = "/settings/brand/logo.png"


def image_bytes(fmt="PNG", size=(300, 100), mode="RGB", color=(31, 78, 121)):
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, format=fmt)
    return out.getvalue()


def mark(color, background=(0, 0, 0, 0)):
    """A logo-like mark of `color` — a frame, so it keeps transparency inside
    it once its margins are trimmed (BL3a), the way lettering does — on a
    canvas that's transparent unless told otherwise."""
    from PIL import ImageDraw

    image = Image.new("RGBA", (300, 100), background)
    ImageDraw.Draw(image).rectangle((50, 20, 250, 80), outline=color, width=10)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def opened(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png))


def upload(client, data, filename="logo.png", follow=False):
    return client.post(
        UPLOAD, data={"logo": (io.BytesIO(data), filename)},
        content_type="multipart/form-data", follow_redirects=follow,
    )


# --- BL1–BL3: what counts as a logo ------------------------------------------

def test_a_png_is_accepted():
    result = logos.normalise(image_bytes("PNG"))

    assert opened(result).format == "PNG"
    assert opened(result).size == (300, 100)


def test_a_jpeg_is_accepted_and_stored_as_png():
    assert opened(logos.normalise(image_bytes("JPEG"))).format == "PNG"


def test_a_phone_jpeg_is_accepted():
    """Phones and cameras embed a second picture in their JPEGs, and Pillow
    reports those as MPO. Refusing them refused most photos off a phone."""
    out = io.BytesIO()
    first, second = Image.new("RGB", (400, 300), (40, 60, 90)), Image.new("RGB", (400, 300), "white")
    first.save(out, "MPO", save_all=True, append_images=[second])
    assert opened(out.getvalue()).format == "MPO"

    result = logos.normalise(out.getvalue())

    assert opened(result).format == "PNG"
    assert opened(result).size == (400, 300)


def test_a_photo_sized_jpeg_is_accepted():
    out = io.BytesIO()
    Image.effect_noise((3000, 2000), 90).convert("RGB").save(out, "JPEG", quality=95)
    assert len(out.getvalue()) > 2 * 1024 * 1024

    assert opened(logos.normalise(out.getvalue())).size == (1200, 800)


def test_the_upload_cap_matches_what_nginx_lets_through():
    """nginx (server_config) allows 10 MB bodies. A *higher* cap here would
    be a promise nginx breaks with a bare 413 before the app can explain."""
    assert config.LOGO_MAX_BYTES == 10 * 1024 * 1024


def test_a_sideways_photo_is_stored_the_right_way_up():
    image = Image.new("RGB", (300, 100), (31, 78, 121))
    exif = image.getexif()
    exif[0x0112] = 6  # "rotate 90° clockwise to display"
    out = io.BytesIO()
    image.save(out, "JPEG", exif=exif)

    assert opened(logos.normalise(out.getvalue())).size == (100, 300)


def test_transparency_survives():
    result = logos.normalise(image_bytes("PNG", mode="RGBA", color=(255, 255, 255, 0)))

    image = opened(result)
    assert image.mode == "RGBA"
    assert image.getpixel((0, 0))[3] == 0


def test_transparent_margins_are_trimmed():
    padded = Image.new("RGBA", (600, 400), (0, 0, 0, 0))
    padded.paste((255, 255, 255, 255), (200, 150, 400, 250))
    out = io.BytesIO()
    padded.save(out, format="PNG")

    image = opened(logos.normalise(out.getvalue()))

    assert image.size == (200, 100)
    assert image.getpixel((0, 0)) == (255, 255, 255, 255)


def test_an_opaque_image_is_not_cropped():
    assert opened(logos.normalise(image_bytes("JPEG", color=(255, 255, 255)))).size == (300, 100)


def test_a_fully_transparent_image_is_left_alone():
    blank = image_bytes("PNG", mode="RGBA", color=(0, 0, 0, 0))

    assert opened(logos.normalise(blank)).size == (300, 100)


def test_a_large_image_is_scaled_down_keeping_its_shape():
    assert opened(logos.normalise(image_bytes("PNG", size=(3000, 1000)))).size == (1200, 400)


def test_a_small_image_is_not_scaled_up():
    assert opened(logos.normalise(image_bytes("PNG", size=(120, 40)))).size == (120, 40)


def test_what_is_stored_is_a_fresh_encoding_not_the_upload():
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
    monkeypatch.setattr(logos, "MAX_PIXELS", 1000)

    with pytest.raises(logos.LogoError, match="dimensions"):
        logos.normalise(image_bytes("PNG", size=(100, 100)))


# --- BL4–BL6: keeping it -------------------------------------------------------

def test_setting_a_logo_stores_a_file_for_that_company(company):
    brand.set_logo(company.id, image_bytes())

    assert brand.has_logo(company.id)
    path = brand.logo_path(company.id)
    assert pathlib.Path(path).parent.name == str(company.id)
    assert pathlib.Path(path).is_relative_to(pathlib.Path(config.BRAND_DIR))
    assert opened(open(path, "rb").read()).format == "PNG"


def test_the_stored_name_is_generated_never_taken_from_anyone(company):
    brand.set_logo(company.id, image_bytes())

    name = brand.logo_filename(company.id)
    assert name.endswith(".png") and len(name) == 36


def test_replacing_a_logo_removes_the_old_file(company):
    brand.set_logo(company.id, image_bytes())
    old_path = brand.logo_path(company.id)

    brand.set_logo(company.id, image_bytes(color=(200, 0, 0)))

    assert brand.logo_path(company.id) != old_path
    assert not os.path.exists(old_path)


def test_a_refused_upload_leaves_the_existing_logo_alone(company):
    brand.set_logo(company.id, image_bytes())
    before = brand.logo_filename(company.id)

    with pytest.raises(brand.LogoError):
        brand.set_logo(company.id, b"not an image")

    assert brand.logo_filename(company.id) == before
    assert os.path.exists(brand.logo_path(company.id))


def test_removing_a_logo_deletes_the_file(company):
    brand.set_logo(company.id, image_bytes())
    path = brand.logo_path(company.id)

    brand.remove_logo(company.id)

    assert not brand.has_logo(company.id)
    assert brand.logo_path(company.id) is None and brand.logo_png(company.id) is None
    assert not os.path.exists(path)


def test_removing_a_logo_that_is_not_there_is_harmless(company):
    brand.remove_logo(company.id)

    assert not brand.has_logo(company.id)


def test_logos_are_per_company(company, other_company):
    brand.set_logo(company.id, image_bytes())

    assert brand.logo_path(other_company.id) is None
    assert brand.logo_png(other_company.id) is None


def test_no_company_has_no_logo():
    assert brand.logo_path(None) is None and brand.appearance(None) is None


def test_one_companys_filename_does_not_open_anothers_file(company, other_company):
    brand.set_logo(company.id, image_bytes())

    assert logos.path_for(other_company.id, brand.logo_filename(company.id)) is None


@pytest.mark.parametrize("name", [
    "../1/secret.png", "..\\..\\app.py", "/etc/passwd", "../../data/atelier.db",
])
def test_a_stored_name_that_points_elsewhere_opens_nothing(company, name):
    assert logos.path_for(company.id, name) is None
    assert logos.read(company.id, name) is None


# --- BL9–BL11: Settings → General → Brand ---------------------------------------

def test_uploading_a_logo_saves_it(logged_in, company):
    response = upload(logged_in, image_bytes())

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings/general")
    assert brand.has_logo(company.id)


def test_uploading_something_else_says_why_and_saves_nothing(logged_in, company):
    response = upload(logged_in, b"not an image", "logo.png", follow=True)

    assert not brand.has_logo(company.id)
    body = response.get_data(as_text=True)
    assert "couldn&#39;t be read as a PNG or JPEG" in body.split('id="brand"')[1]


def test_the_file_name_does_not_make_it_an_image(logged_in, company):
    upload(logged_in, b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', "logo.png")

    assert not brand.has_logo(company.id)


def test_uploading_nothing_says_so(logged_in, company):
    response = logged_in.post(UPLOAD, data={}, follow_redirects=True)

    assert not brand.has_logo(company.id)
    assert "Choose an image file first." in response.get_data(as_text=True)


def test_an_oversized_upload_is_refused(logged_in, company, monkeypatch):
    monkeypatch.setattr(config, "LOGO_MAX_BYTES", 100)

    response = upload(logged_in, image_bytes(), follow=True)

    assert not brand.has_logo(company.id)
    assert "too large" in response.get_data(as_text=True)


def test_a_refused_upload_keeps_the_logo_already_there(logged_in, company):
    upload(logged_in, image_bytes())
    before = brand.logo_filename(company.id)

    upload(logged_in, b"garbage")

    db.session.expire_all()
    assert brand.logo_filename(company.id) == before


def test_deleting_the_logo(logged_in, company):
    upload(logged_in, image_bytes())

    response = logged_in.post(DELETE)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings/general")
    db.session.expire_all()
    assert not brand.has_logo(company.id)


@pytest.mark.parametrize("url", [UPLOAD, DELETE])
def test_the_logo_routes_require_a_login(app, url):
    response = app.test_client().post(url)

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_general_offers_an_add_logo_tile_when_there_is_none(logged_in, company):
    body = logged_in.get("/settings/general").get_data(as_text=True)
    section = body.split('id="brand"')[1].split("</section>")[0]

    assert "<h2>Brand</h2>" in body
    assert 'name="logo"' in section and 'enctype="multipart/form-data"' in section
    assert "Add logo" in section and "data-upload-zone" in section
    assert 'data-upload-status="logo-error"' in section and 'id="logo-error"' in section
    assert 'accept="image/png,image/jpeg,.png,.jpg,.jpeg"' in section
    assert 'data-type-error="A logo needs to be a PNG or JPEG image."' in section
    assert 'data-upload-hint hidden>or drop an image here</span>' in section
    assert "assets/js/upload-tile.js" in body
    assert SERVED not in body


def test_general_shows_the_logo_on_light_and_dark_with_replace_and_delete(logged_in, company):
    upload(logged_in, image_bytes())

    body = logged_in.get("/settings/general").get_data(as_text=True)
    section = body.split('id="brand"')[1].split("</section>")[0]

    assert "brand__preview--light" in section and "brand__preview--dark" in section
    assert section.count(f'src="{SERVED}?v=') == 2
    assert ">Replace</label>" in section
    assert f'action="{DELETE}"' in section and ">Delete<" in section
    assert "Remove" not in section


def test_the_logo_is_served_to_its_own_company(logged_in, company):
    upload(logged_in, image_bytes())

    response = logged_in.get(SERVED)

    assert response.status_code == 200 and response.mimetype == "image/png"
    assert opened(response.data).format == "PNG"
    assert "no-cache" in response.headers.get("Cache-Control", "")


def test_no_logo_is_a_404(logged_in, company):
    assert logged_in.get(SERVED).status_code == 404


def test_another_companys_logo_is_never_served(logged_in, company, other_company):
    brand.set_logo(other_company.id, image_bytes())
    db.session.commit()

    assert logged_in.get(SERVED).status_code == 404


def test_the_served_logo_requires_a_login(app):
    response = app.test_client().get(SERVED)

    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_the_old_invoicing_logo_routes_are_gone(logged_in, company):
    assert logged_in.post("/settings/invoicing/logo").status_code == 404
    assert logged_in.get("/invoices/logo.png").status_code == 404


# --- BL12–BL13: what the logo reads on ------------------------------------------

@pytest.mark.parametrize("png, tone", [
    (mark((255, 255, 255, 255)), "light"),            # white on transparency
    (mark((235, 215, 160, 255)), "light"),            # cream
    (mark((20, 20, 20, 255)), "dark"),                # black
    (mark((31, 78, 121, 255)), "dark"),               # navy
    (image_bytes("PNG", color=(255, 255, 255)), "opaque"),    # fills its box
    (mark((255, 255, 255, 255), (20, 20, 20, 255)), "opaque"),
])
def test_the_tone_comes_from_the_visible_pixels(png, tone):
    assert brand.tone_of_png(png)["tone"] == tone


def test_appearance_is_read_from_the_stored_file(company):
    assert brand.appearance(company.id) is None
    brand.set_logo(company.id, mark((255, 255, 255, 255)))
    assert brand.appearance(company.id)["tone"] == "light"
    brand.set_logo(company.id, mark((20, 20, 20, 255)))
    assert brand.appearance(company.id)["tone"] == "dark"


def test_readable_on():
    light = brand.tone_of_png(mark((255, 255, 255, 255)))
    dark = brand.tone_of_png(mark((20, 20, 20, 255)))
    opaque = brand.tone_of_png(image_bytes("PNG", color=(255, 255, 255)))

    assert not brand.readable_on(light, "#ffffff") and brand.readable_on(light, "#111111")
    assert brand.readable_on(dark, "#ffffff") and not brand.readable_on(dark, "#111111")
    assert brand.readable_on(opaque, "#ffffff") and brand.readable_on(opaque, "#111111")
    assert brand.readable_on(None, "#ffffff")


@pytest.mark.parametrize("png, words", [
    (mark((255, 255, 255, 255)), "A light logo"),
    (mark((20, 20, 20, 255)), "A dark logo"),
    (image_bytes("PNG", color=(255, 255, 255)), "fills its own background"),
])
def test_general_says_how_the_logo_will_be_placed(logged_in, company, png, words):
    brand.set_logo(company.id, png)
    db.session.commit()

    assert words in logged_in.get("/settings/general").get_data(as_text=True)


# --- BL15: the move out of billing ------------------------------------------------

def _legacy(company, png=None, name="0123456789abcdef0123456789abcdef.png"):
    """A logo as billing used to keep it: a column on billing_profiles and a
    file under BILLING_LOGO_DIR/<company_id>/."""
    from billing import config as billing_config
    from billing.services import invoicing

    invoicing.profile_for(company.id)
    db.session.execute(sa.text("UPDATE billing_profiles SET logo_filename = :n "
                               "WHERE company_id = :c"), {"n": name, "c": company.id})
    db.session.commit()
    folder = pathlib.Path(billing_config.LOGO_DIR) / str(company.id)
    folder.mkdir(parents=True, exist_ok=True)
    if png is not None:
        (folder / name).write_bytes(png)
    return folder / name, billing_config.LOGO_DIR


def _billing_column(company):
    return db.session.execute(sa.text(
        "SELECT logo_filename FROM billing_profiles WHERE company_id = :c"),
        {"c": company.id}).scalar()


def test_an_old_invoice_logo_is_adopted(company):
    png = mark((255, 255, 255, 255))
    source, legacy_dir = _legacy(company, png)

    assert adopt_billing_logos(legacy_dir) == 1

    db.session.expire_all()
    assert brand.logo_filename(company.id) == source.name
    assert brand.logo_png(company.id) == png
    assert _billing_column(company) is None
    assert not source.exists()


def test_adopting_twice_changes_nothing(company):
    source, legacy_dir = _legacy(company, mark((255, 255, 255, 255)))
    adopt_billing_logos(legacy_dir)

    assert adopt_billing_logos(legacy_dir) == 0
    assert CompanyBrand.query.count() == 1


def test_a_missing_old_file_is_cleared_not_retried(company):
    source, legacy_dir = _legacy(company, png=None)

    assert adopt_billing_logos(legacy_dir) == 0

    assert not brand.has_logo(company.id)
    assert _billing_column(company) is None


def test_a_name_billing_never_wrote_builds_no_path(company):
    source, legacy_dir = _legacy(company, png=b"x", name="../../app.py")

    assert adopt_billing_logos(legacy_dir) == 0
    assert not brand.has_logo(company.id)


def test_an_existing_brand_logo_wins(company):
    brand.set_logo(company.id, mark((20, 20, 20, 255)))
    db.session.commit()
    kept = brand.logo_filename(company.id)
    source, legacy_dir = _legacy(company, mark((255, 255, 255, 255)))

    adopt_billing_logos(legacy_dir)

    db.session.expire_all()
    assert brand.logo_filename(company.id) == kept
    assert _billing_column(company) is None


# --- The package's boundary ---------------------------------------------------------

def test_brand_imports_only_db_from_the_host():
    import ast

    root = pathlib.Path(__file__).resolve().parent.parent / "brand"
    sources = sorted(root.rglob("*.py"))
    assert len(sources) >= 4
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "") == "models":
                assert [a.name for a in node.names] == ["db"], path.name
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", None) or node.names[0].name
                assert module.split(".")[0] not in {
                    "app", "billing", "showcase", "documents", "ai", "inventory",
                    "communications", "admin"}, path.name
