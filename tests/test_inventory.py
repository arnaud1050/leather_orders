"""
inventory/ — types (hide-don't-delete + duplicate rejection), items (CRUD,
stock levels), drawing materials onto an order (snapshot pricing, stock
decrement/restore, allow-negative), one-off "Others" costs, tenant isolation
on every route, and that none of this touches Order.total/OrderLine/the
invoice — this module is cost-tracking only (see inventory/__init__.py).
"""

import json
from datetime import date

import pytest

from models import Client, Order, db

from inventory import services
from inventory.models import InventoryItem, InventoryType, InventoryUnit, OrderMaterial, OrderMaterialOther


# --- units: which UNIT_CATALOG keys a company offers, and in what order ----

def test_list_units_includes_each_by_default(company):
    units = services.list_units(company.id)
    assert [u.key for u in units] == ["each"]
    assert units[0].is_default is True


def test_each_is_scoped_per_company(company, other_company):
    """_ensure_default_unit seeds "each" lazily per company — visiting it
    for one company must not create a row for another."""
    services.list_units(company.id)
    assert InventoryUnit.query.filter_by(company_id=other_company.id).count() == 0


def test_add_unit_appends_after_each(company):
    unit = services.add_unit(company.id, "sqft")
    assert unit.key == "sqft"
    assert unit.sort_order == 1  # after "each" (sort_order 0, count 1)
    assert [u.key for u in services.list_units(company.id)] == ["each", "sqft"]


def test_add_unit_rejects_an_unknown_key(company):
    assert services.add_unit(company.id, "furlong") is None


def test_add_unit_rejects_each(company):
    """"Each" is always present already — re-adding it via the catalog
    picker would be meaningless."""
    assert services.add_unit(company.id, "each") is None


def test_add_unit_rejects_a_duplicate(company):
    services.add_unit(company.id, "sqft")
    assert services.add_unit(company.id, "sqft") is None
    assert InventoryUnit.query.filter_by(company_id=company.id, key="sqft").count() == 1


def test_add_unit_rejects_a_duplicate_of_a_hidden_unit(company):
    unit = services.add_unit(company.id, "sqft")
    services.toggle_unit(company.id, unit.id)
    assert services.add_unit(company.id, "sqft") is None


def test_available_catalog_units_excludes_each_and_added_units(company):
    services.add_unit(company.id, "sqft")
    available = {u["key"] for u in services.available_catalog_units(company.id)}
    assert "each" not in available
    assert "sqft" not in available
    assert "gram" in available  # not yet added


def test_available_catalog_units_still_excludes_a_hidden_unit(company):
    """A hidden unit isn't offered to "add" again — bring it back via
    Unhide in the existing list instead, same as SourceOption/OrderType."""
    unit = services.add_unit(company.id, "sqft")
    services.toggle_unit(company.id, unit.id)
    available = {u["key"] for u in services.available_catalog_units(company.id)}
    assert "sqft" not in available


def test_available_catalog_units_are_grouped_alphabetically(company):
    """The "Add a unit" dropdown's <optgroup>s should read Area, Count,
    Length, Volume, Weight — alphabetical, not UNIT_CATALOG's own
    Count/Length/Area/Weight/Volume declaration order."""
    groups = [u["group"] for u in services.available_catalog_units(company.id)]
    seen_in_order = list(dict.fromkeys(groups))
    assert seen_in_order == sorted(seen_in_order)
    assert seen_in_order[0] == "Area"


def test_toggle_unit_flips_is_active(company):
    unit = services.add_unit(company.id, "sqft")
    services.toggle_unit(company.id, unit.id)
    assert db.session.get(InventoryUnit, unit.id).is_active is False
    services.toggle_unit(company.id, unit.id)
    assert db.session.get(InventoryUnit, unit.id).is_active is True


def test_toggle_unit_is_a_no_op_for_each(company):
    each = services.list_units(company.id)[0]
    services.toggle_unit(company.id, each.id)
    assert db.session.get(InventoryUnit, each.id).is_active is True


def test_toggle_unit_is_scoped_to_the_tenant(company, other_company):
    unit = services.add_unit(other_company.id, "sqft")
    services.toggle_unit(company.id, unit.id)
    assert db.session.get(InventoryUnit, unit.id).is_active is True  # untouched


def test_delete_unit_removes_it_when_unused(company):
    unit = services.add_unit(company.id, "sqft")
    services.delete_unit(company.id, unit.id)
    assert db.session.get(InventoryUnit, unit.id) is None


