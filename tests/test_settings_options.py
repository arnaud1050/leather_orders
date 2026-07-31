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
