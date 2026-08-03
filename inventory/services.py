"""
Public API for the inventory module. Every function takes `company_id` (or an
already tenant-checked `order_id`) first — the rest of the app should never
reach into `inventory.models` directly.

Validation here is deliberately quiet, matching `add_order_line`/`add_payment`
in app.py: bad or missing input is a silent no-op (return None/False) rather
than a raised exception, since these are always called from a plain form POST
with nowhere interesting to show a stack trace.
"""

from models import db

from inventory.config import DEFAULT_UNIT, UNIT_CATALOG
from inventory.models import InventoryItem, InventoryType, InventoryUnit, OrderMaterial, OrderMaterialOther

# ---------------------------------------------------------------------------
# Units — which of config.UNIT_CATALOG's keys a company offers in its
# Add/Edit item dropdown, and in what order. See InventoryUnit's docstring:
# this list controls what's *offered*, never what add_item/edit_item
# *accept* (that's the full UNIT_CATALOG, always — see below).
# ---------------------------------------------------------------------------

def _ensure_default_unit(company_id: int) -> None:
    """Every company always has "Each" (config.DEFAULT_UNIT) selectable —
    seeded lazily on first read rather than at company creation, the same
    "create it on first use" idiom billing.profile_for() uses, so a company
    that's never touched /settings/inventory or /inventory still gets it
    the moment either page (or add_unit) is asked to list units.

    Assigned the next sort_order like any other unit (not pinned) — it just
    happens to land first by default since nothing else exists yet the
    first time this runs. Unlike hide/delete, position is not part of what
    makes "Each" special: a company can drag it anywhere in the list."""
    exists = InventoryUnit.query.filter_by(company_id=company_id, key=DEFAULT_UNIT).first()
    if exists is None:
        next_sort_order = InventoryUnit.query.filter_by(company_id=company_id).count()
        db.session.add(InventoryUnit(company_id=company_id, key=DEFAULT_UNIT, sort_order=next_sort_order))
        db.session.commit()


def list_units(company_id: int) -> list[InventoryUnit]:
    """Every configured unit, active or hidden, for the Units settings
    section, in the company's own sort_order — "Each" lands first by
    default (see _ensure_default_unit) but can be dragged elsewhere."""
    _ensure_default_unit(company_id)
    return (
        InventoryUnit.query.filter_by(company_id=company_id)
        .order_by(InventoryUnit.sort_order).all()
    )


def active_units(company_id: int) -> list[InventoryUnit]:
    _ensure_default_unit(company_id)
    return (
        InventoryUnit.query.filter_by(company_id=company_id, is_active=True)
        .order_by(InventoryUnit.sort_order).all()
    )


def available_catalog_units(company_id: int) -> list[dict]:
    """Catalog entries not yet added (active or hidden) for this company —
    what the "Add a unit" dropdown in Settings offers. "Each" is never
    offered here: every company always has it (_ensure_default_unit) and it
    can't be hidden or removed, so re-adding it would be meaningless.

    Sorted by `group` (Area, Count, Length, Volume, Weight) so the
    dropdown's <optgroup>s read alphabetically — a stable sort, so entries
    within one group keep UNIT_CATALOG's own order rather than also being
    re-sorted by label."""
    _ensure_default_unit(company_id)
    used_keys = {u.key for u in InventoryUnit.query.filter_by(company_id=company_id).all()}
    entries = [
        {"key": key, **info}
        for key, info in UNIT_CATALOG.items()
        if key != DEFAULT_UNIT and key not in used_keys
    ]
    return sorted(entries, key=lambda entry: entry["group"])


def add_unit(company_id: int, key: str) -> InventoryUnit | None:
    if key not in UNIT_CATALOG or key == DEFAULT_UNIT:
        return None
    _ensure_default_unit(company_id)
    if InventoryUnit.query.filter_by(company_id=company_id, key=key).first() is not None:
        return None
    next_sort_order = InventoryUnit.query.filter_by(company_id=company_id).count()
    unit = InventoryUnit(company_id=company_id, key=key, sort_order=next_sort_order)
    db.session.add(unit)
    db.session.commit()
    return unit