def test_delete_unit_is_blocked_once_referenced(company):
    unit = services.add_unit(company.id, "sqft")
    services.add_item(
        company.id, name="Horween Chromexcel", unit="sqft",
        inventory_type_id=None, quantity_on_hand=10, unit_price=1,
    )
    services.delete_unit(company.id, unit.id)
    assert db.session.get(InventoryUnit, unit.id) is not None


def test_delete_unit_is_scoped_to_the_tenant(company, other_company):
    """delete_unit filters on company_id just as toggle_unit does — deleting
    with the wrong company must leave the other tenant's unit alone (UN10)."""
    unit = services.add_unit(other_company.id, "sqft")
    services.delete_unit(company.id, unit.id)
    assert db.session.get(InventoryUnit, unit.id) is not None  # untouched


def test_delete_unit_is_a_no_op_for_each(company):
    each = services.list_units(company.id)[0]
    services.delete_unit(company.id, each.id)
    assert db.session.get(InventoryUnit, each.id) is not None


def test_reorder_units_sets_sort_order_from_position(company):
    a = services.add_unit(company.id, "sqft")
    b = services.add_unit(company.id, "gram")
    services.reorder_units(company.id, [b.id, a.id])
    assert db.session.get(InventoryUnit, b.id).sort_order == 0
    assert db.session.get(InventoryUnit, a.id).sort_order == 1


def test_reorder_units_can_reposition_each(company):
    """"Each" can't be hidden or deleted, but it isn't pinned in place —
    a company can drag it anywhere in the list."""
    each = services.list_units(company.id)[0]
    unit = services.add_unit(company.id, "sqft")
    services.reorder_units(company.id, [unit.id, each.id])
    assert db.session.get(InventoryUnit, unit.id).sort_order == 0
    assert db.session.get(InventoryUnit, each.id).sort_order == 1


def test_reorder_units_skips_ids_outside_the_tenant(company, other_company):
    theirs = services.add_unit(other_company.id, "sqft")
    services.reorder_units(company.id, [theirs.id])
    assert db.session.get(InventoryUnit, theirs.id).sort_order == 1  # untouched


def test_add_item_accepts_any_catalog_unit_regardless_of_company_selection(company):
    """The Units list controls what's *offered* in the dropdown, never what
    add_item/edit_item *accept* — a catalog key is always valid even if this
    company has never added it to their settings."""
    assert InventoryUnit.query.filter_by(company_id=company.id, key="gram").first() is None
    item = services.add_item(
        company.id, name="Clay", unit="gram",
        inventory_type_id=None, quantity_on_hand=500, unit_price=0.02,
    )
    assert item is not None
    assert item.unit == "gram"


def test_add_item_rejects_a_key_outside_the_catalog(company):
    assert services.add_item(
        company.id, name="Widget", unit="furlong",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    ) is None


# --- routes: units settings --------------------------------------------

def test_add_unit_route_creates_a_unit(logged_in, company):
    response = logged_in.post("/settings/inventory-units", data={"key": "sqft"}, follow_redirects=True)
    assert response.status_code == 200
    assert InventoryUnit.query.filter_by(company_id=company.id, key="sqft").first() is not None


def test_toggle_unit_route_flips_is_active(logged_in, company):
    unit = services.add_unit(company.id, "sqft")
    logged_in.post(f"/settings/inventory-units/{unit.id}/toggle")
    assert db.session.get(InventoryUnit, unit.id).is_active is False


def test_delete_unit_route_removes_it_when_unused(logged_in, company):
    unit = services.add_unit(company.id, "sqft")
    logged_in.post(f"/settings/inventory-units/{unit.id}/delete")
    assert db.session.get(InventoryUnit, unit.id) is None


def test_reorder_units_route_persists_order(logged_in, company):
    a = services.add_unit(company.id, "sqft")
    b = services.add_unit(company.id, "gram")
    logged_in.post(
        "/settings/inventory-units/reorder",
        data=json.dumps({"order": [b.id, a.id]}),
        content_type="application/json",
    )
    assert db.session.get(InventoryUnit, b.id).sort_order == 0
    assert db.session.get(InventoryUnit, a.id).sort_order == 1


