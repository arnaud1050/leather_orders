"""
The public API of the showcase module.

Every function takes `company_id` first and filters on it (hard rule 1).
Functions that change something return an error string a person can act on,
or None — the admin and documents convention — and commit their own work.
Rule ids (SC…) refer to showcase/REQUIREMENTS.md.
"""

import hashlib
import secrets
from datetime import timedelta

import features
from models import db

from showcase import config, hooks, images, storage
from showcase.models import (
    VISIBILITIES, ShowcaseCategory, ShowcaseDismissal, ShowcaseExcludedOrderType,
    ShowcaseItem, ShowcaseKioskLink, ShowcasePhoto, ShowcaseSettings, ShowcaseSpecField,
    ShowcaseSpecValue, _utcnow,
)

FEATURE = "showcase"
TITLE_MAX = 160
DESCRIPTION_MAX = 4000
SPEC_VALUE_MAX = 200
LABEL_MAX = 80


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def get_settings(company_id: int) -> ShowcaseSettings:
    """The company's row, created on first read (SC2)."""
    row = db.session.get(ShowcaseSettings, company_id)
    if row is None:
        row = ShowcaseSettings(company_id=company_id, default_visibility="public")
        db.session.add(row)
        db.session.flush()
    return row


def set_default_visibility(company_id: int, visibility: str) -> str | None:
    if visibility not in VISIBILITIES:
        return "Choose one of the three visibilities."
    get_settings(company_id).default_visibility = visibility
    db.session.commit()
    return None


# ---------------------------------------------------------------------------
# Categories and spec fields — the same hide-don't-delete list, twice (SC3)
# ---------------------------------------------------------------------------

def list_rows(model, company_id: int) -> list:
    """Every row, hidden ones too — Settings needs them to unhide."""
    return model.query.filter_by(company_id=company_id).order_by(model.sort_order, model.id).all()


def active_rows(model, company_id: int) -> list:
    return [row for row in list_rows(model, company_id) if row.is_active]


def add_row(model, company_id: int, label: str) -> str | None:
    label = (label or "").strip()[:LABEL_MAX]
    if not label:
        return "A name is required."
    if any(row.label.strip().lower() == label.lower() for row in list_rows(model, company_id)):
        return f'"{label}" already exists.'
    db.session.add(model(
        company_id=company_id, label=label,
        sort_order=model.query.filter_by(company_id=company_id).count(),
    ))
    db.session.commit()
    return None


def _row(model, company_id: int, row_id: int):
    return model.query.filter_by(id=row_id, company_id=company_id).first()


def toggle_row(model, company_id: int, row_id: int):
    row = _row(model, company_id, row_id)
    if row is not None:
        row.is_active = not row.is_active
        db.session.commit()
    return row


def delete_row(model, company_id: int, row_id: int) -> tuple[str, bool] | None:
    """(label, deleted). A row in use is kept and reported, never deleted."""
    row = _row(model, company_id, row_id)
    if row is None:
        return None
    label = row.label
    if not row.can_delete:
        return label, False
    if model is ShowcaseSpecField:
        ShowcaseSpecValue.query.filter_by(field_id=row.id).delete()
    db.session.delete(row)
    db.session.commit()
    return label, True


def reorder_rows(model, company_id: int, ordered_ids: list[int]) -> None:
    """Ids not this company's are skipped: a fetch body is data, not a form
    the server built."""
    by_id = {row.id: row for row in list_rows(model, company_id)}
    for index, row_id in enumerate(ordered_ids):
        if row_id in by_id:
            by_id[row_id].sort_order = index
    db.session.commit()


# ---------------------------------------------------------------------------
# Order types that are never showcased (SC4)
# ---------------------------------------------------------------------------

def excluded_type_ids(company_id: int) -> set[int]:
    return {
        row.order_type_id
        for row in ShowcaseExcludedOrderType.query.filter_by(company_id=company_id)
    }


def set_excluded_types(company_id: int, chosen: set[int]) -> None:
    """Make the excluded set exactly `chosen`, ignoring ids that aren't one
    of this company's order types."""
    valid = {t["id"] for t in hooks.order_types(company_id)}
    chosen = chosen & valid
    stale = ShowcaseExcludedOrderType.query.filter_by(company_id=company_id)
    if chosen:
        stale = stale.filter(ShowcaseExcludedOrderType.order_type_id.notin_(chosen))
    stale.delete(synchronize_session=False)
    for type_id in chosen - excluded_type_ids(company_id):
        db.session.add(ShowcaseExcludedOrderType(company_id=company_id, order_type_id=type_id))
    db.session.commit()


