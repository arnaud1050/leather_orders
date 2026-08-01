"""
The module's blueprint: the master inventory page, inventory-type settings,
and the order-page Materials tab's mutations.

`resolve_order(order_id)` is supplied by the host at registration time
(app.py's `get_order_or_404`), same hook pattern as
`documents.routes.register` — this module can't import from `app.py` without
a circular import, since `app.py` is what imports and registers this
blueprint.

No CSRF layer here, deliberately — same posture as `documents/routes.py`:
matches every other mutating route in `app.py`, relying on the app-wide
`SESSION_COOKIE_SAMESITE=Lax` rather than a token. Nothing here sends mail or
touches a third-party account, unlike communications.
"""

from flask import Blueprint, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from inventory import services
from inventory.config import UNIT_LABELS

bp = Blueprint("inventory", __name__, template_folder="templates")

_resolve_order = None


def register(app, *, resolve_order) -> None:
    global _resolve_order
    _resolve_order = resolve_order
    app.register_blueprint(bp)


def _get_order_or_404(order_id: int):
    return _resolve_order(order_id)


def _parse_float(raw: str | None) -> float | None:
    """Same shape as app.py's `_parse_amount` — blank/unparsable means "no
    value", not zero, so callers can tell the difference between a
    deliberate 0 and a field left empty."""
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _flash_settings_notice(message: str) -> None:
    """Same session key app.py's settings pages already read via
    `_take_settings_notice()` — reused directly rather than imported, since
    importing app.py from here would be circular."""
    session["settings_notice"] = message


# ---------------------------------------------------------------------------
# Master inventory page — /inventory
# ---------------------------------------------------------------------------

# Server-side sort, same ?sort=&dir= convention as orders_list()/
# clients_list() in app.py (those sort in Python too, for the same reason:
# nothing here is a plain column SQL could sort on cheaply — a type is a
# joined label, not a scalar on InventoryItem itself). Sorting by "type"
# breaks ties by name, so items of the same material still read together.
INVENTORY_SORT_KEYS = {
    "type": lambda i: (i.inventory_type.label.lower() if i.inventory_type else "", i.name.lower()),
    "name": lambda i: i.name.lower(),
}


@bp.route("/inventory")
@login_required
def inventory_list():
    items = services.list_items(current_user.company_id)

    sort_by = request.args.get("sort", "type")
    if sort_by not in INVENTORY_SORT_KEYS:
        sort_by = "type"
    sort_dir = "desc" if request.args.get("dir") == "desc" else "asc"
    items.sort(key=INVENTORY_SORT_KEYS[sort_by], reverse=(sort_dir == "desc"))

    # Filter buttons: one per type actually in use (not every configured
    # type — an unused type would just be a button that always empties the
    # table), plus "No Type" whenever some item has none. Ordered by the
    # type's own sort_order so this lines up with the Type dropdown
    # elsewhere, "No Type" last since it's the catch-all rather than a
    # configured category.
    seen_type_ids: set[int] = set()
    filter_types = []
    has_untyped = False
    for item in sorted(items, key=lambda i: i.inventory_type.sort_order if i.inventory_type else -1):
        if item.inventory_type is None:
            has_untyped = True
        elif item.inventory_type.id not in seen_type_ids:
            seen_type_ids.add(item.inventory_type.id)
            filter_types.append({"value": str(item.inventory_type.id), "label": item.inventory_type.label})
    if has_untyped:
        filter_types.append({"value": "none", "label": "No Type"})

    return render_template(
        "inventory/inventory_list.html",
        items=items,
        filter_types=filter_types,
        sort_by=sort_by,
        sort_dir=sort_dir,
        inventory_types=services.active_types(current_user.company_id),
        unit_labels=UNIT_LABELS,
        active_view="inventory",
    )


@bp.route("/inventory/new", methods=["POST"])
@login_required
def add_item():
    raw_type_id = request.form.get("inventory_type_id", "")
    services.add_item(
        current_user.company_id,
        name=request.form.get("name", ""),
        unit=request.form.get("unit", ""),
        inventory_type_id=int(raw_type_id) if raw_type_id.isdigit() else None,
        quantity_on_hand=_parse_float(request.form.get("quantity_on_hand")) or 0.0,
        unit_price=_parse_float(request.form.get("unit_price")) or 0.0,
    )
    return redirect(url_for("inventory.inventory_list"))


