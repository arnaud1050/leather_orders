"""
The studio's brand: its logo, used on invoices and in catalog mode.

The logo used to be an invoice setting (`billing/logos.py`). Once Showcase
needed it too it became the studio's, so it lives here and billing receives
the bytes through a hook the host registers
(`billing.services.invoicing.set_logo_source`) — billing still imports
nothing new. See brand/CLAUDE.md and brand/REQUIREMENTS.md (BL1–BL15).

Imports only `db` from the host. The settings routes are the host's
(Settings → General → Brand, in app.py).

Besides keeping the file, this answers one question no other part of the
app could: **what is this logo legible on?** `appearance()` reads the
image's visible pixels — a white mark on transparency is "light" and needs
a dark background, a black one is "dark", and a logo that fills its own
rectangle is "opaque" and brings its background with it.
"""

import functools
import io
import os

from brand import config, logos
from brand import models  # noqa: F401 — registers the table with db.create_all()
from brand.logos import LogoError
from brand.models import CompanyBrand
from models import db

__all__ = [
    "LogoError", "appearance", "has_logo", "hex_luminance", "logo_filename", "logo_path",
    "logo_png", "readable_on", "remove_logo", "set_logo", "tone_of_png",
]


def _row(company_id: int) -> CompanyBrand | None:
    return db.session.get(CompanyBrand, company_id)


def logo_filename(company_id: int | None) -> str | None:
    """The stored name — also a cache-busting version for the logo's URL."""
    if company_id is None:
        return None
    row = _row(company_id)
    return row.logo_filename if row else None


def has_logo(company_id: int | None) -> bool:
    return bool(logo_filename(company_id))


def logo_path(company_id: int | None) -> str | None:
    """Where the file is, for serving it — or None, including when the row
    names a file that has gone missing."""
    name = logo_filename(company_id)
    return logos.path_for(company_id, name) if name else None


def logo_png(company_id: int | None) -> bytes | None:
    """The PNG's bytes, or None. What billing embeds in a PDF."""
    name = logo_filename(company_id)
    return logos.read(company_id, name) if name else None


def set_logo(company_id: int, data: bytes) -> None:
    """Replace the logo with an upload (BL1–BL6).

    Raises `LogoError`, with a message for the person uploading, when the
    bytes aren't a usable PNG or JPEG — leaving the existing logo, row and
    file, exactly as it was. Flushes; the caller commits.
    """
    png = logos.normalise(data)
    row = _row(company_id)
    if row is None:
        row = CompanyBrand(company_id=company_id)
        db.session.add(row)
    previous = row.logo_filename
    row.logo_filename = logos.save(company_id, png)
    db.session.flush()
    logos.delete(company_id, previous)


def remove_logo(company_id: int) -> None:
    """A real delete, not a hide: nothing keeps a reference to an old logo."""
    row = _row(company_id)
    if row is None or not row.logo_filename:
        return
    previous = row.logo_filename
    row.logo_filename = None
    db.session.flush()
    logos.delete(company_id, previous)


# ---------------------------------------------------------------------------
# What the logo is legible on (BL12–BL14)
# ---------------------------------------------------------------------------

def _luminance(r: float, g: float, b: float) -> float:
    """Relative luminance of an sRGB colour, 0 (black) to 1 (white)."""
    def linear(c):
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    # Perceptual lightness of that luminance, so "mid grey" sits near 0.5.
    y = 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b)
    return y ** (1 / 2.2)


def hex_luminance(value: str) -> float:
    value = value.lstrip("#")
    return _luminance(int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def tone_of_png(png: bytes) -> dict:
    """The appearance of a PNG's bytes (BL12): `{"tone", "luminance"}`."""
    from PIL import Image

    with Image.open(io.BytesIO(png)) as image:
        image = image.convert("RGBA")
        image.thumbnail((96, 96))
        # getdata() is deprecated from Pillow 12; its replacement is newer
        # than the floor in requirements.txt, so use whichever exists.
        flatten = getattr(image, "get_flattened_data", None) or image.getdata
        pixels = list(flatten())
    visible = [(r, g, b, a) for r, g, b, a in pixels if a > 32]
    if not visible:
        return {"tone": "opaque", "luminance": 1.0}
    weight = sum(a for *_, a in visible)
    luminance = sum(_luminance(r, g, b) * a for r, g, b, a in visible) / weight
    # Opaque: it fills its own rectangle, so it carries its background.
    if len(visible) == len(pixels) and min(a for *_, a in pixels) >= 250:
        tone = "opaque"
    elif luminance >= config.LIGHT_LOGO_LUMINANCE:
        tone = "light"
    else:
        tone = "dark"
    return {"tone": tone, "luminance": round(luminance, 3)}


@functools.lru_cache(maxsize=64)
def _appearance_of_file(path: str, mtime: float) -> dict:
    with open(path, "rb") as handle:
        return tone_of_png(handle.read())


def appearance(company_id: int | None) -> dict | None:
    """`{"tone": "light" | "dark" | "opaque", "luminance": 0..1}` for the
    company's logo, or None without one (BL12). Read from the file itself —
    never stored, so a replaced logo can't leave a stale answer — and
    cached per file, since names are unique per upload."""
    path = logo_path(company_id)
    if path is None:
        return None
    try:
        return _appearance_of_file(path, os.path.getmtime(path))
    except Exception:  # noqa: BLE001 — a broken file is "no opinion", not a 500
        return None


def readable_on(look: dict | None, background_hex: str) -> bool:
    """Whether a logo with this appearance stands out on that background
    (BL14). An opaque logo always does: it brings its own background."""
    if look is None or look["tone"] == "opaque":
        return True
    return abs(look["luminance"] - hex_luminance(background_hex)) >= config.MIN_CONTRAST
