"""
Public API for the documents module. Every function takes `company_id` (or
an already-tenant-checked `order_id`) first — the rest of the app should
never reach into `documents.models` or `documents.storage` directly.
"""

from dataclasses import dataclass

from models import db

from documents import storage, thumbnails, validation
from documents.models import Document
from documents.validation import ValidationError


def list_for_order(order_id: int) -> list[Document]:
    return (
        Document.query.filter_by(order_id=order_id)
        .order_by(Document.uploaded_at.desc())
        .all()
    )


def get_for_order(order_id: int, document_id: int) -> Document | None:
    return Document.query.filter_by(id=document_id, order_id=order_id).first()


def usage_for_company(company_id: int) -> int:
    total = db.session.query(db.func.sum(Document.size_bytes)).filter(
        Document.company_id == company_id
    ).scalar()
    return int(total or 0)


@dataclass
class UploadResult:
    saved: list[Document]
    errors: list[str]


def upload(company_id: int, order_id: int, files: list[tuple[str, bytes]]) -> UploadResult:
    """Validate and store each (filename, data) pair.

    Partial success is expected and reported, not treated as a failure —
    3 of 5 files fitting under quota while 2 don't is a normal outcome for
    a multi-file upload, not an all-or-nothing transaction. Usage is
    re-read after each save so the quota check inside `validate_upload`
    accounts for files already saved earlier in the same batch.
    """
    saved: list[Document] = []
    errors: list[str] = []

    for filename, data in files:
        if not filename:
            continue
        try:
            content_type = validation.validate_upload(
                filename, data, current_usage_bytes=usage_for_company(company_id)
            )
        except ValidationError as exc:
            errors.append(str(exc))
            continue

        stored_filename = storage.save(company_id, filename, data)
        thumbnail_bytes = thumbnails.generate(content_type, data)
        thumbnail_filename = (
            storage.save(company_id, "thumb.png", thumbnail_bytes, subdir="thumbnails")
            if thumbnail_bytes else None
        )

        document = Document(
            company_id=company_id,
            order_id=order_id,
            original_filename=filename,
            stored_filename=stored_filename,
            thumbnail_filename=thumbnail_filename,
            content_type=content_type,
            size_bytes=len(data),
        )
        db.session.add(document)
        saved.append(document)

    if saved:
        db.session.commit()

    return UploadResult(saved=saved, errors=errors)


def delete(document: Document) -> None:
    storage.delete(document.company_id, document.stored_filename)
    storage.delete(document.company_id, document.thumbnail_filename, subdir="thumbnails")
    db.session.delete(document)
    db.session.commit()
