"""
Orders-list column order/visibility — Settings > Orders > "Orders list
columns" (see _order_columns_for / _save_order_columns in app.py).

Stored as one JSON blob on Company.order_columns rather than a table like
OrderType/SourceOption: this is a fixed set of 9 known columns, not an
open-ended list a user names, so there's nothing per-row to hide-not-delete.
Two routes back it, mirroring existing Settings conventions rather than
inventing a new interaction: /settings/order-columns/<key>/toggle (immediate
POST + redirect, same shape as toggle_order_type) and
/settings/order-columns/reorder (JSON fetch on drop, same shape as
documents.reorder_types).
"""

import json

from models import Company, OrderType, db


def test_orders_list_shows_all_columns_by_default(logged_in, order):
    response = logged_in.get("/orders")

    assert response.status_code == 200
    for key in ("item", "client", "status", "start", "due", "total", "paid", "balance"):
        assert f"sort={key}".encode() in response.data


def test_orders_list_hides_type_column_with_no_order_types(logged_in, order):
    response = logged_in.get("/orders")

    assert b"sort=type" not in response.data


def test_toggle_order_column_hides_it_from_the_orders_list(logged_in, order, company):
    logged_in.post("/settings/order-columns/paid/toggle")

    response = logged_in.get("/orders")

    assert b"sort=paid" not in response.data
    saved = json.loads(db.session.get(Company, company.id).order_columns)
    assert {"key": "paid", "visible": False} in saved


def test_toggle_order_column_twice_shows_it_again(logged_in, order):
    logged_in.post("/settings/order-columns/paid/toggle")
    logged_in.post("/settings/order-columns/paid/toggle")

    response = logged_in.get("/orders")

    assert b"sort=paid" in response.data


def test_toggle_unknown_column_key_404s(logged_in, order):
    response = logged_in.post("/settings/order-columns/nonsense/toggle")

    assert response.status_code == 404


def test_reorder_order_columns_persists_the_new_order(logged_in, order, company):
    logged_in.post(
        "/settings/order-columns/reorder",
        data=json.dumps({"order": [
            "due", "client", "item", "status", "start", "type", "total", "paid", "balance",
        ]}),
        content_type="application/json",
    )

    saved = json.loads(db.session.get(Company, company.id).order_columns)
    assert [c["key"] for c in saved][:3] == ["due", "client", "item"]


def test_reorder_order_columns_preserves_visibility(logged_in, order, company):
    logged_in.post("/settings/order-columns/paid/toggle")  # hide Paid first

    logged_in.post(
        "/settings/order-columns/reorder",
        data=json.dumps({"order": [
            "paid", "item", "client", "type", "status", "start", "due", "total", "balance",
        ]}),
        content_type="application/json",
    )

    saved = json.loads(db.session.get(Company, company.id).order_columns)
    paid_entry = next(c for c in saved if c["key"] == "paid")
    assert paid_entry["visible"] is False


def test_reorder_order_columns_reflects_in_orders_list_header_order(logged_in, order):
    logged_in.post(
        "/settings/order-columns/reorder",
        data=json.dumps({"order": [
            "due", "item", "client", "status", "start", "type", "total", "paid", "balance",
        ]}),
        content_type="application/json",
    )

    html = logged_in.get("/orders").data.decode()
    assert html.index("sort=due") < html.index("sort=item") < html.index("sort=client")


def test_reorder_ignores_unknown_keys(logged_in, order, company):
    logged_in.post(
        "/settings/order-columns/reorder",
        data=json.dumps({"order": [
            "item", "bogus", "client", "type", "status", "start", "due", "total", "paid", "balance",
        ]}),
        content_type="application/json",
    )

    saved = json.loads(db.session.get(Company, company.id).order_columns)
    assert "bogus" not in [c["key"] for c in saved]
    assert len(saved) == 9


def test_reorder_with_empty_order_is_a_no_op(logged_in, order, company):
    logged_in.post(
        "/settings/order-columns/reorder",
        data=json.dumps({"order": []}),
        content_type="application/json",
    )

    assert db.session.get(Company, company.id).order_columns is None


def test_reorder_appends_a_key_the_client_omitted(logged_in, order, company):
    """A hand-crafted POST missing a column shouldn't silently drop it."""
    logged_in.post(
        "/settings/order-columns/reorder",
        data=json.dumps({"order": ["client", "item"]}),
        content_type="application/json",
    )

    saved = json.loads(db.session.get(Company, company.id).order_columns)
    assert {c["key"] for c in saved} == set(
        ["item", "client", "type", "status", "start", "due", "total", "paid", "balance"]
    )


def test_settings_page_lists_columns_in_default_order(logged_in, order, company):
    db.session.add(OrderType(company_id=company.id, label="Custom Order", sort_order=0))
    db.session.commit()

    html = logged_in.get("/settings/orders").data.decode()
    section = html[html.index("Orders list columns"):]

    assert section.index("Item") < section.index("Client") < section.index("Type") < section.index("Status")


def test_settings_page_omits_type_column_with_no_order_types(logged_in, order):
    html = logged_in.get("/settings/orders").data.decode()
    section = html[html.index("Orders list columns"):html.index("Document types")]

    assert "Type" not in section


def test_settings_page_shows_type_column_once_an_order_type_exists(logged_in, order, company):
    db.session.add(OrderType(company_id=company.id, label="Custom Order", sort_order=0))
    db.session.commit()

    html = logged_in.get("/settings/orders").data.decode()
    section = html[html.index("Orders list columns"):html.index("Document types")]

    assert "Type" in section


def test_column_preferences_are_scoped_per_company(logged_in, order, company, other_company):
    logged_in.post("/settings/order-columns/paid/toggle")

    assert db.session.get(Company, company.id).order_columns is not None
    assert db.session.get(Company, other_company.id).order_columns is None


def test_type_column_shows_once_a_type_exists_and_is_used(logged_in, order, company):
    order_type = OrderType(company_id=company.id, label="Custom Order", sort_order=0)
    db.session.add(order_type)
    db.session.flush()
    order.order_type_id = order_type.id
    db.session.commit()

    response = logged_in.get("/orders")

    assert b"sort=type" in response.data


def test_hiding_type_column_still_hides_it_even_when_order_types_exist(logged_in, order, company):
    db.session.add(OrderType(company_id=company.id, label="Custom Order", sort_order=0))
    db.session.commit()
    logged_in.post("/settings/order-columns/type/toggle")

    response = logged_in.get("/orders")

    assert b"sort=type" not in response.data