def is_excluded(company_id: int, order: dict) -> bool:
    return order.get("order_type_id") in excluded_type_ids(company_id)


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

def list_items(company_id: int, *, category_id: int | None = None,
               status: str | None = None, visibility: str | None = None) -> list[ShowcaseItem]:
    query = ShowcaseItem.query.filter_by(company_id=company_id)
    if category_id is not None:
        query = query.filter_by(category_id=category_id)
    if status is not None:
        query = query.filter_by(status=status)
    if visibility is not None:
        query = query.filter_by(visibility=visibility)
    return query.order_by(ShowcaseItem.updated_at.desc(), ShowcaseItem.id.desc()).all()


def get_item(company_id: int, item_id: int) -> ShowcaseItem | None:
    return ShowcaseItem.query.filter_by(id=item_id, company_id=company_id).first()


def item_for_order(company_id: int, order_id: int) -> ShowcaseItem | None:
    return ShowcaseItem.query.filter_by(company_id=company_id, order_id=order_id).first()


def item_for_source(company_id: int, source_ref: str) -> ShowcaseItem | None:
    """The piece an import already made from this source, if any (SC34)."""
    return ShowcaseItem.query.filter_by(company_id=company_id, source_ref=source_ref).first()


def items_imported_from(company_id: int, host: str) -> list[ShowcaseItem]:
    """Every piece an import brought in from `host` (SC37); pieces made in
    the app have no `source_ref` and never match."""
    prefix = host.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/"
    return (ShowcaseItem.query
            .filter(ShowcaseItem.company_id == company_id,
                    ShowcaseItem.source_ref.like(prefix + "%", escape="\\"))
            .order_by(ShowcaseItem.id).all())


def create_item(company_id: int, title: str, *, order_id: int | None = None,
                category_id: int | None = None,
                source_ref: str | None = None) -> ShowcaseItem:
    item = ShowcaseItem(
        company_id=company_id, order_id=order_id, source_ref=source_ref,
        title=((title or "").strip() or "Untitled piece")[:TITLE_MAX],
        category_id=category_id,
        visibility=get_settings(company_id).default_visibility,
        status="draft",
    )
    db.session.add(item)
    db.session.commit()
    return item


def start_for_order(company_id: int, order: dict) -> ShowcaseItem:
    """The order's draft, prefilled (SC8) — or the one it already has, so
    a double click can't make two (SC7)."""
    existing = item_for_order(company_id, order["id"])
    if existing is not None:
        return existing
    type_label = (order.get("order_type_label") or "").strip().lower()
    category = next(
        (c for c in active_rows(ShowcaseCategory, company_id)
         if type_label and c.label.strip().lower() == type_label),
        None,
    )
    dismissal = ShowcaseDismissal.query.filter_by(
        company_id=company_id, order_id=order["id"]).first()
    if dismissal is not None:
        db.session.delete(dismissal)
    return create_item(company_id, order.get("item") or "", order_id=order["id"],
                       category_id=category.id if category else None)


def offered_categories(company_id: int, item: ShowcaseItem) -> list[ShowcaseCategory]:
    """Active categories, plus the item's own if it's since been hidden, so
    saving the form can't silently clear it."""
    rows = active_rows(ShowcaseCategory, company_id)
    if item.category is not None and item.category not in rows:
        rows = sorted(rows + [item.category], key=lambda c: (c.sort_order, c.id))
    return rows


def spec_rows(company_id: int, item: ShowcaseItem) -> list[tuple[ShowcaseSpecField, str]]:
    """(field, value) for each active field, in order — the editor's rows."""
    values = {v.field_id: v.value for v in item.spec_values}
    return [(f, values.get(f.id, "")) for f in active_rows(ShowcaseSpecField, company_id)]


def shown_specs(company_id: int, item: ShowcaseItem) -> list[tuple[str, str]]:
    """(label, value) pairs anyone outside the app may see: active fields
    with a value, in Settings order (SC5)."""
    return [(f.label, v) for f, v in spec_rows(company_id, item) if v.strip()]


