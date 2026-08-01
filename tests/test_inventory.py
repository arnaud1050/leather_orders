"""
inventory/ — types (hide-don't-delete + duplicate rejection), items (CRUD,
stock levels), drawing materials onto an order (snapshot pricing, stock
decrement/restore, allow-negative), one-off "Others" costs, tenant isolation
on every route, and that none of this touches Order.total/OrderLine/the
invoice — this module is cost-tracking only (see inventory/__init__.py).
"""

from datetime import date

import pytest

from models import Client, Order, db

from inventory import services
from inventory.models import InventoryItem, InventoryType, OrderMaterial, OrderMaterialOther


# --- inventory types: add / duplicate / hide-don't-delete / tenant scoping --

def test_add_type_assigns_next_sort_order(company):
    a = services.add_type(company.id, "Leather")
    b = services.add_type(company.id, "Lining")
    assert a.sort_order == 0
    assert b.sort_order == 1


def test_add_type_rejects_a_blank_label(company):
    assert services.add_type(company.id, "   ") is None


def test_add_type_rejects_an_exact_duplicate(company):
    services.add_type(company.id, "Leather")
    assert services.add_type(company.id, "Leather") is None
    assert InventoryType.query.filter_by(company_id=company.id).count() == 1


def test_add_type_rejects_a_case_insensitive_duplicate(company):
    services.add_type(company.id, "Leather")
    assert services.add_type(company.id, "  leather  ") is None


def test_add_type_rejects_a_duplicate_of_a_hidden_type(company):
    leather = services.add_type(company.id, "Leather")
    services.toggle_type(company.id, leather.id)  # hide
    assert services.add_type(company.id, "Leather") is None


def test_add_type_allows_the_same_label_in_another_company(company, other_company):
    services.add_type(company.id, "Leather")
    assert services.add_type(other_company.id, "Leather") is not None


def test_toggle_type_flips_is_active(company):
    leather = services.add_type(company.id, "Leather")
    services.toggle_type(company.id, leather.id)
    assert db.session.get(InventoryType, leather.id).is_active is False
    services.toggle_type(company.id, leather.id)
    assert db.session.get(InventoryType, leather.id).is_active is True


def test_toggle_type_is_scoped_to_the_tenant(company, other_company):
    leather = services.add_type(company.id, "Leather")
    services.toggle_type(other_company.id, leather.id)
    assert db.session.get(InventoryType, leather.id).is_active is True  # untouched


def test_delete_type_removes_it_when_unused(company):
    leather = services.add_type(company.id, "Leather")
    services.delete_type(company.id, leather.id)
    assert db.session.get(InventoryType, leather.id) is None


def test_delete_type_is_blocked_once_referenced(company):
    leather = services.add_type(company.id, "Leather")
    services.add_item(
        company.id, name="Horween Chromexcel", unit="sqft",
        inventory_type_id=leather.id, quantity_on_hand=50, unit_price=12.5,
    )
    services.delete_type(company.id, leather.id)
    assert db.session.get(InventoryType, leather.id) is not None
    assert db.session.get(InventoryType, leather.id).can_delete is False


def test_has_types_is_false_when_none_defined(company):
    assert services.has_types(company.id) is False


def test_has_types_is_true_once_a_type_exists(company):
    services.add_type(company.id, "Leather")
    assert services.has_types(company.id) is True


def test_has_types_is_true_even_for_a_hidden_type(company):
    leather = services.add_type(company.id, "Leather")
    services.toggle_type(company.id, leather.id)  # hide
    assert services.has_types(company.id) is True


def test_has_types_is_scoped_to_the_tenant(company, other_company):
    services.add_type(other_company.id, "Theirs")
    assert services.has_types(company.id) is False


# --- inventory items: add / edit / hide-don't-delete / tenant scoping ------