def test_settings_inventory_page_lists_units_first(logged_in, company):
    services.add_unit(company.id, "sqft")
    response = logged_in.get("/settings/inventory")
    assert response.status_code == 200
    body = response.data.decode()
    assert body.index("Units") < body.index("Inventory types")
    assert "Sqft" in body
    assert "always available" in body  # Each's tag


def test_settings_inventory_page_excludes_added_units_from_add_dropdown(logged_in, company):
    services.add_unit(company.id, "sqft")
    body = logged_in.get("/settings/inventory").data.decode()
    assert 'value="gram"' in body  # still offered
    assert 'value="sqft"' not in body  # already added, not re-offered


# --- migrations: backfilling units from existing items ----------------------

def test_migration_backfills_a_unit_from_an_existing_item(company):
    from inventory import migrations as inventory_migrations

    services.add_item(
        company.id, name="Horween Chromexcel", unit="sqft",
        inventory_type_id=None, quantity_on_hand=10, unit_price=1,
    )
    assert InventoryUnit.query.filter_by(company_id=company.id, key="sqft").first() is None

    inventory_migrations.run_migrations()

    backfilled = InventoryUnit.query.filter_by(company_id=company.id, key="sqft").first()
    assert backfilled is not None
    assert backfilled.is_active is True

    each = InventoryUnit.query.filter_by(company_id=company.id, key="each").first()
    assert each is not None
    assert each.is_default is True


def test_migration_backfill_is_idempotent(company):
    from inventory import migrations as inventory_migrations

    services.add_item(
        company.id, name="Horween Chromexcel", unit="sqft",
        inventory_type_id=None, quantity_on_hand=10, unit_price=1,
    )
    inventory_migrations.run_migrations()
    inventory_migrations.run_migrations()
    assert InventoryUnit.query.filter_by(company_id=company.id, key="sqft").count() == 1


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


# --- I13: the low-stock warning point on an item --------------------------

def test_add_item_stores_the_low_stock_threshold(company):
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=100, unit_price=1,
        low_stock_threshold=10,
    )
    assert item.low_stock_threshold == 10


def test_add_item_defaults_the_threshold_to_zero(company):
    """Omitted (as every pre-existing caller does) means 0 — the low-stock
    band off, only the hard zero/negative signal applies."""
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=5, unit_price=1,
    )
    assert item.low_stock_threshold == 0


def test_edit_item_overwrites_the_low_stock_threshold(company):
    """Always overwritten, like quantity/price (I6) — not a partial-update
    field that a blank keeps."""
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=100, unit_price=1,
        low_stock_threshold=10,
    )
    services.edit_item(
        company.id, item.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=100, unit_price=1,
        low_stock_threshold=25,
    )
    assert db.session.get(InventoryItem, item.id).low_stock_threshold == 25


def test_is_low_stock_only_in_the_band_above_zero(company):
    """0 < qty <= threshold. Out-of-stock (<= 0) is the red tier's job, and a
    0 threshold can never be low — the bands are disjoint."""
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=10, unit_price=1,
        low_stock_threshold=10,
    )
    assert item.is_low_stock is True  # exactly at the threshold counts

    item.quantity_on_hand = 11
    assert item.is_low_stock is False  # above it

    item.quantity_on_hand = 0
    assert item.is_low_stock is False  # zero belongs to the red tier

    item.quantity_on_hand = 5
    item.low_stock_threshold = 0
    assert item.is_low_stock is False  # no warning point set


def test_edit_item_ignores_a_blank_name(company):
    """Editing is a partial update, unlike creation (I6): a blank name keeps
    the existing one rather than failing the edit — while quantity/price are
    still overwritten, proving it's a partial update, not a whole no-op."""
    item = services.add_item(
        company.id, name="Original", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    )
    services.edit_item(
        company.id, item.id, name="   ", unit="each",
        inventory_type_id=None, quantity_on_hand=9, unit_price=7,
    )
    updated = db.session.get(InventoryItem, item.id)
    assert updated.name == "Original"      # blank name ignored
    assert updated.quantity_on_hand == 9   # other fields still applied
    assert updated.unit_price == 7


def test_edit_item_ignores_an_invalid_unit(company):
    """An unrecognised unit keeps the existing one (I6), the same
    drop-silently shape as the blank name."""
    item = services.add_item(
        company.id, name="Widget", unit="sqft",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    )
    services.edit_item(
        company.id, item.id, name="Widget", unit="not-a-real-unit",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    )
    assert db.session.get(InventoryItem, item.id).unit == "sqft"  # kept


