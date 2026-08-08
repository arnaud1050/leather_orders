"""
Document bytes on disk.

Same shape as `communications/storage/attachment_storage.py`: the model
holds an opaque `stored_filename`, and only this file knows it means a path
under `config.DOCUMENT_DIR`. Pointing this at S3 later means rewriting
these functions, not the schema or the callers.

Files are namespaced per company (`<DOCUMENT_DIR>/<company_id>/...`), and
thumbnails live in a `thumbnails/` subdirectory of the same company folder
— same isolation, one level deeper.

Names on disk are generated, never taken from the upload. An uploaded file
called `../../app.py` is a real thing someone might try; the original name
is display-only metadata on the row.
"""

import os
import uuid


def _root_for(company_id: int, subdir: str | None) -> str:
    from documents import config

    parts = [config.DOCUMENT_DIR, str(int(company_id))]
    if subdir:
        parts.append(subdir)
    path = os.path.join(*parts)
    os.makedirs(path, exist_ok=True)
    return path


def save(company_id: int, original_filename: str, data: bytes, *, subdir: str | None = None) -> str:
    """Write bytes, return the opaque stored name to put on the row.

    The extension is carried over (so a browser/OS can guess the right
    handler) but sanitised to alphanumerics — everything else about the
    original filename is discarded.
    """
    _, extension = os.path.splitext(original_filename or "")
    extension = "".join(ch for ch in extension if ch.isalnum() or ch == ".")[:12]
    stored_filename = f"{uuid.uuid4().hex}{extension}"
    with open(os.path.join(_root_for(company_id, subdir), stored_filename), "wb") as handle:
        handle.write(data)
    return stored_filename


def path_for(company_id: int, stored_filename: str | None, *, subdir: str | None = None) -> str | None:
    """Absolute path for a stored file, or None if it's missing/invalid.

    Re-checks path containment rather than trusting the stored name: even
    though `save()` only ever generates safe names, a row is data, and a
    path built from data gets validated before it opens a file.
    """
    if not stored_filename:
        return None
    directory = _root_for(company_id, subdir)
    path = os.path.abspath(os.path.join(directory, stored_filename))
    if not path.startswith(os.path.abspath(directory) + os.sep):
        return None
    return path if os.path.exists(path) else None


def read(company_id: int, stored_filename: str | None, *, subdir: str | None = None) -> bytes | None:
    """The stored bytes, or None if the file is missing.

    Used where the file has to be handed to something other than the
    browser (mailing a document out as an attachment); serving one to the
    browser still goes through `path_for` + `send_file`, which streams.
    """
    path = path_for(company_id, stored_filename, subdir=subdir)
    if path is None:
        return None
    with open(path, "rb") as handle:
        return handle.read()


def delete(company_id: int, stored_filename: str | None, *, subdir: str | None = None) -> None:
    path = path_for(company_id, stored_filename, subdir=subdir)
    if path:
        try:
            os.remove(path)
        except OSError:
            pass  # already gone, or a read-only volume — not worth failing a delete over
