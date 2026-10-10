"""
The showcase tables.

Imports only `db` from the host (hard rule 4). An order is referred to by a
plain integer `order_id` this module never resolves itself: everything it
needs to know about an order arrives through the host's adapter
(`showcase_adapter.py`), so there is no foreign key into `orders` and no
import of `Order`. `company_id` foreign keys name the table by string, the
same allowance `documents/` and `features/` use.
"""

import uuid
from datetime import datetime, timezone

from models import db

VISIBILITIES = ("private", "in_person", "public")
VISIBILITY_LABELS = {
    "private": "Private",
    "in_person": "In person only",
    "public": "Public",
}
STATUSES = ("draft", "published", "withdrawn")
STATUS_LABELS = {"draft": "Draft", "published": "Published", "withdrawn": "Withdrawn"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_public_key() -> str:
    """A piece's or photo's key on the studio's website (SC40): random, so
    it says nothing about how many pieces exist, and never changed."""
    return uuid.uuid4().hex


class ShowcaseSettings(db.Model):
    """One row per company, created lazily on first read — the same
    "created on first use" contract as the billing letterhead, so a fresh
    tenant needs no seed row."""

    __tablename__ = "showcase_settings"

    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), primary_key=True)
    # Public unless the studio changes it (decided 2026-10-05): excluded
    # order types already keep NDA and white-label work out.
    default_visibility = db.Column(db.String(20), nullable=False, default="public")


class _LabelledList:
    """The shape every company-configured list here shares with OrderType
    and DocumentType: ordered, hidden rather than deleted once in use."""

    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(80), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class ShowcaseCategory(_LabelledList, db.Model):
    """Bags, Wallets… Matched to a website's own categories by name."""

    __tablename__ = "showcase_categories"

    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False, index=True)

    @property
    def can_delete(self) -> bool:
        return ShowcaseItem.query.filter_by(category_id=self.id).first() is None


class ShowcaseSpecField(_LabelledList, db.Model):
    """One of the studio's fixed spec labels (Dimensions, Leather…). Every
    item shows the same fields in the same order; a blank value is simply
    not shown."""

    __tablename__ = "showcase_spec_fields"

    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False, index=True)

    @property
    def can_delete(self) -> bool:
        return (
            ShowcaseSpecValue.query.filter_by(field_id=self.id)
            .filter(ShowcaseSpecValue.value != "").first() is None
        )


class ShowcaseItem(db.Model):
    """One finished piece in the portfolio."""

    __tablename__ = "showcase_items"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False, index=True)
    # Optional: stock and market pieces have no order. At most one item per
    # order (services enforce it). Never resolved here — see the docstring.
    order_id = db.Column(db.Integer, index=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.Text)
    category_id = db.Column(db.Integer, db.ForeignKey("showcase_categories.id"))
    visibility = db.Column(db.String(20), nullable=False, default="public")
    status = db.Column(db.String(20), nullable=False, default="draft")
    published_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)
    # Where an imported piece came from — the website card it was copied
    # from, as "<host><first photo's path>" (scripts/import_showcase_from_site.py,
    # SC34). Null for everything made in the app. Lets the import skip what
    # it already brought in, and lets the website sync (roadmap step 4)
    # link a piece to its existing card instead of posting a duplicate.
    source_ref = db.Column(db.String(255))
    # Its key on the studio's website (SC40), sent with every call so the
    # site can follow the piece through any edit.
    public_key = db.Column(db.String(32), index=True, default=new_public_key)

    category = db.relationship("ShowcaseCategory")
    photos = db.relationship(
        "ShowcasePhoto", back_populates="item", cascade="all, delete-orphan",
        order_by="ShowcasePhoto.position",
    )
    spec_values = db.relationship(
        "ShowcaseSpecValue", back_populates="item", cascade="all, delete-orphan",
    )
    publication = db.relationship(
        "ShowcasePublication", back_populates="item", uselist=False,
        cascade="all, delete-orphan",
    )

    @property
    def cover(self):
        return self.photos[0] if self.photos else None

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @property
    def visibility_label(self) -> str:
        return VISIBILITY_LABELS.get(self.visibility, self.visibility)


class ShowcaseSpecValue(db.Model):
    __tablename__ = "showcase_spec_values"
    __table_args__ = (db.UniqueConstraint("item_id", "field_id", name="uq_showcase_spec_value"),)

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey("showcase_items.id"), nullable=False, index=True)
    field_id = db.Column(db.Integer, db.ForeignKey("showcase_spec_fields.id"), nullable=False)
    value = db.Column(db.String(200), nullable=False, default="")

    item = db.relationship("ShowcaseItem", back_populates="spec_values")


