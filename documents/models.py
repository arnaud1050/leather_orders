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


class Document(db.Model):
    __tablename__ = "order_documents"

    id = db.Column(db.Integer, primary_key=True)
    # Denormalized, like communications' EmailThread.company_id — every
    # query filters by company first, and a tenant filter that depends on
    # remembering to join through order -> client -> company is one that
    # eventually gets skipped.
    company_id = db.Column(db.Integer, nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False)

    # Display only — never used to build a path. What's actually on disk is
    # named opaquely (see storage.py).
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(64), nullable=False)
    thumbnail_filename = db.Column(db.String(64), nullable=True)

    content_type = db.Column(db.String(100), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    uploaded_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    order = db.relationship("Order")