def details_error(company_id: int, item: ShowcaseItem, *, title: str, description: str,
                  category_id: int | None, visibility: str) -> str | None:
    """Why the details form would be refused, or None. Also asked before a
    new piece exists (`item` unsaved), so a refused form saves nothing."""
    title = (title or "").strip()
    if not title:
        return "A title is required."
    if len(title) > TITLE_MAX:
        return f"Keep the title under {TITLE_MAX} characters."
    if len((description or "").strip()) > DESCRIPTION_MAX:
        return f"Keep the description under {DESCRIPTION_MAX} characters."
    if visibility not in VISIBILITIES:
        return "Choose one of the three visibilities."
    if category_id is not None and category_id not in {
            c.id for c in offered_categories(company_id, item)}:
        return "That category isn't available any more. Pick another."
    return None


def new_item(company_id: int) -> ShowcaseItem:
    """A piece for the new-piece form, never added to the session: nothing
    is saved until the form is (create_item)."""
    return ShowcaseItem(company_id=company_id, title="", status="draft",
                        visibility=get_settings(company_id).default_visibility)


def update_details(company_id: int, item: ShowcaseItem, *, title: str, description: str,
                   category_id: int | None, visibility: str,
                   specs: dict[int, str]) -> str | None:
    """Save the form. Nothing is written when anything is refused."""
    error = details_error(company_id, item, title=title, description=description,
                          category_id=category_id, visibility=visibility)
    if error is not None:
        return error

    description = (description or "").strip()
    item.title = title.strip()
    item.description = description or None
    item.category_id = category_id
    item.visibility = visibility
    active = {f.id for f in active_rows(ShowcaseSpecField, company_id)}
    existing = {v.field_id: v for v in item.spec_values}
    for field_id, raw in specs.items():
        if field_id not in active:
            continue
        value = (raw or "").strip()[:SPEC_VALUE_MAX]
        if field_id in existing:
            existing[field_id].value = value
        elif value:
            item.spec_values.append(ShowcaseSpecValue(field_id=field_id, value=value))
    item.updated_at = _utcnow()
    db.session.commit()
    return None


def publish(company_id: int, item: ShowcaseItem) -> str | None:
    """A piece goes out with a title and at least one photo (SC9)."""
    if not item.title.strip():
        return "Add a title before publishing."
    if not item.photos:
        return "Add at least one photo before publishing."
    if item.status != "published":
        item.status = "published"
        item.published_at = _utcnow()
        item.updated_at = _utcnow()
        db.session.commit()
    return None


def withdraw(company_id: int, item: ShowcaseItem) -> None:
    if item.status == "published":
        item.status = "withdrawn"
        item.updated_at = _utcnow()
        db.session.commit()


def delete_item(company_id: int, item: ShowcaseItem) -> str | None:
    """Drafts and withdrawn items only (SC10): a published one is withdrawn
    first, so deleting is never also an unannounced unpublish."""
    if item.status == "published":
        return "Withdraw this piece before deleting it."
    if item.publication is not None and item.publication.on_site:
        # SC46: atelier would lose the only record that the card is there.
        return "Take this piece off the website before deleting it."
    for photo in item.photos:
        storage.delete(company_id, photo.stored_filename)
        storage.delete(company_id, photo.thumbnail_filename)
    db.session.delete(item)
    db.session.commit()
    return None


# ---------------------------------------------------------------------------
# Photos
# ---------------------------------------------------------------------------

def storage_used(company_id: int) -> int:
    total = db.session.query(db.func.sum(ShowcasePhoto.size_bytes)).filter(
        ShowcasePhoto.company_id == company_id).scalar()
    return int(total or 0)


def get_photo(company_id: int, photo_id: int) -> ShowcasePhoto | None:
    return ShowcasePhoto.query.filter_by(id=photo_id, company_id=company_id).first()


def add_photo(company_id: int, item: ShowcaseItem, data: bytes, name: str,
              *, source_document_id: int | None = None,
              source_ref: str | None = None) -> str | None:
    """Re-encode, strip, store (SC11). Error string or None."""
    if len(item.photos) >= config.MAX_PHOTOS_PER_ITEM:
        return f"A piece has at most {config.MAX_PHOTOS_PER_ITEM} photos."
    try:
        processed = images.process(data, name)
    except images.ImageError as exc:
        return str(exc)
    size = len(processed.full) + len(processed.thumbnail)
    if storage_used(company_id) + size > config.MAX_TOTAL_BYTES:
        return (f"{name} would put the showcase over its "
                f"{config.MAX_TOTAL_BYTES / 1_000_000:.0f}MB of storage. "
                "Delete a photo or a piece first.")
    photo = ShowcasePhoto(
        company_id=company_id,
        stored_filename=storage.save(company_id, processed.full),
        thumbnail_filename=storage.save(company_id, processed.thumbnail),
        width=processed.width, height=processed.height, size_bytes=size,
        position=len(item.photos), source_document_id=source_document_id,
        source_ref=source_ref, content_sha256=hashlib.sha256(processed.full).hexdigest(),
    )
    item.photos.append(photo)
    item.updated_at = _utcnow()
    db.session.commit()
    return None