def test_add_item_creates_it_with_given_fields(company):
    leather = services.add_type(company.id, "Leather")
    item = services.add_item(
        company.id, name="Horween Chromexcel", unit="sqft",
        inventory_type_id=leather.id, quantity_on_hand=50, unit_price=12.5,
    )
    assert item.company_id == company.id
    assert item.inventory_type_id == leather.id
    assert item.quantity_on_hand == 50
    assert item.unit_price == 12.5
    assert item.is_active is True


def test_add_item_works_without_a_type(company):
    item = services.add_item(
        company.id, name="Brass zippers", unit="each",
        inventory_type_id=None, quantity_on_hand=20, unit_price=1.75,
    )
    assert item.inventory_type_id is None


def test_add_item_rejects_a_blank_name(company):
    assert services.add_item(
        company.id, name="   ", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    ) is None


def test_add_item_rejects_an_invalid_unit(company):
    assert services.add_item(
        company.id, name="Mystery material", unit="yards",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    ) is None


def test_add_item_with_a_foreign_type_id_falls_back_to_none(company, other_company):
    theirs = services.add_type(other_company.id, "Theirs")
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=theirs.id, quantity_on_hand=1, unit_price=1,
    )
    assert item.inventory_type_id is None


def test_edit_item_updates_fields(company):
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    )
    services.edit_item(
        company.id, item.id, name="Renamed widget", unit="each",
        inventory_type_id=None, quantity_on_hand=5, unit_price=2.5,
    )
    updated = db.session.get(InventoryItem, item.id)
    assert updated.name == "Renamed widget"
    assert updated.quantity_on_hand == 5
    assert updated.unit_price == 2.5


def test_edit_item_allows_setting_quantity_negative(company):
    """Allowed, not blocked — see add_material's docstring for why: this is
    the same posture, just reachable from the edit form too."""
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    )
    services.edit_item(
        company.id, item.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=-3, unit_price=1,
    )
    assert db.session.get(InventoryItem, item.id).quantity_on_hand == -3


def test_edit_item_is_scoped_to_the_tenant(company, other_company):
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    )
    result = services.edit_item(
        other_company.id, item.id, name="Hijacked", unit="each",
        inventory_type_id=None, quantity_on_hand=99, unit_price=99,
    )
    assert result is None
    assert db.session.get(InventoryItem, item.id).name == "Widget"  # untouched


def test_toggle_item_flips_is_active(company):
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    )
    services.toggle_item(company.id, item.id)
    assert db.session.get(InventoryItem, item.id).is_active is False


def test_delete_item_removes_it_when_unused(company):
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    )
    services.delete_item(company.id, item.id)
    assert db.session.get(InventoryItem, item.id) is None


def test_delete_item_is_blocked_once_referenced(company, order):
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=10, unit_price=1,
    )
    services.add_material(company.id, order.id, item.id, quantity_used=1)
    services.delete_item(company.id, item.id)
    assert db.session.get(InventoryItem, item.id) is not None


# --- selectable_items: active ∪ referenced-by-this-order -------------------

def test_selectable_items_excludes_hidden_items_by_default(company):
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    )
    services.toggle_item(company.id, item.id)  # hide
    assert services.selectable_items(company.id) == []


def test_selectable_items_includes_a_hidden_item_already_used_on_this_order(company, order):
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=10, unit_price=1,
    )
    services.add_material(company.id, order.id, item.id, quantity_used=1)
    services.toggle_item(company.id, item.id)  # hide after using it

    assert services.selectable_items(company.id, order.id) == [item]


# --- order materials: snapshot pricing, stock decrement/restore -----------

def test_add_material_decrements_stock_and_snapshots_the_item(company, order):
    item = services.add_item(
        company.id, name="Horween Chromexcel", unit="sqft",
        inventory_type_id=None, quantity_on_hand=50, unit_price=12.5,
    )
    material = services.add_material(company.id, order.id, item.id, quantity_used=4.5)

    assert material.item_name == "Horween Chromexcel"
    assert material.unit == "sqft"
    assert material.unit_price == 12.5
    assert material.total == 4.5 * 12.5
    assert db.session.get(InventoryItem, item.id).quantity_on_hand == 45.5


