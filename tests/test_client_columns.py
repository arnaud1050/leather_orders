"""
Clients-list column order/visibility — Settings > Clients > "Clients list
columns" (see CLIENT_COLUMNS / _client_columns_for in app.py), LST13–LST15.

The Orders-list editor (tests/test_order_columns.py) over a different
canonical dict, stored on Company.client_columns. The one difference is
Name, which can be moved but never hidden.
"""

import json

from models import Client, Company, db

ALL_KEYS = ["name", "email", "phone", "city", "province", "orders", "value"]


def _saved(company):
    return json.loads(db.session.get(Company, company.id).client_columns)


def _reorder(logged_in, order):
    return logged_in.post(
        "/settings/client-columns/reorder",
        data=json.dumps({"order": order}),
        content_type="application/json",
    )


def _table(html):
    start = html.index('id="clients-table"')
    return html[start:html.index("</table>", start)]


def _settings_section(html):
    return html[html.index("Clients list columns"):]


def test_clients_list_default_columns(logged_in, client_record):
    table = _table(logged_in.get("/clients").get_data(as_text=True))

    for label in ("Name", "Email", "Phone", "Orders", "Lifetime value"):
        assert label in table
    assert "sort=city" not in table
    assert "sort=province" not in table


def test_city_and_province_start_hidden_and_can_be_shown(logged_in, client_record, company):
    section = _settings_section(logged_in.get("/settings/clients").get_data(as_text=True))
    assert section.count("(hidden)") == 2

    logged_in.post("/settings/client-columns/city/toggle")
    logged_in.post("/settings/client-columns/province/toggle")

    table = _table(logged_in.get("/clients").get_data(as_text=True))
    assert "sort=city" in table and "sort=province" in table
    assert {"key": "city", "visible": True} in _saved(company)


def test_hiding_a_column_drops_its_header_and_cells(logged_in, company):
    db.session.add(Client(company_id=company.id, first_name="Ana", last_name="Coast",
                          email="ana@example.invalid"))
    db.session.commit()

    logged_in.post("/settings/client-columns/email/toggle")

    table = _table(logged_in.get("/clients").get_data(as_text=True))
    assert "Email" not in table
    assert "ana@example.invalid" not in table
    assert {"key": "email", "visible": False} in _saved(company)


def test_toggling_twice_shows_the_column_again(logged_in, client_record):
    logged_in.post("/settings/client-columns/email/toggle")
    logged_in.post("/settings/client-columns/email/toggle")

    assert "Email" in _table(logged_in.get("/clients").get_data(as_text=True))


def test_name_column_cannot_be_hidden(logged_in, client_record, company):
    response = logged_in.post("/settings/client-columns/name/toggle")

    assert response.status_code == 302
    assert db.session.get(Company, company.id).client_columns is None
    assert "sort=name" in logged_in.get("/clients").get_data(as_text=True)


def test_name_shows_even_if_a_saved_blob_hides_it(logged_in, client_record, company):
    db.session.get(Company, company.id).client_columns = json.dumps(
        [{"key": "name", "visible": False}])
    db.session.commit()

    assert "sort=name" in logged_in.get("/clients").get_data(as_text=True)


def test_settings_page_offers_no_hide_button_for_name(logged_in):
    section = _settings_section(logged_in.get("/settings/clients").get_data(as_text=True))

    assert "/settings/client-columns/name/toggle" not in section
    assert "/settings/client-columns/email/toggle" in section


def test_toggle_unknown_column_key_404s(logged_in):
    assert logged_in.post("/settings/client-columns/nonsense/toggle").status_code == 404


def test_reorder_persists_and_reorders_the_table(logged_in, client_record, company):
    _reorder(logged_in, ["value", "name", "email", "phone", "city", "province", "orders"])

    assert [c["key"] for c in _saved(company)][:2] == ["value", "name"]
    table = _table(logged_in.get("/clients").get_data(as_text=True))
    assert table.index("sort=value") < table.index("sort=name") < table.index("sort=orders")


def test_reorder_preserves_visibility(logged_in, company):
    logged_in.post("/settings/client-columns/phone/toggle")

    _reorder(logged_in, ["phone", "name", "email", "city", "province", "orders", "value"])

    phone = next(c for c in _saved(company) if c["key"] == "phone")
    assert phone["visible"] is False


def test_reorder_ignores_unknown_keys_and_appends_omitted_ones(logged_in, company):
    _reorder(logged_in, ["city", "bogus", "name"])

    keys = [c["key"] for c in _saved(company)]
    assert keys[:2] == ["city", "name"]
    assert sorted(keys) == sorted(ALL_KEYS)


def test_reorder_with_empty_order_is_a_no_op(logged_in, company):
    _reorder(logged_in, [])

    assert db.session.get(Company, company.id).client_columns is None


def test_unparseable_blob_falls_back_to_the_default_order(logged_in, client_record, company):
    db.session.get(Company, company.id).client_columns = "not json"
    db.session.commit()

    table = _table(logged_in.get("/clients").get_data(as_text=True))
    assert table.index("sort=name") < table.index("sort=orders") < table.index("sort=value")
    assert "sort=city" not in table


def test_settings_page_lists_hidden_columns_too(logged_in):
    logged_in.post("/settings/client-columns/email/toggle")

    section = _settings_section(logged_in.get("/settings/clients").get_data(as_text=True))
    assert "(hidden)" in section
    assert section.index("Name") < section.index("Email") < section.index("Lifetime value")


def test_column_preferences_are_scoped_per_company(logged_in, company, other_company):
    logged_in.post("/settings/client-columns/email/toggle")

    assert db.session.get(Company, company.id).client_columns is not None
    assert db.session.get(Company, other_company.id).client_columns is None


def test_order_and_client_columns_are_stored_separately(logged_in, company):
    logged_in.post("/settings/client-columns/email/toggle")

    assert db.session.get(Company, company.id).order_columns is None


def test_sorting_by_a_hidden_column_still_works(logged_in, company):
    """LST15: hiding a column hides only the column, not its sort key."""
    for first, city in (("Al", "Toronto"), ("Bea", "Montreal")):
        db.session.add(Client(company_id=company.id, first_name=first,
                              last_name="Test", city=city))
    db.session.commit()  # City is hidden by default (LST13a)

    html = logged_in.get("/clients?sort=city&dir=asc").get_data(as_text=True)

    assert "sort=city" not in _table(html)
    assert html.index("Bea Test") < html.index("Al Test")