def test_edit_item_clearing_the_type_sets_it_to_none(company):
    """Editing inventory_type_id to empty clears the type (I7)."""
    leather = services.add_type(company.id, "Leather")
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=leather.id, quantity_on_hand=1, unit_price=1,
    )
    services.edit_item(
        company.id, item.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=1, unit_price=1,
    )
    assert db.session.get(InventoryItem, item.id).inventory_type_id is None


def test_edit_item_with_a_foreign_type_id_falls_back_to_none(company, other_company):
    """A type id belonging to another company resolves to "no type" on edit,
    the same drop-silently behaviour I4 asserts for creation — the gap I7
    names, since only the creation path was covered before."""
    own_type = services.add_type(company.id, "Leather")
    foreign_type = services.add_type(other_company.id, "Lining")
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=own_type.id, quantity_on_hand=1, unit_price=1,
    )
    services.edit_item(
        company.id, item.id, name="Widget", unit="each",
        inventory_type_id=foreign_type.id, quantity_on_hand=1, unit_price=1,
    )
    assert db.session.get(InventoryItem, item.id).inventory_type_id is None


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


def test_selectable_items_are_ordered_by_type_sort_order_then_name(company):
    """The merged, deduplicated set is ordered by (type sort_order, item
    name), untyped last (S3) — previously only membership was asserted, not
    the order. Labels and names are chosen so a naive alphabetical sort would
    give a different answer, pinning it to sort_order specifically."""
    first_type = services.add_type(company.id, "Zebra")      # sort_order 0
    second_type = services.add_type(company.id, "Antelope")  # sort_order 1
    for name, type_id in [
        ("Zzz", first_type.id),    # same type as Alpha, later name
        ("Aaa", second_type.id),   # earlier name but later type
        ("Untyped", None),         # no type -> sorts last
        ("Alpha", first_type.id),
    ]:
        services.add_item(
            company.id, name=name, unit="each",
            inventory_type_id=type_id, quantity_on_hand=1, unit_price=1,
        )

    names = [i.name for i in services.selectable_items(company.id)]
    # Zebra (sort_order 0) before Antelope (1) despite "A" < "Z"; within
    # Zebra, name breaks the tie (Alpha < Zzz); untyped (9999) last.
    assert names == ["Alpha", "Zzz", "Aaa", "Untyped"]


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


def test_edit_other_rejects_a_blank_description(company, order):
    """Same guard as add_other: a blank description is refused and the row
    left untouched (O3), not blanked out."""
    other = services.add_other(company.id, order.id, "Buckle", 8.5)
    assert services.edit_other(order.id, other.id, "   ", 9.0) is None
    unchanged = db.session.get(OrderMaterialOther, other.id)
    assert unchanged.description == "Buckle"
    assert unchanged.cost == 8.5


def test_edit_other_rejects_a_missing_cost(company, order):
    other = services.add_other(company.id, order.id, "Buckle", 8.5)
    assert services.edit_other(order.id, other.id, "Replacement buckle", None) is None
    unchanged = db.session.get(OrderMaterialOther, other.id)
    assert unchanged.description == "Buckle"
    assert unchanged.cost == 8.5


def test_delete_other_removes_the_row(company, order):
    other = services.add_other(company.id, order.id, "Buckle", 8.5)
    services.delete_other(order.id, other.id)
    assert db.session.get(OrderMaterialOther, other.id) is None


def test_list_materials_for_order_returns_every_row_in_id_order(company, order):
    """M12: every row for the order, in insertion (id) order — and only that
    order's rows, never another order's."""
    item = services.add_item(
        company.id, name="Leather", unit="sqft",
        inventory_type_id=None, quantity_on_hand=100, unit_price=5,
    )
    m1 = services.add_material(company.id, order.id, item.id, quantity_used=1)
    m2 = services.add_material(company.id, order.id, item.id, quantity_used=2)
    m3 = services.add_material(company.id, order.id, item.id, quantity_used=3)

    other_order = _order_for(company.id)
    services.add_material(company.id, other_order.id, item.id, quantity_used=9)

    rows = services.list_materials_for_order(order.id)
    assert [r.id for r in rows] == [m1.id, m2.id, m3.id]


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
    "/settings/inventory-units",
    "/settings/inventory-units/1/toggle",
    "/settings/inventory-units/1/delete",
    "/settings/inventory-units/reorder",
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


