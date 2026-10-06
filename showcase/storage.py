"""
Photo bytes on disk.

Same shape as `documents/storage.py` (a copy, not an import: a module never
imports a sibling). The row holds an opaque generated name; only this file
knows it means `<SHOWCASE_DIR>/<company_id>/<name>`, and every path built
from a row is re-checked for containment before a file is opened.
"""

import os
import uuid

from showcase import config


def _root_for(company_id: int) -> str:
    path = os.path.join(config.SHOWCASE_DIR, str(int(company_id)))
    os.makedirs(path, exist_ok=True)
    return path


def save(company_id: int, data: bytes) -> str:
    stored_filename = f"{uuid.uuid4().hex}.jpg"
    with open(os.path.join(_root_for(company_id), stored_filename), "wb") as handle:
        handle.write(data)
    return stored_filename


def path_for(company_id: int, stored_filename: str | None) -> str | None:
    if not stored_filename:
        return None
    directory = _root_for(company_id)
    path = os.path.abspath(os.path.join(directory, stored_filename))
    if not path.startswith(os.path.abspath(directory) + os.sep):
        return None
    return path if os.path.exists(path) else None


def delete(company_id: int, stored_filename: str | None) -> None:
    path = path_for(company_id, stored_filename)
    if path:
        try:
            os.remove(path)
        except OSError:
            pass  # already gone, or a read-only volume — not worth failing over
