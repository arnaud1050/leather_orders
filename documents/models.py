"""
The `order_documents` table.

Named to avoid any collision with the legacy `documents` table (the fake
"Mockup"/"Invoice" placeholder rows) before `migrations.py` drops it on
existing installs.

`order_id` is a real foreign key into this app's `orders` table — see the
module docstring for why that's fine here even though `billing/` avoids
the equivalent (no circular import forces the indirection).
"""

from datetime import datetime, timezone

from models import db


def _utcnow() -> datetime:
    """Naive UTC 'now' — timestamps are naive UTC throughout this app (see
    communications/models.py's utcnow(), which this mirrors)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class DocumentType(db.Model):
    """A company-configurable document category (e.g. "Mockups",
    "Renderings"). Same hide-don't-delete shape as the root OrderType/
    SourceOption models: once a document references one, deleting it would
    orphan that document's label, so hiding (is_active=False) is the only
    way to retire one from new uploads while what's already tagged with it
    keeps its section on the order page. Module-owned rather than living in
    root models.py alongside OrderType/SourceOption, since categorizing
    documents is this module's own concern.
    """
    __tablename__ = "document_types"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=False)
    label = db.Column(db.String(120), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    @property
    def can_delete(self):
        return Document.query.filter_by(document_type_id=self.id).first() is None


class Document(db.Model):
    __tablename__ = "order_documents"

    id = db.Column(db.Integer, primary_key=True)
    # Denormalized, like communications' EmailThread.company_id — every
    # query filters by company first, and a tenant filter that depends on
    # remembering to join through order -> client -> company is one that
    # eventually gets skipped.
    company_id = db.Column(db.Integer, nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)
    # Optional — a company with no DocumentTypes defined never shows the
    # sectioned layout at all (see services.has_document_types), so this
    # stays nullable rather than needing a fallback category.
    document_type_id = db.Column(db.Integer, db.ForeignKey("document_types.id"))

    # Display only — never used to build a path. What's actually on disk is
    # named opaquely (see storage.py).
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(64), nullable=False)
    thumbnail_filename = db.Column(db.String(64), nullable=True)

    content_type = db.Column(db.String(100), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    uploaded_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    order = db.relationship("Order")
    document_type = db.relationship("DocumentType")
