"""
Best-effort thumbnail generation.

Every function here returns `None` on anything short of success — a
missing `poppler` install, a non-PDF-compatible `.ai` file, an unsupported
type — rather than raising. A thumbnail is a nice-to-have; the upload
itself must never fail because a preview couldn't be made. Callers fall
back to a generic per-extension icon in the UI when this returns `None`.

PDF/`.ai` rendering shells out to `pdftoppm` (via `pdf2image`), which needs
the `poppler-utils` system package — present in both Docker images, not
necessarily on a local dev machine. Checked with `shutil.which` up front so
that absence degrades quietly instead of raising on every PDF upload.
"""

import shutil
from io import BytesIO

from documents import config

_POPPLER_AVAILABLE = shutil.which("pdftoppm") is not None


def _resize_to_png(image) -> bytes:
    image = image.convert("RGB")
    image.thumbnail((config.THUMBNAIL_MAX_DIMENSION, config.THUMBNAIL_MAX_DIMENSION))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def generate(content_type: str, data: bytes) -> bytes | None:
    """PNG thumbnail bytes for supported types, or None."""
    if content_type in ("image/jpeg", "image/png"):
        try:
            from PIL import Image

            with Image.open(BytesIO(data)) as image:
                return _resize_to_png(image)
        except Exception:
            return None

    if content_type == "application/pdf" and _POPPLER_AVAILABLE:
        try:
            from pdf2image import convert_from_bytes

            pages = convert_from_bytes(data, first_page=1, last_page=1)
            if not pages:
                return None
            return _resize_to_png(pages[0])
        except Exception:
            return None

    # .eps, pure-PostScript .ai, .svg, .docx, .xlsx: no thumbnail attempted
    # — the UI falls back to a generic per-extension icon.
    return None