def toggle_unit(company_id: int, unit_id: int) -> None:
    unit = InventoryUnit.query.filter_by(id=unit_id, company_id=company_id).first()
    if unit is not None and not unit.is_default:
        unit.is_active = not unit.is_active
        db.session.commit()


def delete_unit(company_id: int, unit_id: int) -> None:
    unit = InventoryUnit.query.filter_by(id=unit_id, company_id=company_id).first()
    if unit is not None and unit.can_delete:
        db.session.delete(unit)
        db.session.commit()


def reorder_units(company_id: int, ordered_ids: list[int]) -> None:
    """Set sort_order from position in `ordered_ids`, same shape as
    documents.reorder_document_types. "Each" is draggable like any other
    row (see _settings_units.html) and is repositioned exactly like the
    rest — being exempt from hide/delete doesn't extend to position. Ids
    outside this company are silently skipped, same "the request is a
    fetch(), not a form the server built" reasoning as everywhere else this
    pattern is used."""
    units_by_id = {
        u.id: u for u in InventoryUnit.query.filter_by(company_id=company_id).all()
    }
    position = 0
    for unit_id in ordered_ids:
        unit = units_by_id.get(unit_id)
        if unit is None:
            continue
        unit.sort_order = position
        position += 1
    db.session.commit()


# ---------------------------------------------------------------------------
# Inventory types — company-configurable categories, same hide-don't-delete
# shape as OrderType/DocumentType.
# ---------------------------------------------------------------------------

def has_types(company_id: int) -> bool:
    """Whether the company has defined any InventoryType at all — active or
    hidden. Gates whether the master list's "No Type" filter button appears
    (see inventory/routes.py): with zero types ever defined, every item is
    untyped by definition, so a filter for that single, all-or-nothing
    bucket would be pointless. Existence, not is_active — same "does the
    category exist at all" question `has_order_types` asks in app.py."""
    return InventoryType.query.filter_by(company_id=company_id).first() is not None


def list_types(company_id: int) -> list[InventoryType]:
    """Every type, active or hidden — the settings page needs both."""
    return (
        InventoryType.query.filter_by(company_id=company_id)
        .order_by(InventoryType.sort_order)
        .all()
    )


def active_types(company_id: int) -> list[InventoryType]:
    return (
        InventoryType.query.filter_by(company_id=company_id, is_active=True)
        .order_by(InventoryType.sort_order)
        .all()
    )


def is_duplicate_type_label(company_id: int, label: str) -> bool:
    existing = {
        t.label.strip().lower()
        for t in InventoryType.query.filter_by(company_id=company_id)
    }
    return label.strip().lower() in existing


def add_type(company_id: int, label: str) -> InventoryType | None:
    label = (label or "").strip()
    if not label or is_duplicate_type_label(company_id, label):
        return None
    next_sort_order = InventoryType.query.filter_by(company_id=company_id).count()
    inventory_type = InventoryType(company_id=company_id, label=label, sort_order=next_sort_order)
    db.session.add(inventory_type)
    db.session.commit()
    return inventory_type


def toggle_type(company_id: int, inventory_type_id: int) -> None:
    inventory_type = InventoryType.query.filter_by(
        id=inventory_type_id, company_id=company_id
    ).first()
    if inventory_type is not None:
        inventory_type.is_active = not inventory_type.is_active
        db.session.commit()


def delete_type(company_id: int, inventory_type_id: int) -> None:
    inventory_type = InventoryType.query.filter_by(
        id=inventory_type_id, company_id=company_id
    ).first()
    if inventory_type is not None and inventory_type.can_delete:
        db.session.delete(inventory_type)
        db.session.commit()


# ---------------------------------------------------------------------------
# Inventory items — the master list at /inventory.
# ---------------------------------------------------------------------------

def list_items(company_id: int) -> list[InventoryItem]:
    """Every item, active or hidden, for the master page. Unsorted — the
    master page is sortable by clicking a column header (see
    inventory/routes.py's INVENTORY_SORT_KEYS), same server-side-sort
    convention as orders_list()/clients_list() in app.py, so ordering is a
    presentation concern the route owns, not this service."""
    return InventoryItem.query.filter_by(company_id=company_id).all()


