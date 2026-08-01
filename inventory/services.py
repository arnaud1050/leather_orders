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

from inventory.config import UNIT_LABELS
from inventory.models import InventoryItem, InventoryType, OrderMaterial, OrderMaterialOther

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
    if not name or unit not in UNIT_LABELS:
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
    if unit in UNIT_LABELS:
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


# ---------------------------------------------------------------------------
# Order materials + one-off "Others" — the Materials tab on an order page.
# ---------------------------------------------------------------------------

def list_materials_for_order(order_id: int) -> list[OrderMaterial]:
    return OrderMaterial.query.filter_by(order_id=order_id).order_by(OrderMaterial.id).all()


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