def test_add_material_snapshot_survives_a_later_price_change(company, order):
    """The whole point of snapshotting: raising the item's price afterwards
    must not reprice history."""
    item = services.add_item(
        company.id, name="Horween Chromexcel", unit="sqft",
        inventory_type_id=None, quantity_on_hand=50, unit_price=12.5,
    )
    material = services.add_material(company.id, order.id, item.id, quantity_used=2)

    services.edit_item(
        company.id, item.id, name=item.name, unit=item.unit,
        inventory_type_id=None, quantity_on_hand=item.quantity_on_hand, unit_price=99.0,
    )

    assert db.session.get(OrderMaterial, material.id).unit_price == 12.5


def test_add_material_allows_going_negative(company, order):
    item = services.add_item(
        company.id, name="Widget", unit="sqft",
        inventory_type_id=None, quantity_on_hand=5, unit_price=1,
    )
    services.add_material(company.id, order.id, item.id, quantity_used=100)
    assert db.session.get(InventoryItem, item.id).quantity_on_hand == -95


def test_add_material_rejects_a_zero_or_negative_quantity(company, order):
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=5, unit_price=1,
    )
    assert services.add_material(company.id, order.id, item.id, quantity_used=0) is None
    assert services.add_material(company.id, order.id, item.id, quantity_used=-1) is None
    assert db.session.get(InventoryItem, item.id).quantity_on_hand == 5  # untouched


def test_add_material_rejects_a_missing_quantity(company, order):
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=5, unit_price=1,
    )
    assert services.add_material(company.id, order.id, item.id, quantity_used=None) is None


def test_add_material_rejects_an_unknown_item(company, order):
    assert services.add_material(company.id, order.id, 999999, quantity_used=1) is None


def test_add_material_rejects_another_tenants_item(company, other_company, order):
    theirs = services.add_item(
        other_company.id, name="Theirs", unit="each",
        inventory_type_id=None, quantity_on_hand=5, unit_price=1,
    )
    assert services.add_material(company.id, order.id, theirs.id, quantity_used=1) is None


def test_edit_material_adjusts_stock_by_the_delta(company, order):
    item = services.add_item(
        company.id, name="Widget", unit="sqft",
        inventory_type_id=None, quantity_on_hand=50, unit_price=10,
    )
    material = services.add_material(company.id, order.id, item.id, quantity_used=10)
    assert db.session.get(InventoryItem, item.id).quantity_on_hand == 40

    services.edit_material(order.id, material.id, quantity_used=7)
    assert db.session.get(InventoryItem, item.id).quantity_on_hand == 43  # +3 restored
    assert db.session.get(OrderMaterial, material.id).quantity_used == 7

    services.edit_material(order.id, material.id, quantity_used=12)
    assert db.session.get(InventoryItem, item.id).quantity_on_hand == 38  # -5 more drawn


def test_edit_material_does_not_touch_the_snapshot_price(company, order):
    item = services.add_item(
        company.id, name="Widget", unit="sqft",
        inventory_type_id=None, quantity_on_hand=50, unit_price=10,
    )
    material = services.add_material(company.id, order.id, item.id, quantity_used=10)
    services.edit_material(order.id, material.id, quantity_used=7)
    assert db.session.get(OrderMaterial, material.id).unit_price == 10


def test_edit_material_rejects_a_zero_or_negative_quantity(company, order):
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=50, unit_price=10,
    )
    material = services.add_material(company.id, order.id, item.id, quantity_used=10)
    assert services.edit_material(order.id, material.id, quantity_used=0) is None
    assert services.edit_material(order.id, material.id, quantity_used=-1) is None
    assert db.session.get(OrderMaterial, material.id).quantity_used == 10  # untouched


