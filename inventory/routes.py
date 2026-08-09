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

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from inventory import services
from inventory.config import UNIT_LABELS

bp = Blueprint("inventory", __name__, template_folder="templates")

_resolve_order = None


def register(app, *, resolve_order) -> None:
    global _resolve_order
    _resolve_order = resolve_order
    app.register_blueprint(bp)


@bp.app_context_processor
def _inject_nav_badge():
    """The two nav badges next to "Inventory" in base.html — how many active
    items are out of stock (red, at zero/negative) and how many are low
    (amber, above zero but at or below their own warning point). Same shape
    as communications' badges (see communications/routes.py's
    `_inject_nav_badges`): an `app_context_processor` since base.html is
    extended by every page, not just this module's own; each count comes
    back as a callable so the query only runs on templates that actually
    render it; and both are deliberately forgiving (no user, or a table/
    column that doesn't exist yet on a partially-migrated database) since a
    decoration isn't worth a 500.
    """
    def out_of_stock_count() -> int:
        if not current_user.is_authenticated:
            return 0
        try:
            return services.out_of_stock_count(current_user.company_id)
        except Exception:  # noqa: BLE001 — see docstring
            current_app.logger.debug("Could not compute the inventory stock badge", exc_info=True)
            return 0

    def low_stock_count() -> int:
        if not current_user.is_authenticated:
            return 0
        try:
            return services.low_stock_count(current_user.company_id)
        except Exception:  # noqa: BLE001 — see docstring
            current_app.logger.debug("Could not compute the inventory low-stock badge", exc_info=True)
            return 0

    return {"out_of_stock_count": out_of_stock_count, "low_stock_count": low_stock_count}


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
    "price": lambda i: i.unit_price,
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
    # configured category. "No Type" is further gated on the company having
    # defined at least one real type at all (has_types) — with none defined,
    # "No Type" would be the only bucket that could ever show, which isn't a
    # filter worth offering (same "don't show it until there's something to
    # show" rule as the Type column on /orders).
    seen_type_ids: set[int] = set()
    filter_types = []
    has_untyped = False
    for item in sorted(items, key=lambda i: i.inventory_type.sort_order if i.inventory_type else -1):
        if item.inventory_type is None:
            has_untyped = True
        elif item.inventory_type.id not in seen_type_ids:
            seen_type_ids.add(item.inventory_type.id)
            filter_types.append({"value": str(item.inventory_type.id), "label": item.inventory_type.label})
    if has_untyped and services.has_types(current_user.company_id):
        filter_types.append({"value": "none", "label": "No Type"})

    return render_template(
        "inventory/inventory_list.html",
        items=items,
        # Only what this company chose to show, in its own order — the
        # Actions column isn't in here and isn't configurable, being the
        # row's controls rather than one of the item's fields.
        columns=services.visible_columns(current_user.company_id),
        filter_types=filter_types,
        # Hidden items stay in the DOM (so Unhide keeps working) but are
        # filtered out client-side by default — see inventory_list.html's
        # script. This just tells the template whether the "Show hidden"
        # toggle needs to render at all.
        has_hidden_items=any(not item.is_active for item in items),
        sort_by=sort_by,
        sort_dir=sort_dir,
        inventory_types=services.active_types(current_user.company_id),
        # list_units(), not active_units(): the Add/Edit item dropdown (see
        # inventory_list.html's item_fields() macro) needs every configured
        # unit so it can still show a hidden-but-currently-selected one on
        # an existing item, same active-∪-current pattern as
        # inventory_type_id's own hidden-option handling just above it.
        units=services.list_units(current_user.company_id),
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
        low_stock_threshold=_parse_float(request.form.get("low_stock_threshold")) or 0.0,
        reference=request.form.get("reference"),
        url=request.form.get("url"),
        notes=request.form.get("notes"),
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
        low_stock_threshold=_parse_float(request.form.get("low_stock_threshold")) or 0.0,
        reference=request.form.get("reference"),
        url=request.form.get("url"),
        notes=request.form.get("notes"),
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
# Units — settings-level (company-wide), which of config.UNIT_CATALOG's keys
# a company offers and in what order. "Each" (config.DEFAULT_UNIT) has no
# toggle/delete route at all — there's nothing for a request to hit, so a
# hand-crafted POST against a nonexistent id is just a 404, not a guarded
# no-op the service also happens to enforce (services.toggle_unit/delete_unit
# still check is_default too, belt-and-suspenders).
# ---------------------------------------------------------------------------

@bp.route("/settings/inventory-units", methods=["POST"])
@login_required
def add_unit():
    key = request.form.get("key", "")
    services.add_unit(current_user.company_id, key)
    return redirect(url_for("settings_inventory"))


@bp.route("/settings/inventory-units/<int:unit_id>/toggle", methods=["POST"])
@login_required
def toggle_unit(unit_id: int):
    services.toggle_unit(current_user.company_id, unit_id)
    return redirect(url_for("settings_inventory"))


@bp.route("/settings/inventory-units/<int:unit_id>/delete", methods=["POST"])
@login_required
def delete_unit(unit_id: int):
    services.delete_unit(current_user.company_id, unit_id)
    return redirect(url_for("settings_inventory"))


@bp.route("/settings/inventory-units/reorder", methods=["POST"])
@login_required
def reorder_units():
    """Fired by the drag-and-drop handler in _settings_units.html — a JSON
    body, not a form post, same shape as documents.reorder_types."""
    payload = request.get_json(silent=True) or {}
    ordered_ids = [i for i in payload.get("order", []) if isinstance(i, int)]
    services.reorder_units(current_user.company_id, ordered_ids)
    return "", 204


# ---------------------------------------------------------------------------
# Master-list columns — settings-level, which fields the /inventory table
# renders and in what order. Same pair of routes as app.py's
# toggle_order_column/reorder_order_columns for the Orders list: a plain form
# POST for the per-column Hide/Show button (everything else in Settings acts
# immediately on click, so this shouldn't be the odd one out) and a JSON
# fetch() for the drag-to-reorder handler.
# ---------------------------------------------------------------------------

@bp.route("/settings/inventory-columns/<key>/toggle", methods=["POST"])
@login_required
def toggle_column(key: str):
    services.toggle_column(current_user.company_id, key)
    return redirect(url_for("settings_inventory"))


@bp.route("/settings/inventory-columns/reorder", methods=["POST"])
@login_required
def reorder_columns():
    payload = request.get_json(silent=True) or {}
    ordered_keys = [k for k in payload.get("order", []) if isinstance(k, str)]
    services.reorder_columns(current_user.company_id, ordered_keys)
    return "", 204


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