def test_inventory_list_sorts_by_type(logged_in, company):
    """The default sort, and the one branch of INVENTORY_SORT_KEYS not
    covered by name/price above — a type is a joined label, so it's sorted
    in Python by the type's label, ties broken by item name (U1)."""
    zinc = services.add_type(company.id, "Zinc")
    alpha = services.add_type(company.id, "Alpha")
    services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=zinc.id, quantity_on_hand=1, unit_price=1,
    )
    services.add_item(
        company.id, name="Gadget", unit="each",
        inventory_type_id=alpha.id, quantity_on_hand=1, unit_price=1,
    )
    body = logged_in.get("/inventory?sort=type&dir=asc").data.decode()
    assert body.index("Gadget") < body.index("Widget")  # Alpha-type before Zinc-type


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


# --- stock alerts: out-of-stock nav badge + Materials-tab warning ----------

def test_out_of_stock_count_counts_zero_and_negative_active_items(company):
    services.add_item(
        company.id, name="Plenty", unit="each",
        inventory_type_id=None, quantity_on_hand=10, unit_price=1,
    )
    services.add_item(
        company.id, name="Exactly out", unit="each",
        inventory_type_id=None, quantity_on_hand=0, unit_price=1,
    )
    services.add_item(
        company.id, name="Overdrawn", unit="each",
        inventory_type_id=None, quantity_on_hand=-3, unit_price=1,
    )
    assert services.out_of_stock_count(company.id) == 2


def test_out_of_stock_count_excludes_hidden_items(company):
    item = services.add_item(
        company.id, name="Retired", unit="each",
        inventory_type_id=None, quantity_on_hand=-1, unit_price=1,
    )
    services.toggle_item(company.id, item.id)  # hide it
    assert services.out_of_stock_count(company.id) == 0


def test_out_of_stock_count_is_scoped_to_the_tenant(company, other_company):
    services.add_item(
        other_company.id, name="Theirs", unit="each",
        inventory_type_id=None, quantity_on_hand=-1, unit_price=1,
    )
    assert services.out_of_stock_count(company.id) == 0


def test_understocked_materials_for_order_reads_the_live_quantity(company, order):
    item = services.add_item(
        company.id, name="Horween Chromexcel", unit="sqft",
        inventory_type_id=None, quantity_on_hand=5, unit_price=12.5,
    )
    material = services.add_material(company.id, order.id, item.id, quantity_used=2)
    assert services.understocked_materials_for_order(order.id) == []

    # Draw the rest of the stock via a second order-less item edit (simulating
    # another order/restock moving the same live item to zero or below) —
    # the check must read the item's *current* quantity, not the material's
    # own frozen snapshot.
    services.edit_item(
        company.id, item.id, name=item.name, unit=item.unit,
        inventory_type_id=None, quantity_on_hand=0, unit_price=item.unit_price,
    )
    assert services.understocked_materials_for_order(order.id) == [material]


def test_understocked_materials_for_order_excludes_fully_stocked_materials(company, order):
    item = services.add_item(
        company.id, name="Widget", unit="each",
        inventory_type_id=None, quantity_on_hand=10, unit_price=1,
    )
    services.add_material(company.id, order.id, item.id, quantity_used=1)
    assert services.understocked_materials_for_order(order.id) == []


def test_understocked_materials_for_order_ignores_others(company, order):
    """OrderMaterialOther has no inventory_item_id at all, so it can never
    appear here regardless of how it's used."""
    services.add_other(company.id, order.id, description="Rush courier fee", cost=15.0)
    assert services.understocked_materials_for_order(order.id) == []


def test_stock_alert_badge_appears_when_an_item_is_out_of_stock(logged_in, company):
    services.add_item(
        company.id, name="Overdrawn", unit="each",
        inventory_type_id=None, quantity_on_hand=0, unit_price=1,
    )
    body = logged_in.get("/").get_data(as_text=True)
    assert "nav-badge--stock-alert" in body


def test_stock_alert_badge_absent_when_nothing_is_out_of_stock(logged_in, company):
    services.add_item(
        company.id, name="Plenty", unit="each",
        inventory_type_id=None, quantity_on_hand=10, unit_price=1,
    )
    body = logged_in.get("/").get_data(as_text=True)
    assert "nav-badge--stock-alert" not in body