def test_edit_material_is_scoped_to_the_order(company, order):
    """A material belonging to a different order can't be edited by passing
    the wrong order_id — same shape as every other order-scoped lookup."""
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=10, unit_price=1,
    )
    material = services.add_material(company.id, order.id, item.id, quantity_used=1)
    assert services.edit_material(order.id + 1, material.id, quantity_used=5) is None
    assert db.session.get(OrderMaterial, material.id).quantity_used == 1


def test_delete_material_restores_stock_and_removes_the_row(company, order):
    item = services.add_item(
        company.id, name="Widget", unit="sqft",
        inventory_type_id=None, quantity_on_hand=50, unit_price=10,
    )
    material = services.add_material(company.id, order.id, item.id, quantity_used=10)

    services.delete_material(order.id, material.id)

    assert db.session.get(OrderMaterial, material.id) is None
    assert db.session.get(InventoryItem, item.id).quantity_on_hand == 50


# --- order "Others": one-off costs, no stock effect ------------------------

def test_add_other_creates_a_cost_row(company, order):
    other = services.add_other(company.id, order.id, "Replacement buckle", 8.5)
    assert other.description == "Replacement buckle"
    assert other.cost == 8.5


def test_add_other_rejects_blank_description_or_missing_cost(company, order):
    assert services.add_other(company.id, order.id, "  ", 5.0) is None
    assert services.add_other(company.id, order.id, "Buckle", None) is None


def test_edit_other_updates_fields(company, order):
    other = services.add_other(company.id, order.id, "Buckle", 8.5)
    services.edit_other(order.id, other.id, "Replacement buckle", 9.0)
    updated = db.session.get(OrderMaterialOther, other.id)
    assert updated.description == "Replacement buckle"
    assert updated.cost == 9.0


def test_edit_other_is_scoped_to_the_order(company, order):
    other = services.add_other(company.id, order.id, "Buckle", 8.5)
    assert services.edit_other(order.id + 1, other.id, "Hijacked", 1.0) is None
    assert db.session.get(OrderMaterialOther, other.id).description == "Buckle"


def test_delete_other_removes_the_row(company, order):
    other = services.add_other(company.id, order.id, "Buckle", 8.5)
    services.delete_other(order.id, other.id)
    assert db.session.get(OrderMaterialOther, other.id) is None


# --- total_material_cost: materials + others, nothing else -----------------

def test_total_material_cost_sums_materials_and_others(company, order):
    item = services.add_item(
        company.id, name="Widget", unit="sqft",
        inventory_type_id=None, quantity_on_hand=50, unit_price=10,
    )
    services.add_material(company.id, order.id, item.id, quantity_used=4)  # $40
    services.add_other(company.id, order.id, "Buckle", 8.5)

    assert services.total_material_cost(order.id) == pytest.approx(48.5)


def test_total_material_cost_never_touches_order_total(company, order):
    """This module is cost-tracking only — see inventory/__init__.py. The
    order's client-facing total comes from OrderLine alone."""
    item = services.add_item(
        company.id, name="Widget", unit="sqft",
        inventory_type_id=None, quantity_on_hand=50, unit_price=10,
    )
    total_before = order.total
    services.add_material(company.id, order.id, item.id, quantity_used=4)
    services.add_other(company.id, order.id, "Buckle", 8.5)

    assert db.session.get(Order, order.id).total == total_before


# --- routes: auth ------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/inventory/new",
    "/inventory/1/edit",
    "/inventory/1/toggle",
    "/inventory/1/delete",
    "/settings/inventory-types",
    "/settings/inventory-types/1/toggle",
    "/settings/inventory-types/1/delete",
])
def test_inventory_management_routes_require_login(app, path):
    response = app.test_client().post(path, data={})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_inventory_list_requires_login(app):
    response = app.test_client().get("/inventory")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


@pytest.mark.parametrize("path", [
    "/orders/1/materials/add",
    "/orders/1/materials/1/edit",
    "/orders/1/materials/1/delete",
    "/orders/1/materials/others/add",
    "/orders/1/materials/others/1/edit",
    "/orders/1/materials/others/1/delete",
])
def test_order_material_routes_require_login(app, path):
    response = app.test_client().post(path, data={})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


