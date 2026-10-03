"""
A tenant's invoice logo: checking an upload, and keeping its bytes on disk.

Same shape as `documents/storage.py` — the profile row holds an opaque
filename and only this file knows it means a path under `config.LOGO_DIR`
— but billing keeps its own copy rather than importing a sibling module.

**What is stored is never the upload.** `normalise()` decodes the image and
writes a fresh PNG, capped in size, so what later gets embedded in a PDF is
bytes this module produced: no metadata, no trailing payload, no format
other than PNG. That is also why SVG isn't accepted — it can't be
re-encoded into something inert without rasterising it.

Files are namespaced per company (`<LOGO_DIR>/<company_id>/...`) and named
by this module, never from the upload.
"""

import io
import os
import uuid

from billing import config

__all__ = ["LogoError", "delete", "normalise", "path_for", "read", "save"]

# "MPO" is a JPEG too: phones and cameras add a second embedded picture
# (a depth map, a preview) and Pillow reports those files as MPO. Refusing
# it refused most photos taken on a phone, which is where a lot of logos
# end up being saved from.
ALLOWED_FORMATS = {"PNG", "JPEG", "MPO"}
# Longest edge of the stored image. A logo prints about 6cm wide; 1200px
# is 500dpi at that size, and anything past it only makes every PDF heavier.
MAX_EDGE = 1200
# Refused before decoding. A small file can still declare enormous
# dimensions, and decoding is where that costs memory.
MAX_PIXELS = 40_000_000


class LogoError(ValueError):
    """The upload can't be used as a logo. The message is written for the
    person who chose the file."""


def normalise(data: bytes) -> bytes:
    """Check an upload and return it re-encoded as a size-capped PNG."""
    # Lazy, like the module's other heavy imports: billing stays importable
    # without Pillow, and only uploading a logo finds out.
    from PIL import Image, ImageOps

    if not data:
        raise LogoError("Choose an image file first.")
    if len(data) > config.LOGO_MAX_BYTES:
        megabytes = config.LOGO_MAX_BYTES // (1024 * 1024)
        raise LogoError(f"That file is too large. A logo can be up to {megabytes} MB.")
    try:
        image = Image.open(io.BytesIO(data))
        if image.format not in ALLOWED_FORMATS:
            raise LogoError("A logo needs to be a PNG or JPEG image.")
        if image.width * image.height > MAX_PIXELS:
            raise LogoError("That image's dimensions are too large to use as a logo.")
        image.load()
    except LogoError:
        raise
    except Exception:
        # Pillow raises a wide family of errors on bad input, and a
        # decompression bomb is its own class again. None of them is worth
        # distinguishing for the person holding the file.
        raise LogoError("That file couldn't be read as a PNG or JPEG image.") from None

    # A photo is stored sideways with a note saying which way is up; apply
    # it, or a logo photographed on a phone prints rotated.
    image = ImageOps.exif_transpose(image)
    has_alpha = image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )
    image = image.convert("RGBA" if has_alpha else "RGB")
    if has_alpha:
        # Trim fully transparent margins. Logo files are often exported with
        # generous empty space around the mark, and the layouts size a logo
        # by its box — so the padding would print as a smaller logo.
        visible = image.getchannel("A").getbbox()
        if visible:
            image = image.crop(visible)
    image.thumbnail((MAX_EDGE, MAX_EDGE))
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _root_for(company_id: int) -> str:
    path = os.path.join(config.LOGO_DIR, str(int(company_id)))
    os.makedirs(path, exist_ok=True)
    return path


def save(company_id: int, png: bytes) -> str:
    """Write normalised bytes; return the opaque name to put on the row.

    A fresh name every time, so a replaced logo has a new URL and no
    browser shows the old one from its cache.
    """
    stored_filename = f"{uuid.uuid4().hex}.png"
    with open(os.path.join(_root_for(company_id), stored_filename), "wb") as handle:
        handle.write(png)
    return stored_filename


def path_for(company_id: int, stored_filename: str | None) -> str | None:
    """Absolute path for a stored logo, or None if it's missing or invalid.

    Re-checks containment rather than trusting the stored name: `save()`
    only generates safe ones, but a row is data, and a path built from data
    gets validated before it opens a file.
    """
    if not stored_filename:
        return None
    directory = _root_for(company_id)
    path = os.path.abspath(os.path.join(directory, stored_filename))
    if not path.startswith(os.path.abspath(directory) + os.sep):
        return None
    return path if os.path.exists(path) else None


def read(company_id: int, stored_filename: str | None) -> bytes | None:
    path = path_for(company_id, stored_filename)
    if path is None:
        return None
    with open(path, "rb") as handle:
        return handle.read()


def delete(company_id: int, stored_filename: str | None) -> None:
    path = path_for(company_id, stored_filename)
    if path:
        try:
            os.remove(path)
        except OSError:
            pass  # already gone, or a read-only volume — not worth failing over