def test_stock_alert_badge_clears_after_restocking(logged_in, company):
    item = services.add_item(
        company.id, name="Overdrawn", unit="each",
        inventory_type_id=None, quantity_on_hand=0, unit_price=1,
    )
    assert "nav-badge--stock-alert" in logged_in.get("/").get_data(as_text=True)

    services.edit_item(
        company.id, item.id, name=item.name, unit=item.unit,
        inventory_type_id=None, quantity_on_hand=5, unit_price=item.unit_price,
    )
    assert "nav-badge--stock-alert" not in logged_in.get("/").get_data(as_text=True)


def test_materials_tab_shows_warning_when_understocked(logged_in, company, order):
    item = services.add_item(
        company.id, name="Horween Chromexcel", unit="sqft",
        inventory_type_id=None, quantity_on_hand=2, unit_price=12.5,
    )
    logged_in.post(
        f"/orders/{order.id}/materials/add",
        data={"inventory_item_id": str(item.id), "quantity_used": "5"},
    )
    body = logged_in.get(f"/orders/{order.id}/materials").get_data(as_text=True)
    assert "warning-note" in body
    assert "Horween Chromexcel" in body


def test_materials_tab_has_no_warning_when_fully_stocked(logged_in, company, order):
    item = services.add_item(
        company.id, name="Horween Chromexcel", unit="sqft",
        inventory_type_id=None, quantity_on_hand=50, unit_price=12.5,
    )
    logged_in.post(
        f"/orders/{order.id}/materials/add",
        data={"inventory_item_id": str(item.id), "quantity_used": "5"},
    )
    body = logged_in.get(f"/orders/{order.id}/materials").get_data(as_text=True)
    assert "This order's materials aren't fully covered" not in body


# --- low-stock (amber) alerts: nav badge + Materials-tab warning -----------

def test_low_stock_count_counts_items_in_the_amber_band(company):
    """Above zero and at or below the item's own threshold."""
    services.add_item(
        company.id, name="Low", unit="each",
        inventory_type_id=None, quantity_on_hand=5, unit_price=1,
        low_stock_threshold=5,  # exactly at threshold counts
    )
    services.add_item(
        company.id, name="Also low", unit="each",
        inventory_type_id=None, quantity_on_hand=3, unit_price=1,
        low_stock_threshold=10,
    )
    assert services.low_stock_count(company.id) == 2


def test_low_stock_count_excludes_out_of_stock_and_zero_threshold(company):
    """The band is disjoint from out-of-stock (V1 owns <= 0), and an item with
    no warning point set (threshold 0) is never low."""
    services.add_item(  # out of stock — the red tier, not this one
        company.id, name="Empty", unit="each",
        inventory_type_id=None, quantity_on_hand=0, unit_price=1,
        low_stock_threshold=5,
    )
    services.add_item(  # plenty, but no threshold set
        company.id, name="Untracked", unit="each",
        inventory_type_id=None, quantity_on_hand=2, unit_price=1,
        low_stock_threshold=0,
    )
    services.add_item(  # plenty, above its threshold
        company.id, name="Fine", unit="each",
        inventory_type_id=None, quantity_on_hand=50, unit_price=1,
        low_stock_threshold=5,
    )
    assert services.low_stock_count(company.id) == 0


def test_low_stock_count_excludes_hidden_items(company):
    item = services.add_item(
        company.id, name="Retired", unit="each",
        inventory_type_id=None, quantity_on_hand=3, unit_price=1,
        low_stock_threshold=10,
    )
    services.toggle_item(company.id, item.id)  # hide it
    assert services.low_stock_count(company.id) == 0


def test_low_stock_count_is_scoped_to_the_tenant(company, other_company):
    services.add_item(
        other_company.id, name="Theirs", unit="each",
        inventory_type_id=None, quantity_on_hand=3, unit_price=1,
        low_stock_threshold=10,
    )
    assert services.low_stock_count(company.id) == 0


def test_out_of_stock_and_low_stock_counts_are_disjoint(company):
    """With one item out of stock and a separate one merely low, each count
    reports exactly its own tier — the out-of-stock item never leaks into the
    low count, and vice versa (V9's disjoint-bands guarantee)."""
    services.add_item(
        company.id, name="Empty", unit="each",
        inventory_type_id=None, quantity_on_hand=0, unit_price=1,
        low_stock_threshold=5,
    )
    services.add_item(
        company.id, name="Low", unit="each",
        inventory_type_id=None, quantity_on_hand=3, unit_price=1,
        low_stock_threshold=10,
    )
    assert services.out_of_stock_count(company.id) == 1
    assert services.low_stock_count(company.id) == 1