def add_order_photos(company_id: int, item: ShowcaseItem, document_ids: list[int]) -> list[str]:
    """Copy chosen order documents in, through the host (SC12). Errors per
    document; the rest still land."""
    if item.order_id is None:
        return ["This piece isn't linked to an order."]
    already = {p.source_document_id for p in item.photos}
    errors = []
    for document_id in document_ids:
        if document_id in already:
            continue
        image = hooks.load_order_image(company_id, item.order_id, document_id)
        if image is None:
            errors.append("One of those documents isn't on this order any more.")
            continue
        error = add_photo(company_id, item, image["data"], image["filename"],
                          source_document_id=document_id)
        if error:
            errors.append(error)
        else:
            already.add(document_id)
    return errors


def unused_order_images(company_id: int, item: ShowcaseItem) -> list[dict]:
    """The order's images not yet copied into this item — the picker."""
    if item.order_id is None:
        return []
    used = {p.source_document_id for p in item.photos}
    return [i for i in hooks.order_images(company_id, item.order_id) if i["id"] not in used]


def delete_photo(company_id: int, item: ShowcaseItem, photo_id: int) -> str | None:
    photo = next((p for p in item.photos if p.id == photo_id), None)
    if photo is None:
        return None
    if item.status == "published" and len(item.photos) == 1:
        return "A published piece needs a photo. Add another first, or withdraw it."
    storage.delete(company_id, photo.stored_filename)
    storage.delete(company_id, photo.thumbnail_filename)
    item.photos.remove(photo)
    for index, remaining in enumerate(item.photos):
        remaining.position = index
    item.updated_at = _utcnow()
    db.session.commit()
    return None


def reorder_photos(company_id: int, item: ShowcaseItem, ordered_ids: list[int]) -> None:
    """The first photo is the cover. Ids not on this item are ignored, and
    any left out keep their relative order after the ones given."""
    by_id = {p.id: p for p in item.photos}
    ordered = [by_id[i] for i in ordered_ids if i in by_id]
    ordered += [p for p in item.photos if p not in ordered]
    for index, photo in enumerate(ordered):
        photo.position = index
    item.updated_at = _utcnow()
    db.session.commit()


# ---------------------------------------------------------------------------
# The reminder: delivered orders nobody has decided about yet (SC20–SC23)
# ---------------------------------------------------------------------------

def _reminder_start(company_id: int):
    since = features.enabled_since(company_id, FEATURE)
    return since.date() if since else None


def awaiting_orders(company_id: int) -> list[dict]:
    """Delivered on or after the day Showcase was switched on, of a type
    that isn't excluded, with neither an item nor a dismissal. Derived on
    every call, never stored (hard rule 10)."""
    since = _reminder_start(company_id)
    if since is None:
        return []
    excluded = excluded_type_ids(company_id)
    decided = {
        order_id for (order_id,) in db.session.query(ShowcaseItem.order_id).filter(
            ShowcaseItem.company_id == company_id, ShowcaseItem.order_id.isnot(None))
    } | {
        order_id for (order_id,) in db.session.query(ShowcaseDismissal.order_id).filter_by(
            company_id=company_id)
    }
    return sorted(
        (o for o in hooks.delivered_orders(company_id)
         if o["delivered_on"] is not None and o["delivered_on"] >= since
         and o.get("order_type_id") not in excluded and o["id"] not in decided),
        key=lambda o: o["delivered_on"], reverse=True,
    )


def is_dismissed(company_id: int, order_id: int) -> bool:
    return ShowcaseDismissal.query.filter_by(
        company_id=company_id, order_id=order_id).first() is not None


def is_awaiting(company_id: int, order: dict) -> bool:
    return any(o["id"] == order["id"] for o in awaiting_orders(company_id))


def dismiss(company_id: int, order_id: int, user_id: int | None) -> None:
    if not is_dismissed(company_id, order_id):
        db.session.add(ShowcaseDismissal(
            company_id=company_id, order_id=order_id, dismissed_by=user_id))
        db.session.commit()


def undismiss(company_id: int, order_id: int) -> None:
    ShowcaseDismissal.query.filter_by(company_id=company_id, order_id=order_id).delete()
    db.session.commit()