def get_item(company_id: int, item_id: int) -> InventoryItem | None:
    return InventoryItem.query.filter_by(id=item_id, company_id=company_id).first()


def selectable_items(company_id: int, order_id: int | None = None) -> list[InventoryItem]:
    """Active items, plus any item already used on this order even if it's
    since been hidden — same active-∪-referenced pattern as
    Order.order_type/Client.sources, so removing/re-adding a material on an
    order doesn't require un-hiding the item first."""
    active = InventoryItem.query.filter_by(company_id=company_id, is_active=True).all()
    referenced = []
    if order_id is not None:
        referenced_ids = {
            m.inventory_item_id
            for m in OrderMaterial.query.filter_by(order_id=order_id).all()
        }
        if referenced_ids:
            referenced = InventoryItem.query.filter(
                InventoryItem.id.in_(referenced_ids)
            ).all()
    merged = sorted(
        {i.id: i for i in active + referenced}.values(),
        key=lambda i: (
            i.inventory_type.sort_order if i.inventory_type else 9999,
            i.name.lower(),
        ),
    )
    return merged


def add_item(
    company_id: int, name: str, unit: str, inventory_type_id: int | None,
    quantity_on_hand: float, unit_price: float,
) -> InventoryItem | None:
    name = (name or "").strip()
    if not name or unit not in UNIT_CATALOG:
        return None
    resolved_type_id = None
    if inventory_type_id is not None:
        resolved_type_id = (
            InventoryType.query.filter_by(id=inventory_type_id, company_id=company_id)
            .with_entities(InventoryType.id).scalar()
        )
    item = InventoryItem(
        company_id=company_id,
        inventory_type_id=resolved_type_id,
        name=name,
        unit=unit,
        quantity_on_hand=quantity_on_hand,
        unit_price=unit_price,
    )
    db.session.add(item)
    db.session.commit()
    return item


def edit_item(
    company_id: int, item_id: int, *, name: str, unit: str,
    inventory_type_id: int | None, quantity_on_hand: float, unit_price: float,
) -> InventoryItem | None:
    item = get_item(company_id, item_id)
    if item is None:
        return None
    name = (name or "").strip()
    if name:
        item.name = name
    if unit in UNIT_CATALOG:
        item.unit = unit
    if inventory_type_id is None:
        item.inventory_type_id = None
    else:
        resolved_type_id = (
            InventoryType.query.filter_by(id=inventory_type_id, company_id=company_id)
            .with_entities(InventoryType.id).scalar()
        )
        item.inventory_type_id = resolved_type_id
    item.quantity_on_hand = quantity_on_hand
    item.unit_price = unit_price
    db.session.commit()
    return item


def toggle_item(company_id: int, item_id: int) -> None:
    item = get_item(company_id, item_id)
    if item is not None:
        item.is_active = not item.is_active
        db.session.commit()


def delete_item(company_id: int, item_id: int) -> None:
    item = get_item(company_id, item_id)
    if item is not None and item.can_delete:
        db.session.delete(item)
        db.session.commit()


def out_of_stock_count(company_id: int) -> int:
    """Active items currently at zero or negative stock — the count behind
    the red nav badge next to "Inventory" (see inventory/routes.py's
    `_inject_nav_badge`). Hidden items are excluded: an item taken out of
    active use isn't something anyone needs to restock, same "don't alarm
    about what's retired" reasoning `selectable_items` already applies.
    Stays lit until a restock (editing quantity_on_hand back above zero)
    changes what this query returns — not until anyone merely views
    /inventory or an order, same "the fact resolves it, not looking at it"
    rule the integration-alert badge already follows."""
    return InventoryItem.query.filter_by(company_id=company_id, is_active=True).filter(
        InventoryItem.quantity_on_hand <= 0,
    ).count()


# ---------------------------------------------------------------------------
# Order materials + one-off "Others" — the Materials tab on an order page.
# ---------------------------------------------------------------------------

def list_materials_for_order(order_id: int) -> list[OrderMaterial]:
    return OrderMaterial.query.filter_by(order_id=order_id).order_by(OrderMaterial.id).all()