@bp.route("/inventory/<int:item_id>/edit", methods=["POST"])
@login_required
def edit_item(item_id: int):
    raw_type_id = request.form.get("inventory_type_id", "")
    services.edit_item(
        current_user.company_id, item_id,
        name=request.form.get("name", ""),
        unit=request.form.get("unit", ""),
        inventory_type_id=int(raw_type_id) if raw_type_id.isdigit() else None,
        quantity_on_hand=_parse_float(request.form.get("quantity_on_hand")) or 0.0,
        unit_price=_parse_float(request.form.get("unit_price")) or 0.0,
    )
    return redirect(url_for("inventory.inventory_list"))


@bp.route("/inventory/<int:item_id>/toggle", methods=["POST"])
@login_required
def toggle_item(item_id: int):
    services.toggle_item(current_user.company_id, item_id)
    return redirect(url_for("inventory.inventory_list"))


@bp.route("/inventory/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_item(item_id: int):
    services.delete_item(current_user.company_id, item_id)
    return redirect(url_for("inventory.inventory_list"))


# ---------------------------------------------------------------------------
# Inventory types — settings-level (company-wide, not order-scoped), same
# shape as documents.routes' DocumentType routes.
# ---------------------------------------------------------------------------

@bp.route("/settings/inventory-types", methods=["POST"])
@login_required
def add_type():
    label = request.form.get("label", "")
    inventory_type = services.add_type(current_user.company_id, label)
    if inventory_type is None and label.strip():
        _flash_settings_notice(f'An inventory type called "{label.strip()}" already exists.')
    return redirect(url_for("settings_inventory"))


@bp.route("/settings/inventory-types/<int:inventory_type_id>/toggle", methods=["POST"])
@login_required
def toggle_type(inventory_type_id: int):
    services.toggle_type(current_user.company_id, inventory_type_id)
    return redirect(url_for("settings_inventory"))


@bp.route("/settings/inventory-types/<int:inventory_type_id>/delete", methods=["POST"])
@login_required
def delete_type(inventory_type_id: int):
    services.delete_type(current_user.company_id, inventory_type_id)
    return redirect(url_for("settings_inventory"))


# ---------------------------------------------------------------------------
# Order materials + one-off "Others" — the Materials tab on an order page.
# ---------------------------------------------------------------------------

def _redirect_back(order_id: int):
    return_to = request.form.get("return_to") or url_for("order_materials", order_id=order_id)
    return redirect(return_to)


@bp.route("/orders/<int:order_id>/materials/add", methods=["POST"])
@login_required
def add_material(order_id: int):
    order = _get_order_or_404(order_id)
    raw_item_id = request.form.get("inventory_item_id", "")
    services.add_material(
        current_user.company_id, order.id,
        inventory_item_id=int(raw_item_id) if raw_item_id.isdigit() else -1,
        quantity_used=_parse_float(request.form.get("quantity_used")),
    )
    return _redirect_back(order.id)


@bp.route("/orders/<int:order_id>/materials/<int:material_id>/edit", methods=["POST"])
@login_required
def edit_material(order_id: int, material_id: int):
    order = _get_order_or_404(order_id)
    services.edit_material(
        order.id, material_id,
        quantity_used=_parse_float(request.form.get("quantity_used")),
    )
    return _redirect_back(order.id)


@bp.route("/orders/<int:order_id>/materials/<int:material_id>/delete", methods=["POST"])
@login_required
def delete_material(order_id: int, material_id: int):
    order = _get_order_or_404(order_id)
    services.delete_material(order.id, material_id)
    return _redirect_back(order.id)


@bp.route("/orders/<int:order_id>/materials/others/add", methods=["POST"])
@login_required
def add_other(order_id: int):
    order = _get_order_or_404(order_id)
    services.add_other(
        current_user.company_id, order.id,
        description=request.form.get("description", ""),
        cost=_parse_float(request.form.get("cost")),
    )
    return _redirect_back(order.id)


@bp.route("/orders/<int:order_id>/materials/others/<int:other_id>/edit", methods=["POST"])
@login_required
def edit_other(order_id: int, other_id: int):
    order = _get_order_or_404(order_id)
    services.edit_other(
        order.id, other_id,
        description=request.form.get("description", ""),
        cost=_parse_float(request.form.get("cost")),
    )
    return _redirect_back(order.id)


@bp.route("/orders/<int:order_id>/materials/others/<int:other_id>/delete", methods=["POST"])
@login_required
def delete_other(order_id: int, other_id: int):
    order = _get_order_or_404(order_id)
    services.delete_other(order.id, other_id)
    return _redirect_back(order.id)
