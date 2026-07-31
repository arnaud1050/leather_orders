"""
Public API for the documents module. Every function takes `company_id` (or
an already-tenant-checked `order_id`) first — the rest of the app should
never reach into `documents.models` or `documents.storage` directly.
"""

from dataclasses import dataclass, field

from models import db

from documents import storage, thumbnails, validation
from documents.models import Document, DocumentType
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


def upload(
    company_id: int, order_id: int, files: list[tuple[str, bytes]],
    document_type_id: int | None = None,
) -> UploadResult:
    """Validate and store each (filename, data) pair.

    Partial success is expected and reported, not treated as a failure —
    3 of 5 files fitting under quota while 2 don't is a normal outcome for
    a multi-file upload, not an all-or-nothing transaction. Usage is
    re-read after each save so the quota check inside `validate_upload`
    accounts for files already saved earlier in the same batch.

    `document_type_id`, if given, must belong to this company — resolved
    once up front rather than trusted as-is, so a stale or tampered id (a
    type deleted moments ago, or one from someone else's session) quietly
    lands the upload with no type instead of failing the whole batch.
    """
    resolved_type_id = None
    if document_type_id is not None:
        resolved_type_id = (
            DocumentType.query.filter_by(id=document_type_id, company_id=company_id)
            .with_entities(DocumentType.id).scalar()
        )

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
            document_type_id=resolved_type_id,
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


# ---------------------------------------------------------------------------
# Document types — company-configurable categories a document can belong to.
# Same hide-don't-delete shape as OrderType/SourceOption in root models.py;
# see DocumentType's docstring for why it lives here instead.
# ---------------------------------------------------------------------------

def list_document_types(company_id: int) -> list[DocumentType]:
    """Every type, active or hidden — for the settings page, which needs to
    show hidden ones too (with an Unhide option), not just offer new ones."""
    return (
        DocumentType.query.filter_by(company_id=company_id)
        .order_by(DocumentType.sort_order)
        .all()
    )


def has_document_types(company_id: int) -> bool:
    """Whether the order page should render the sectioned layout at all.

    True the moment one type exists, active or not — a company that's
    hidden every type still has documents filed under them, and those need
    a section to live in even though nothing new can be added there.
    """
    return DocumentType.query.filter_by(company_id=company_id).first() is not None


def add_document_type(company_id: int, label: str) -> DocumentType | None:
    """None on a blank label, or one that (case-insensitively) matches a
    type this company already has — active or hidden, since two sections
    reading the same name is confusing either way, and a hidden type is
    still a real row someone could unhide back into a collision."""
    label = (label or "").strip()
    if not label or is_duplicate_document_type_label(company_id, label):
        return None
    next_sort_order = DocumentType.query.filter_by(company_id=company_id).count()
    document_type = DocumentType(company_id=company_id, label=label, sort_order=next_sort_order)
    db.session.add(document_type)
    db.session.commit()
    return document_type


def is_duplicate_document_type_label(company_id: int, label: str) -> bool:
    existing_labels = {
        t.label.strip().lower()
        for t in DocumentType.query.filter_by(company_id=company_id)
    }
    return label.strip().lower() in existing_labels


def toggle_document_type(company_id: int, document_type_id: int) -> None:
    document_type = DocumentType.query.filter_by(
        id=document_type_id, company_id=company_id
    ).first()
    if document_type is not None:
        document_type.is_active = not document_type.is_active
        db.session.commit()


def delete_document_type(company_id: int, document_type_id: int) -> None:
    document_type = DocumentType.query.filter_by(
        id=document_type_id, company_id=company_id
    ).first()
    if document_type is not None and document_type.can_delete:
        db.session.delete(document_type)
        db.session.commit()


def reorder_document_types(company_id: int, ordered_ids: list[int]) -> None:
    """Set sort_order from position in `ordered_ids`.

    Ids that don't belong to this company are silently skipped rather than
    trusted — the request comes from a fetch() call, not a form the server
    built, so it's treated as data, not as already-validated input.
    """
    types_by_id = {
        t.id: t for t in DocumentType.query.filter_by(company_id=company_id).all()
    }
    for index, type_id in enumerate(ordered_ids):
        document_type = types_by_id.get(type_id)
        if document_type is not None:
            document_type.sort_order = index
    db.session.commit()


@dataclass
class DocumentSection:
    document_type: DocumentType | None
    label: str
    can_upload: bool
    documents: list[Document] = field(default_factory=list)


def sections_for_order(order_id: int, company_id: int) -> list[DocumentSection]:
    """The sectioned view of one order's documents.

    Active types (in sort_order) first, each upload-able. Any type that's
    since been hidden but still has a document on *this* order keeps its
    section too — merged in and re-sorted by sort_order, same "active ∪
    referenced" pattern Order.order_type / Client.sources already use —
    just without an add tile, since hiding means "not offered for new use".
    A trailing "Other" section collects anything with no type at all
    (pre-dating this feature, or deliberately left uncategorized) — always
    present once any type exists, with its own add tile, same as an active
    type's section; unlike a hidden type, "uncategorized" is always a valid
    thing to upload as, not a state something merely ended up in.
    """
    documents = list_for_order(order_id)
    by_type_id: dict[int, list[Document]] = {}
    untyped: list[Document] = []
    for document in documents:
        if document.document_type_id is None:
            untyped.append(document)
        else:
            by_type_id.setdefault(document.document_type_id, []).append(document)

    active_types = [
        t for t in DocumentType.query.filter_by(company_id=company_id, is_active=True)
    ]
    referenced_hidden_types = (
        DocumentType.query.filter(
            DocumentType.company_id == company_id,
            DocumentType.is_active.is_(False),
            DocumentType.id.in_(by_type_id.keys()),
        ).all()
        if by_type_id else []
    )
    active_ids = {t.id for t in active_types}
    all_types = sorted(active_types + referenced_hidden_types, key=lambda t: t.sort_order)

    sections = [
        DocumentSection(
            document_type=t, label=t.label, can_upload=t.id in active_ids,
            documents=by_type_id.get(t.id, []),
        )
        for t in all_types
    ]
    sections.append(DocumentSection(
        document_type=None, label="Other", can_upload=True, documents=untyped,
    ))
    return sections
