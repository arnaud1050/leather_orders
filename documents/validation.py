"""
Upload validation: extension allowlist, content sniffing, size and quota.

Nothing here trusts the browser-supplied MIME type or a renamed extension —
every check either reads the actual bytes or a size already known before
they're written to disk. Raises `ValidationError` with a message safe to
show the user; never lets a bad upload reach `storage.save()`.
"""

import os
import zipfile
from io import BytesIO

from documents import config


class ValidationError(Exception):
    """A rejected upload, with a user-facing reason."""


_EXTENSION_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".eps": "application/postscript",
    ".ai": "application/postscript",  # overridden to application/pdf below when PDF-compatible
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _extension_of(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def _sniff_content_type(extension: str, data: bytes) -> str:
    """Confirm the bytes actually look like what the extension claims.

    Raises ValidationError on a mismatch rather than returning a fallback —
    a file that fails its own extension's check is exactly the case this
    exists to catch (a renamed .exe, a corrupt upload).
    """
    if extension in (".jpg", ".jpeg", ".png"):
        try:
            from PIL import Image

            with Image.open(BytesIO(data)) as image:
                image.verify()
        except Exception as exc:
            raise ValidationError(f"That doesn't look like a valid {extension} image.") from exc
        return _EXTENSION_CONTENT_TYPES[extension]

    if extension in (".pdf", ".ai"):
        if data.startswith(b"%PDF-"):
            return "application/pdf"
        if extension == ".ai" and data.startswith(b"%!PS-Adobe-"):
            # Pure-PostScript .ai, saved without "Create PDF Compatible
            # File" — valid, just not renderable as a PDF (see thumbnails.py).
            return "application/postscript"
        raise ValidationError("That doesn't look like a valid PDF/Illustrator file.")

    if extension == ".eps":
        if not data.startswith(b"%!PS-Adobe-"):
            raise ValidationError("That doesn't look like a valid EPS file.")
        return "application/postscript"

    if extension == ".svg":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("That doesn't look like a valid SVG file.") from exc
        if "<svg" not in text[:2000]:
            raise ValidationError("That doesn't look like a valid SVG file.")
        return "image/svg+xml"

    if extension in (".docx", ".xlsx"):
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                if "[Content_Types].xml" not in archive.namelist():
                    raise ValidationError(f"That doesn't look like a valid {extension} file.")
        except zipfile.BadZipFile as exc:
            raise ValidationError(f"That doesn't look like a valid {extension} file.") from exc
        return _EXTENSION_CONTENT_TYPES[extension]

    raise ValidationError(f"{extension or '(no extension)'} isn't an allowed file type.")


def validate_upload(original_filename: str, data: bytes, *, current_usage_bytes: int) -> str:
    """Validate one upload, returning its confirmed content type.

    `current_usage_bytes` is the company's usage *before* this file — the
    caller (services.py) owns the query, so this stays a pure function.
    """
    extension = _extension_of(original_filename)
    if extension not in config.ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(config.ALLOWED_EXTENSIONS))
        raise ValidationError(f"\"{original_filename}\": {extension or 'files with no extension'} "
                               f"isn't allowed. Allowed types: {allowed}.")

    size = len(data)
    if size > config.MAX_FILE_BYTES:
        raise ValidationError(
            f"\"{original_filename}\" is {size / 1024 / 1024:.1f}MB, over the "
            f"{config.MAX_FILE_BYTES / 1024 / 1024:.0f}MB per-file limit."
        )
    if current_usage_bytes + size > config.MAX_TOTAL_BYTES:
        raise ValidationError(
            f"\"{original_filename}\" would put this company's document storage over its "
            f"{config.MAX_TOTAL_BYTES / 1024 / 1024 / 1024:.1f}GB limit. "
            "Delete something first, or upload fewer files."
        )

    return _sniff_content_type(extension, data)
