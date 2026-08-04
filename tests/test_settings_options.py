"""
Duplicate-label prevention for the two company-configurable option lists
on Settings > Orders / Settings > Clients: OrderType and SourceOption.

Same rule, same reasoning, as documents.services.add_document_type's
duplicate check (see tests/test_documents.py) — two rows reading the same
name is confusing whether or not one is hidden, so the match is
case-insensitive and checked against active and hidden rows alike.
"""

from datetime import date

from models import Client, Order, OrderType, SourceOption, db


def test_add_order_type_rejects_a_case_insensitive_duplicate(logged_in, company):
    logged_in.post("/settings/order-types", data={"label": "Custom Order"})

    response = logged_in.post(
        "/settings/order-types", data={"label": "  custom order  "},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"already exists" in response.data
    assert OrderType.query.filter_by(company_id=company.id).count() == 1


def test_add_order_type_rejects_a_duplicate_of_a_hidden_type(logged_in, company):
    logged_in.post("/settings/order-types", data={"label": "Custom Order"})
    order_type = OrderType.query.filter_by(company_id=company.id).one()
    logged_in.post(f"/settings/order-types/{order_type.id}/toggle")  # hide it

    logged_in.post("/settings/order-types", data={"label": "Custom Order"})

    assert OrderType.query.filter_by(company_id=company.id).count() == 1


def test_add_order_type_allows_the_same_label_in_another_company(logged_in, company, other_company):
    db.session.add(OrderType(company_id=other_company.id, label="Custom Order", sort_order=0))
    db.session.commit()

    logged_in.post("/settings/order-types", data={"label": "Custom Order"})

    assert OrderType.query.filter_by(company_id=company.id).count() == 1


def test_add_source_option_rejects_a_case_insensitive_duplicate(logged_in, company):
    logged_in.post("/settings/sources", data={"label": "Word of Mouth"})

    response = logged_in.post(
        "/settings/sources", data={"label": "word of mouth"}, follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"already exists" in response.data
    assert SourceOption.query.filter_by(company_id=company.id).count() == 1


def test_add_source_option_rejects_a_duplicate_of_a_hidden_option(logged_in, company):
    logged_in.post("/settings/sources", data={"label": "Word of Mouth"})
    option = SourceOption.query.filter_by(company_id=company.id).one()
    logged_in.post(f"/settings/sources/{option.id}/toggle")  # hide it

    logged_in.post("/settings/sources", data={"label": "Word of Mouth"})

    assert SourceOption.query.filter_by(company_id=company.id).count() == 1


# --- drag-to-reorder: /settings/sources/reorder ----------------------------

def test_reorder_source_options_sets_sort_order_from_position(logged_in, company):
    a = SourceOption(company_id=company.id, label="A", sort_order=0)
    b = SourceOption(company_id=company.id, label="B", sort_order=1)
    c = SourceOption(company_id=company.id, label="C", sort_order=2)
    db.session.add_all([a, b, c])
    db.session.commit()

    logged_in.post(
        "/settings/sources/reorder",
        json={"order": [c.id, a.id, b.id]},
    )

    ordered = (
        SourceOption.query.filter_by(company_id=company.id)
        .order_by(SourceOption.sort_order).all()
    )
    assert [o.label for o in ordered] == ["C", "A", "B"]


def test_reorder_source_options_ignores_ids_from_another_tenant(logged_in, company, other_company):
    mine = SourceOption(company_id=company.id, label="Mine", sort_order=0)
    theirs = SourceOption(company_id=other_company.id, label="Theirs", sort_order=0)
    db.session.add_all([mine, theirs])
    db.session.commit()

    logged_in.post(
        "/settings/sources/reorder",
        json={"order": [theirs.id, mine.id]},
    )

    assert db.session.get(SourceOption, theirs.id).sort_order == 0  # untouched


def test_reorder_source_options_reflects_on_the_settings_page(logged_in, company):
    a = SourceOption(company_id=company.id, label="Alpha", sort_order=0)
    b = SourceOption(company_id=company.id, label="Beta", sort_order=1)
    db.session.add_all([a, b])
    db.session.commit()

    logged_in.post("/settings/sources/reorder", json={"order": [b.id, a.id]})

    html = logged_in.get("/settings/clients").get_data(as_text=True)
    assert html.index("Beta") < html.index("Alpha")


def test_reorder_source_options_requires_login(app):
    response = app.test_client().post(
        "/settings/sources/reorder", json={"order": []},
    )
    assert response.status_code in (302, 401)


# --- hard delete, gated on can_delete (hard rule 8) ------------------------
#
# The two lists hide-don't-delete once a record references them, and hard
# delete only while `can_delete` (no client/order points at the row). Both
# the delete-it and the guard-blocks-it branches were previously untested.

def test_delete_order_type_removes_an_unreferenced_type(logged_in, company):
    order_type = OrderType(company_id=company.id, label="Retired", sort_order=0)
    db.session.add(order_type)
    db.session.commit()
    type_id = order_type.id

    logged_in.post(f"/settings/order-types/{type_id}/delete")

    assert db.session.get(OrderType, type_id) is None


def test_delete_order_type_keeps_a_type_an_order_uses(logged_in, company, client_record):
    order_type = OrderType(company_id=company.id, label="In use", sort_order=0)
    db.session.add(order_type)
    db.session.flush()
    db.session.add(Order(
        client_id=client_record.id, item="Bag", order_type_id=order_type.id,
        start=date(2026, 7, 1), due=date(2026, 7, 15), status="in_progress"))
    db.session.commit()
    type_id = order_type.id

    logged_in.post(f"/settings/order-types/{type_id}/delete")

    assert db.session.get(OrderType, type_id) is not None  # guarded by can_delete


def test_delete_source_option_removes_an_unreferenced_option(logged_in, company):
    option = SourceOption(company_id=company.id, label="Retired", sort_order=0)
    db.session.add(option)
    db.session.commit()
    option_id = option.id

    logged_in.post(f"/settings/sources/{option_id}/delete")

    assert db.session.get(SourceOption, option_id) is None


def test_delete_source_option_keeps_an_option_a_client_uses(logged_in, company):
    option = SourceOption(company_id=company.id, label="In use", sort_order=0)
    db.session.add(option)
    db.session.flush()
    client = Client(company_id=company.id, first_name="Referring", last_name="Client")
    client.sources.append(option)
    db.session.add(client)
    db.session.commit()
    option_id = option.id

    logged_in.post(f"/settings/sources/{option_id}/delete")

    assert db.session.get(SourceOption, option_id) is not None  # guarded by can_delete