# --- routes: master inventory page -----------------------------------------

def test_inventory_list_sorts_by_name(logged_in, company):
    services.add_item(
        company.id, name="Zipper", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    )
    services.add_item(
        company.id, name="Awl", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    )
    response = logged_in.get("/inventory?sort=name&dir=asc")
    body = response.data.decode()
    assert body.index("Awl") < body.index("Zipper")


def test_inventory_list_sorts_by_price(logged_in, company):
    services.add_item(
        company.id, name="Pricey", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=99.00,
    )
    services.add_item(
        company.id, name="Cheap", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1.00,
    )
    response = logged_in.get("/inventory?sort=price&dir=asc")
    body = response.data.decode()
    assert body.index("Cheap") < body.index("Pricey")


def test_filter_types_excludes_no_type_when_company_has_no_types_defined(logged_in, company):
    """No InventoryType exists for this company at all, so every item is
    untyped by definition — "No Type" would be the only possible filter
    button and isn't offered (see services.has_types)."""
    services.add_item(
        company.id, name="Untyped widget", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    )
    response = logged_in.get("/inventory")
    assert b"No Type" not in response.data


def test_filter_types_includes_no_type_when_a_type_exists_and_an_item_is_untyped(logged_in, company):
    leather = services.add_type(company.id, "Leather")
    services.add_item(
        company.id, name="Horween Chromexcel", unit="sqft",
        inventory_type_id=leather.id, quantity_on_hand=1, unit_price=1,
    )
    services.add_item(
        company.id, name="Untyped widget", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    )
    response = logged_in.get("/inventory")
    assert b"No Type" in response.data


def test_inventory_list_marks_hidden_items_with_data_active_false(logged_in, company):
    active = services.add_item(
        company.id, name="Active widget", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    )
    hidden = services.add_item(
        company.id, name="Hidden widget", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    )
    services.toggle_item(company.id, hidden.id)

    body = logged_in.get("/inventory").data.decode()

    assert f'data-active="true"' in body
    assert f'data-active="false"' in body


def test_show_hidden_toggle_appears_when_a_hidden_item_exists(logged_in, company):
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    )
    services.toggle_item(company.id, item.id)
    response = logged_in.get("/inventory")
    # The double-quoted HTML attribute form, not a bare substring match — the
    # inline script always mentions the id in a getElementById() call
    # (single-quoted) regardless of whether the button itself is rendered.
    assert b'id="show-hidden-toggle"' in response.data


def test_show_hidden_toggle_absent_when_nothing_is_hidden(logged_in, company):
    services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    )
    response = logged_in.get("/inventory")
    assert b'id="show-hidden-toggle"' not in response.data


def test_add_item_route_creates_an_item(logged_in, company):
    response = logged_in.post(
        "/inventory/new",
        data={"name": "Widget", "unit": "each", "quantity_on_hand": "5", "unit_price": "2.00"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    item = InventoryItem.query.filter_by(company_id=company.id, name="Widget").first()
    assert item is not None
    assert item.quantity_on_hand == 5


def test_edit_item_route_updates_the_item(logged_in, company):
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=5, unit_price=2,
    )
    logged_in.post(
        f"/inventory/{item.id}/edit",
        data={"name": "Widget", "unit": "each", "quantity_on_hand": "12", "unit_price": "3.50"},
        follow_redirects=True,
    )
    updated = db.session.get(InventoryItem, item.id)
    assert updated.quantity_on_hand == 12
    assert updated.unit_price == 3.5


def test_toggle_item_route_flips_is_active(logged_in, company):
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=5, unit_price=2,
    )
    logged_in.post(f"/inventory/{item.id}/toggle", follow_redirects=True)
    assert db.session.get(InventoryItem, item.id).is_active is False


def test_delete_item_route_removes_it_when_unused(logged_in, company):
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=5, unit_price=2,
    )
    logged_in.post(f"/inventory/{item.id}/delete", follow_redirects=True)
    assert db.session.get(InventoryItem, item.id) is None