def order_tab_available(company_id: int, order: dict | None) -> bool:
    """The order page's Showcase tab (SC6): the feature is on, and the order
    either already has an item or is delivered and of an allowed type."""
    if order is None or not features.is_enabled(company_id, FEATURE):
        return False
    if item_for_order(company_id, order["id"]) is not None:
        return True
    return bool(order.get("delivered")) and not is_excluded(company_id, order)


# ---------------------------------------------------------------------------
# Catalog mode (SC25–SC29)
# ---------------------------------------------------------------------------

CATALOG_VISIBILITIES = ("public", "in_person")
KIOSK_TOUCH_INTERVAL = timedelta(minutes=5)


def catalog_items(company_id: int) -> list[ShowcaseItem]:
    """What catalog mode shows: published pieces that are public or in
    person only, newest first (SC25). Never a draft, a withdrawn piece or a
    private one, and never a piece without a photo."""
    items = (
        ShowcaseItem.query.filter(
            ShowcaseItem.company_id == company_id,
            ShowcaseItem.status == "published",
            ShowcaseItem.visibility.in_(CATALOG_VISIBILITIES),
        )
        .order_by(ShowcaseItem.published_at.desc(), ShowcaseItem.id.desc())
        .all()
    )
    return [item for item in items if item.photos]


def in_catalog(company_id: int, photo: ShowcasePhoto) -> bool:
    """Whether a photo belongs to a piece catalog mode shows — the only
    photos a kiosk link may serve (SC28)."""
    item = photo.item
    return (photo.company_id == company_id and item.status == "published"
            and item.visibility in CATALOG_VISIBILITIES)


def catalog_payload(company_id: int, photo_url, thumb_url) -> dict:
    """The catalog as plain data for the page's JSON (SC26): no ids beyond
    what the page needs, nothing from the order, no price, no client.
    `photo_url(photo)` / `thumb_url(photo)` build the links, which differ
    between a kiosk link and a signed-in user."""
    items = catalog_items(company_id)
    pieces, category_ids = [], set()
    for item in items:
        if item.category is not None:
            category_ids.add(item.category_id)
        pieces.append({
            "id": item.id,
            "title": item.title,
            "category_id": item.category_id,
            "category": item.category.label if item.category else "",
            "description": item.description or "",
            "specs": [[label, value] for label, value in shown_specs(company_id, item)],
            "photos": [
                {"full": photo_url(p), "thumb": thumb_url(p), "w": p.width, "h": p.height}
                for p in item.photos
            ],
        })
    categories = [
        {"id": c.id, "label": c.label}
        for c in list_rows(ShowcaseCategory, company_id) if c.id in category_ids
    ]
    return {"pieces": pieces, "categories": categories}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def list_kiosk_links(company_id: int) -> list[ShowcaseKioskLink]:
    return (
        ShowcaseKioskLink.query.filter_by(company_id=company_id, revoked_at=None)
        .order_by(ShowcaseKioskLink.created_at.desc(), ShowcaseKioskLink.id.desc())
        .all()
    )


def create_kiosk_link(company_id: int, name: str) -> tuple[ShowcaseKioskLink, str]:
    """A new link and its token. The token is returned once and never
    stored (SC27): only its hash is."""
    token = secrets.token_urlsafe(24)
    link = ShowcaseKioskLink(
        company_id=company_id,
        name=((name or "").strip() or "Catalog device")[:LABEL_MAX],
        token_hash=_hash(token),
    )
    db.session.add(link)
    db.session.commit()
    return link, token


def revoke_kiosk_link(company_id: int, link_id: int) -> ShowcaseKioskLink | None:
    link = ShowcaseKioskLink.query.filter_by(
        id=link_id, company_id=company_id, revoked_at=None).first()
    if link is not None:
        link.revoked_at = _utcnow()
        db.session.commit()
    return link


def resolve_kiosk(token: str) -> ShowcaseKioskLink | None:
    """The live link for a token, or None: unknown, deleted, or its company
    no longer has the feature or is deactivated (SC29). Notes the use, at
    most every few minutes, so the photos a page loads don't each write."""
    if not token or len(token) > 100:
        return None
    link = ShowcaseKioskLink.query.filter_by(token_hash=_hash(token), revoked_at=None).first()
    if link is None or not features.is_enabled(link.company_id, FEATURE):
        return None
    if not hooks.company_active(link.company_id):
        return None
    now = _utcnow()
    if link.last_used_at is None or now - link.last_used_at > KIOSK_TOUCH_INTERVAL:
        link.last_used_at = now
        db.session.commit()
    return link
