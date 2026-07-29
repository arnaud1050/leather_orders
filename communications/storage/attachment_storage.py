"""
Attachment bytes on disk.

Separate from the model so the storage backend can change without the
schema changing: EmailAttachment holds an opaque `stored_filename`, and
only this module knows it means "a file under ATTACHMENT_DIR". Pointing
this at S3 later means rewriting these four functions.

Files are namespaced per company (`<ATTACHMENT_DIR>/<company_id>/...`) so
one tenant's attachments are never in the same directory as another's —
the same isolation the database rows have, applied to the filesystem.

Names on disk are generated, never taken from the email. An attachment
called `../../app.py` is a real thing an attacker sends; the original name
is display-only metadata on the row.
"""

import os
import uuid

from communications import config


def _company_dir(company_id: int) -> str:
    path = os.path.join(config.ATTACHMENT_DIR, str(int(company_id)))
    os.makedirs(path, exist_ok=True)
    return path


def save(company_id: int, original_filename: str, data: bytes) -> str:
    """Write bytes, return the opaque stored name to put on the row.

    The extension is carried over (so a browser serves it sensibly) but
    sanitised to alphanumerics — everything else about the sender's
    filename is discarded.
    """
    _, extension = os.path.splitext(original_filename or "")
    extension = "".join(ch for ch in extension if ch.isalnum() or ch == ".")[:12]
    stored_filename = f"{uuid.uuid4().hex}{extension}"
    with open(os.path.join(_company_dir(company_id), stored_filename), "wb") as handle:
        handle.write(data)
    return stored_filename


def path_for(company_id: int, stored_filename: str) -> str | None:
    """Absolute path for a stored attachment, or None if it's gone.

    Re-checks containment rather than trusting the stored name: even though
    save() generates it, a row is data, and a path built from data gets
    validated before it opens a file.
    """
    if not stored_filename:
        return None
    directory = _company_dir(company_id)
    path = os.path.abspath(os.path.join(directory, stored_filename))
    if not path.startswith(os.path.abspath(directory) + os.sep):
        return None
    return path if os.path.exists(path) else None


def delete(company_id: int, stored_filename: str) -> None:
    path = path_for(company_id, stored_filename)
    if path:
        try:
            os.remove(path)
        except OSError:
            pass  # already gone, or read-only volume — not worth failing a sync