def test_item_management_routes_are_scoped_to_the_tenant(logged_in, other_company):
    theirs = services.add_item(
        other_company.id, name="Theirs", unit="each",
        inventory_type_id=None, quantity_on_hand=5, unit_price=2,
    )
    logged_in.post(
        f"/inventory/{theirs.id}/edit",
        data={"name": "Hijacked", "unit": "each", "quantity_on_hand": "0", "unit_price": "0"},
    )
    logged_in.post(f"/inventory/{theirs.id}/toggle")
    logged_in.post(f"/inventory/{theirs.id}/delete")

    untouched = db.session.get(InventoryItem, theirs.id)
    assert untouched is not None
    assert untouched.name == "Theirs"
    assert untouched.is_active is True


# --- routes: inventory-type settings ----------------------------------------

def test_add_type_route_creates_a_type(logged_in, company):
    response = logged_in.post(
        "/settings/inventory-types", data={"label": "Leather"}, follow_redirects=True,
    )
    assert response.status_code == 200
    assert InventoryType.query.filter_by(company_id=company.id, label="Leather").first() is not None


def test_add_type_route_rejects_a_duplicate_and_flashes_a_message(logged_in, company):
    logged_in.post("/settings/inventory-types", data={"label": "Leather"})
    response = logged_in.post(
        "/settings/inventory-types", data={"label": "Leather"}, follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"already exists" in response.data
    assert InventoryType.query.filter_by(company_id=company.id, label="Leather").count() == 1


def test_settings_inventory_page_lists_types(logged_in, company):
    services.add_type(company.id, "Leather")
    response = logged_in.get("/settings/inventory")
    assert response.status_code == 200
    assert b"Leather" in response.data


# --- routes: order Materials tab, full HTTP roundtrip -----------------------

def test_add_material_route_decrements_stock_and_shows_the_total(logged_in, company, order):
    item = services.add_item(
        company.id, name="Horween Chromexcel", unit="sqft",
        inventory_type_id=None, quantity_on_hand=50, unit_price=12.5,
    )
    response = logged_in.post(
        f"/orders/{order.id}/materials/add",
        data={"inventory_item_id": str(item.id), "quantity_used": "4.5"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"56.25" in response.data  # 4.5 * 12.5
    assert db.session.get(InventoryItem, item.id).quantity_on_hand == 45.5


def test_edit_material_route_updates_quantity_and_stock(logged_in, company, order):
    item = services.add_item(
        company.id, name="Horween Chromexcel", unit="sqft",
        inventory_type_id=None, quantity_on_hand=50, unit_price=12.5,
    )
    material = services.add_material(company.id, order.id, item.id, quantity_used=10)

    response = logged_in.post(
        f"/orders/{order.id}/materials/{material.id}/edit",
        data={"quantity_used": "7"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert db.session.get(OrderMaterial, material.id).quantity_used == 7
    assert db.session.get(InventoryItem, item.id).quantity_on_hand == 43


def test_delete_material_route_restores_stock(logged_in, company, order):
    item = services.add_item(
        company.id, name="Horween Chromexcel", unit="sqft",
        inventory_type_id=None, quantity_on_hand=50, unit_price=12.5,
    )
    material = services.add_material(company.id, order.id, item.id, quantity_used=10)

    logged_in.post(f"/orders/{order.id}/materials/{material.id}/delete", follow_redirects=True)

    assert db.session.get(OrderMaterial, material.id) is None
    assert db.session.get(InventoryItem, item.id).quantity_on_hand == 50


def test_others_route_roundtrip(logged_in, order):
    logged_in.post(
        f"/orders/{order.id}/materials/others/add",
        data={"description": "Replacement buckle", "cost": "8.50"},
    )
    other = OrderMaterialOther.query.filter_by(order_id=order.id).first()
    assert other is not None

    logged_in.post(
        f"/orders/{order.id}/materials/others/{other.id}/edit",
        data={"description": "Replacement buckle (brass)", "cost": "9.00"},
    )
    assert db.session.get(OrderMaterialOther, other.id).description == "Replacement buckle (brass)"

    logged_in.post(f"/orders/{order.id}/materials/others/{other.id}/delete")
    assert db.session.get(OrderMaterialOther, other.id) is None


def test_materials_tab_never_changes_order_total(logged_in, company, order):
    item = services.add_item(
        company.id, name="Horween Chromexcel", unit="sqft",
        inventory_type_id=None, quantity_on_hand=50, unit_price=12.5,
    )
    total_before = order.total
    logged_in.post(
        f"/orders/{order.id}/materials/add",
        data={"inventory_item_id": str(item.id), "quantity_used": "4.5"},
    )
    logged_in.post(
        f"/orders/{order.id}/materials/others/add",
        data={"description": "Buckle", "cost": "8.50"},
    )
    assert db.session.get(Order, order.id).total == total_before


# --- routes: tenant isolation on order-scoped material routes --------------

def _order_for(company_id: int) -> Order:
    outsider_client = Client(company_id=company_id, first_name="X", last_name="Y")
    db.session.add(outsider_client)
    db.session.flush()
    outsider_order = Order(
        client_id=outsider_client.id, item="Outsider's item",
        start=date(2026, 1, 1), due=date(2026, 1, 10), status="in_progress",
    )
    db.session.add(outsider_order)
    db.session.flush()
    return outsider_order


def test_add_material_404s_for_another_tenants_order(logged_in, other_company):
    outsider_order = _order_for(other_company.id)
    item = services.add_item(
        other_company.id, name="Theirs", unit="each",
        inventory_type_id=None, quantity_on_hand=5, unit_price=1,
    )
    response = logged_in.post(
        f"/orders/{outsider_order.id}/materials/add",
        data={"inventory_item_id": str(item.id), "quantity_used": "1"},
    )
    assert response.status_code == 404
    assert OrderMaterial.query.filter_by(order_id=outsider_order.id).count() == 0


def test_edit_material_404s_for_another_tenants_order(logged_in, other_company):
    outsider_order = _order_for(other_company.id)
    item = services.add_item(
        other_company.id, name="Theirs", unit="each",
        inventory_type_id=None, quantity_on_hand=5, unit_price=1,
    )
    material = services.add_material(other_company.id, outsider_order.id, item.id, quantity_used=1)

    response = logged_in.post(
        f"/orders/{outsider_order.id}/materials/{material.id}/edit",
        data={"quantity_used": "99"},
    )
    assert response.status_code == 404
    assert db.session.get(OrderMaterial, material.id).quantity_used == 1


def test_delete_material_404s_for_another_tenants_order(logged_in, other_company):
    outsider_order = _order_for(other_company.id)
    item = services.add_item(
        other_company.id, name="Theirs", unit="each",
        inventory_type_id=None, quantity_on_hand=5, unit_price=1,
    )
    material = services.add_material(other_company.id, outsider_order.id, item.id, quantity_used=1)

    response = logged_in.post(f"/orders/{outsider_order.id}/materials/{material.id}/delete")
    assert response.status_code == 404
    assert db.session.get(OrderMaterial, material.id) is not None  # untouched


def test_order_materials_page_404s_for_another_tenants_order(logged_in, other_company):
    outsider_order = _order_for(other_company.id)
    response = logged_in.get(f"/orders/{outsider_order.id}/materials")
    assert response.status_code == 404


# --- migrations: no-op contract --------------------------------------------

def test_run_migrations_is_a_safe_no_op(app):
    """Every table this module owns is brand new, so ADDED_COLUMNS starts
    empty — this just proves calling it (as app.py does on every boot)
    never raises, on a fresh schema or a second call."""
    from inventory import migrations as inventory_migrations

    with app.app_context():
        inventory_migrations.run_migrations()
        inventory_migrations.run_migrations()  # idempotent