def understocked_materials_for_order(order_id: int) -> list[OrderMaterial]:
    """This order's materials whose live item is currently at zero or
    negative stock — i.e. what the order drew isn't fully covered by what's
    on hand. Reads the *live* item's quantity_on_hand, not the material's own
    frozen item_name/unit/unit_price snapshot, since stock is a shared,
    live figure (every other order and restock moves it), unlike a price
    that's deliberately frozen at the moment it was drawn. Powers the
    Materials tab's warning banner (see
    inventory/templates/inventory/_order_materials.html)."""
    return [
        material
        for material in list_materials_for_order(order_id)
        if material.item is not None and material.item.quantity_on_hand <= 0
    ]


def list_others_for_order(order_id: int) -> list[OrderMaterialOther]:
    return OrderMaterialOther.query.filter_by(order_id=order_id).order_by(OrderMaterialOther.id).all()


def total_material_cost(order_id: int) -> float:
    materials_total = sum(m.total for m in list_materials_for_order(order_id))
    others_total = sum(o.cost for o in list_others_for_order(order_id))
    return materials_total + others_total


def add_material(
    company_id: int, order_id: int, inventory_item_id: int, quantity_used: float,
) -> OrderMaterial | None:
    """Snapshot the item's current name/unit/price onto a new OrderMaterial
    row and decrement the item's quantity_on_hand. Going negative is allowed
    (not blocked) — the master /inventory page flags it visually instead;
    see the module docstring for why."""
    item = get_item(company_id, inventory_item_id)
    if item is None or quantity_used is None or quantity_used <= 0:
        return None
    material = OrderMaterial(
        company_id=company_id,
        order_id=order_id,
        inventory_item_id=item.id,
        quantity_used=quantity_used,
        item_name=item.name,
        unit=item.unit,
        unit_price=item.unit_price,
    )
    item.quantity_on_hand -= quantity_used
    db.session.add(material)
    db.session.commit()
    return material


def edit_material(order_id: int, material_id: int, quantity_used: float | None) -> OrderMaterial | None:
    """Change how much of a material an order used. Only the quantity is
    editable — item_name/unit/unit_price stay the snapshot taken when the
    row was created (see the model docstring), so correcting a quantity
    doesn't quietly re-price it off today's inventory cost. Adjusts stock by
    the *delta* against the live item, same allow-negative posture as
    add_material."""
    material = OrderMaterial.query.filter_by(id=material_id, order_id=order_id).first()
    if material is None or quantity_used is None or quantity_used <= 0:
        return None
    if material.item is not None:
        material.item.quantity_on_hand += material.quantity_used - quantity_used
    material.quantity_used = quantity_used
    db.session.commit()
    return material


def delete_material(order_id: int, material_id: int) -> None:
    """Restore the quantity back onto the live item (if it still exists —
    it always does, since InventoryItem is hide-don't-delete while any
    OrderMaterial references it) before removing the row."""
    material = OrderMaterial.query.filter_by(id=material_id, order_id=order_id).first()
    if material is None:
        return
    if material.item is not None:
        material.item.quantity_on_hand += material.quantity_used
    db.session.delete(material)
    db.session.commit()


def add_other(company_id: int, order_id: int, description: str, cost: float | None) -> OrderMaterialOther | None:
    description = (description or "").strip()
    if not description or cost is None:
        return None
    other = OrderMaterialOther(
        company_id=company_id, order_id=order_id, description=description, cost=cost,
    )
    db.session.add(other)
    db.session.commit()
    return other


def edit_other(order_id: int, other_id: int, description: str, cost: float | None) -> OrderMaterialOther | None:
    other = OrderMaterialOther.query.filter_by(id=other_id, order_id=order_id).first()
    description = (description or "").strip()
    if other is None or not description or cost is None:
        return None
    other.description = description
    other.cost = cost
    db.session.commit()
    return other


def delete_other(order_id: int, other_id: int) -> None:
    other = OrderMaterialOther.query.filter_by(id=other_id, order_id=order_id).first()
    if other is not None:
        db.session.delete(other)
        db.session.commit()
