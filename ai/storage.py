"""
Render bytes on disk.

Same shape as `documents/storage.py`, which is itself the same shape as
`communications/storage/attachment_storage.py`: the row holds an opaque
`stored_filename`, and only this file knows it means a path under
`config.RENDER_DIR`. Files are namespaced per company.

Names are generated, never derived from anything a user or a vendor
supplied. Nothing here takes a filename as input at all — a render arrives
as bytes and a content type, so there's no original name to be tempted by.
"""

import os
import uuid


def _root_for(company_id: int) -> str:
    from ai import config

    path = os.path.join(config.RENDER_DIR, str(int(company_id)))
    os.makedirs(path, exist_ok=True)
    return path


def save(company_id: int, data: bytes, content_type: str) -> str:
    """Write bytes, return the opaque stored name to put on the row."""
    extension = ".jpg" if content_type == "image/jpeg" else ".png"
    stored_filename = f"{uuid.uuid4().hex}{extension}"
    with open(os.path.join(_root_for(company_id), stored_filename), "wb") as handle:
        handle.write(data)
    return stored_filename


def path_for(company_id: int, stored_filename: str | None) -> str | None:
    """Absolute path for a stored render, or None if missing/invalid.

    Re-checks containment rather than trusting the stored name: even though
    `save()` only ever generates safe names, a row is data, and a path built
    from data gets validated before it opens a file.
    """
    if not stored_filename:
        return None
    directory = _root_for(company_id)
    path = os.path.abspath(os.path.join(directory, stored_filename))
    if not path.startswith(os.path.abspath(directory) + os.sep):
        return None
    return path if os.path.exists(path) else None


def read(company_id: int, stored_filename: str | None) -> bytes | None:
    """The stored bytes. Needed because saving a draft into the order's
    documents hands the bytes to another module, rather than streaming them
    to a browser the way `path_for` + `send_file` does."""
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
