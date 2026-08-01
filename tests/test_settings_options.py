"""
Duplicate-label prevention for the two company-configurable option lists
on Settings > Orders / Settings > Clients: OrderType and SourceOption.

Same rule, same reasoning, as documents.services.add_document_type's
duplicate check (see tests/test_documents.py) — two rows reading the same
name is confusing whether or not one is hidden, so the match is
case-insensitive and checked against active and hidden rows alike.
"""

from models import OrderType, SourceOption, db


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
