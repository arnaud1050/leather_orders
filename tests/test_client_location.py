"""
Where clients are: the "Outside Canada" province choice (CL12a) and the
City / Province columns on /clients (LST5e).

"Outside Canada" only records the fact for now — tax must not move.
"""

from datetime import date

from models import Client, Order, OrderLine, db

ADDRESS_FORM = {"first_name": "Marie", "last_name": "Alarie", "street": "",
                "city": "", "postal_code": ""}


def add_client(company, first, last, **fields):
    row = Client(company_id=company.id, first_name=first, last_name=last, **fields)
    db.session.add(row)
    db.session.commit()
    return row


# --- CL12a -----------------------------------------------------------------

def test_outside_canada_sets_the_flag_not_the_province(logged_in, client_record):
    logged_in.post(f"/clients/{client_record.id}/edit",
                   data={**ADDRESS_FORM, "province": "OUTSIDE"})
    row = db.session.get(Client, client_record.id)
    assert row.outside_canada is True
    assert row.province is None


def test_choosing_a_province_clears_outside_canada(logged_in, client_record):
    client_record.province, client_record.outside_canada = None, True
    db.session.commit()
    logged_in.post(f"/clients/{client_record.id}/edit",
                   data={**ADDRESS_FORM, "province": "BC"})
    db.session.expire_all()
    row = db.session.get(Client, client_record.id)
    assert row.outside_canada is False
    assert row.province == "BC"


def test_outside_canada_changes_no_order_total(company, client_record):
    """Same as having no province: nothing charged, before and after."""
    client_record.province = None
    order = Order(client_id=client_record.id, item="Bag", start=date(2026, 7, 1),
                  due=date(2026, 7, 15), status="confirmed")
    db.session.add(order)
    db.session.flush()
    db.session.add(OrderLine(order_id=order.id, description="Bag", quantity=1,
                             unit_price=100.0))
    db.session.flush()
    before = order.total
    client_record.outside_canada = True
    db.session.flush()
    assert order.total == before == 100.0


# --- LST5e -----------------------------------------------------------------

def test_clients_list_shows_city_and_province(logged_in, company):
    add_client(company, "Ana", "Coast", city="Vancouver", province="BC")
    add_client(company, "Ben", "Abroad", city="Lyon", outside_canada=True)
    body = logged_in.get("/clients").get_data(as_text=True)
    assert "Vancouver" in body
    assert 'title="British Columbia">BC<' in body
    assert "Lyon" in body
    assert "Outside Canada" in body


def _names_in_order(body, names):
    return sorted(names, key=body.index)


def test_clients_list_sorts_by_province_with_blanks_last(logged_in, company):
    add_client(company, "Cara", "Blank")
    add_client(company, "Dan", "Abroad", outside_canada=True)
    add_client(company, "Eve", "Ontario", province="ON")
    add_client(company, "Fay", "Coast", province="BC")
    body = logged_in.get("/clients?sort=province&dir=asc").get_data(as_text=True)
    names = ["Fay Coast", "Eve Ontario", "Dan Abroad", "Cara Blank"]
    assert _names_in_order(body, names) == names


def test_clients_list_sorts_by_city_with_blanks_last(logged_in, company):
    add_client(company, "Gus", "Nowhere")
    add_client(company, "Hal", "Toronto", city="Toronto")
    add_client(company, "Ida", "Montreal", city="montréal")
    body = logged_in.get("/clients?sort=city&dir=asc").get_data(as_text=True)
    names = ["Ida Montreal", "Hal Toronto", "Gus Nowhere"]
    assert _names_in_order(body, names) == names