def test_low_stock_badge_appears_when_an_item_is_low(logged_in, company):
    services.add_item(
        company.id, name="Low", unit="each",
        inventory_type_id=None, quantity_on_hand=3, unit_price=1,
        low_stock_threshold=10,
    )
    body = logged_in.get("/").get_data(as_text=True)
    assert "nav-badge--low-stock" in body


def test_low_stock_badge_absent_when_nothing_is_low(logged_in, company):
    services.add_item(
        company.id, name="Plenty", unit="each",
        inventory_type_id=None, quantity_on_hand=50, unit_price=1,
        low_stock_threshold=10,
    )
    body = logged_in.get("/").get_data(as_text=True)
    assert "nav-badge--low-stock" not in body


def test_low_stock_badge_clears_after_restocking(logged_in, company):
    item = services.add_item(
        company.id, name="Low", unit="each",
        inventory_type_id=None, quantity_on_hand=3, unit_price=1,
        low_stock_threshold=10,
    )
    assert "nav-badge--low-stock" in logged_in.get("/").get_data(as_text=True)

    services.edit_item(
        company.id, item.id, name=item.name, unit=item.unit,
        inventory_type_id=None, quantity_on_hand=50, unit_price=item.unit_price,
        low_stock_threshold=10,
    )
    assert "nav-badge--low-stock" not in logged_in.get("/").get_data(as_text=True)


def test_both_stock_badges_render_together(logged_in, company):
    """One out-of-stock item and a separate low one make both nav badges
    render side by side — red (--stock-alert) and amber (--low-stock) — each
    with its own count of 1, never double-counting the same item (V3 + V11)."""
    services.add_item(
        company.id, name="Empty", unit="each",
        inventory_type_id=None, quantity_on_hand=0, unit_price=1,
        low_stock_threshold=5,
    )
    services.add_item(
        company.id, name="Low", unit="each",
        inventory_type_id=None, quantity_on_hand=3, unit_price=1,
        low_stock_threshold=10,
    )
    body = logged_in.get("/").get_data(as_text=True)
    assert "nav-badge--stock-alert" in body
    assert "nav-badge--low-stock" in body


def test_low_stock_materials_for_order_reads_live_quantity_and_threshold(company, order):
    item = services.add_item(
        company.id, name="Horween Chromexcel", unit="sqft",
        inventory_type_id=None, quantity_on_hand=20, unit_price=12.5,
        low_stock_threshold=8,
    )
    material = services.add_material(company.id, order.id, item.id, quantity_used=2)
    assert services.low_stock_materials_for_order(order.id) == []  # 18 on hand, above 8

    # A later draw (simulated via edit) drops the live item into the band.
    services.edit_item(
        company.id, item.id, name=item.name, unit=item.unit,
        inventory_type_id=None, quantity_on_hand=5, unit_price=item.unit_price,
        low_stock_threshold=8,
    )
    assert services.low_stock_materials_for_order(order.id) == [material]


def test_low_stock_and_out_of_stock_banners_are_distinct_on_the_tab(logged_in, company, order):
    """Two materials, one out of stock (red banner) and one merely low (amber
    banner) — both appear, in their own distinct notes."""
    empty = services.add_item(
        company.id, name="Empty leather", unit="sqft",
        inventory_type_id=None, quantity_on_hand=2, unit_price=1,
        low_stock_threshold=5,
    )
    low = services.add_item(
        company.id, name="Low thread", unit="each",
        inventory_type_id=None, quantity_on_hand=20, unit_price=1,
        low_stock_threshold=6,
    )
    logged_in.post(
        f"/orders/{order.id}/materials/add",
        data={"inventory_item_id": str(empty.id), "quantity_used": "2"},
    )  # -> 0 on hand, out of stock
    logged_in.post(
        f"/orders/{order.id}/materials/add",
        data={"inventory_item_id": str(low.id), "quantity_used": "15"},
    )  # -> 5 on hand, at/below threshold 6, low
    body = logged_in.get(f"/orders/{order.id}/materials").get_data(as_text=True)
    assert "warning-note--alert" in body  # red out-of-stock banner
    assert "Running low on" in body        # amber low-stock banner
    assert "Empty leather" in body
    assert "Low thread" in body


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