class ShowcasePhoto(db.Model):
    """The module's own re-encoded copy of a photo, EXIF stripped.

    A copy rather than a reference to an order document, so deleting that
    document can't break a published item (SC12).
    """

    __tablename__ = "showcase_photos"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False, index=True)
    item_id = db.Column(db.Integer, db.ForeignKey("showcase_items.id"), nullable=False, index=True)
    stored_filename = db.Column(db.String(64), nullable=False)
    thumbnail_filename = db.Column(db.String(64), nullable=False)
    width = db.Column(db.Integer, nullable=False)
    height = db.Column(db.Integer, nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    # The order document it was copied from, if any — only so the picker
    # stops offering a document already added. Not a foreign key: the
    # document may be deleted, and the photo must outlive it.
    source_document_id = db.Column(db.Integer)
    # Where an imported photo came from on the studio's website,
    # "<host><path>" (SC39), so the website sync can adopt the site's own
    # copy instead of uploading it again. Null for photos added in the app.
    source_ref = db.Column(db.String(255))
    # Its key on the studio's website (SC40), so the site keeps a photo it
    # already holds through a reorder instead of receiving it again.
    public_key = db.Column(db.String(32), index=True, default=new_public_key)
    # SHA-256 of the stored full-size file, which never changes once
    # written: how the website tells a photo it holds from a new one.
    # Filled when the photo is added; older rows on first need (website.py).
    content_sha256 = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    item = db.relationship("ShowcaseItem", back_populates="photos")


class ShowcaseDismissal(db.Model):
    """An order the studio decided not to showcase. Ends its reminder."""

    __tablename__ = "showcase_dismissals"
    __table_args__ = (db.UniqueConstraint("company_id", "order_id", name="uq_showcase_dismissal"),)

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False, index=True)
    order_id = db.Column(db.Integer, nullable=False)
    dismissed_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    dismissed_by = db.Column(db.Integer)


class ShowcaseKioskLink(db.Model):
    """A link that opens catalog mode on one device, without signing in.

    Only a hash of the token is stored: the link itself is shown once, at
    creation, and a database copy can't be turned back into a working URL.
    One per device, so losing a tablet means deleting that one link.
    Deleting sets `revoked_at` rather than removing the row.
    """

    __tablename__ = "showcase_kiosk_links"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    last_used_at = db.Column(db.DateTime)
    revoked_at = db.Column(db.DateTime)


class ShowcaseExcludedOrderType(db.Model):
    """An order type that is never showcased (an NDA, a white-label run).

    Kept here rather than as a column on the host's `order_types`, so the
    module doesn't reach into host schema.
    """

    __tablename__ = "showcase_excluded_order_types"
    __table_args__ = (
        db.UniqueConstraint("company_id", "order_type_id", name="uq_showcase_excluded_type"),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False, index=True)
    order_type_id = db.Column(db.Integer, nullable=False)


class ShowcaseWebsite(db.Model):
    """A company's connection to its website (SC41): where to send, and the
    secret both sides sign with, encrypted at rest (showcase/crypto.py).
    One per company; no row means no website."""

    __tablename__ = "showcase_websites"

    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), primary_key=True)
    endpoint_url = db.Column(db.String(500), nullable=False)
    secret_encrypted = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    # The last "Send test": what the site said it is, or why it failed.
    checked_at = db.Column(db.DateTime)
    site_name = db.Column(db.String(200))
    max_photos = db.Column(db.Integer)
    check_error = db.Column(db.Text)


class ShowcasePublication(db.Model):
    """What was last sent to the website for one piece (SC42). A record of
    what happened, never a status: whether the piece has changed since is
    worked out by comparing it with `sent_hash` (hard rule 10)."""

    __tablename__ = "showcase_publications"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False, index=True)
    item_id = db.Column(db.Integer, db.ForeignKey("showcase_items.id"), nullable=False,
                        unique=True)
    # The website has a card for it (sent, or linked to an imported card).
    on_site = db.Column(db.Boolean, nullable=False, default=False)
    # The card came from the one-off import and was linked, not created.
    linked = db.Column(db.Boolean, nullable=False, default=False)
    # An imported piece the studio chose to send as a new card (SC45).
    send_as_new = db.Column(db.Boolean, nullable=False, default=False)
    # The site's own admin deleted the card; it isn't re-created unasked.
    removed_on_site = db.Column(db.Boolean, nullable=False, default=False)
    # The hash of the piece as last sent, and its photos' keys and hashes,
    # so a later send carries only the photos the site doesn't hold.
    sent_hash = db.Column(db.String(64))
    sent_photos = db.Column(db.Text)
    sent_at = db.Column(db.DateTime)
    last_error = db.Column(db.Text)
    last_attempt_at = db.Column(db.DateTime)

    item = db.relationship("ShowcaseItem", back_populates="publication")
