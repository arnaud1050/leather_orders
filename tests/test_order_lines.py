"""
Adding and deleting order lines: POST /orders/<id>/lines and
POST /orders/<id>/lines/<line_id>/delete.

Untested before now, despite both mutating what an order is worth (lines
are what an invoice is built from). Covers the happy path, the quantity
sanitising, the two "ignore incomplete input" branches, the
line-belongs-to-this-order guard on delete, and tenant isolation on both.
"""

from datetime import date

from models import Client, Order, OrderLine, db


def _foreign_order(other_company):
    """An order under a second tenant, with one line."""
    client = Client(
        company_id=other_company.id, first_name="Foreign", last_name="Client")
    db.session.add(client)
    db.session.flush()
    order = Order(
        client_id=client.id, item="Foreign order",
        start=date(2026, 7, 1), due=date(2026, 7, 15), status="confirmed")
    db.session.add(order)
    db.session.flush()
    line = OrderLine(
        order_id=order.id, description="Foreign line", quantity=1, unit_price=10.0)
    db.session.add(line)
    db.session.flush()
    return order, line


# --- adding a line ---------------------------------------------------------

def test_add_order_line_appends_a_line(logged_in, order):
    before = len(order.lines)

    logged_in.post(f"/orders/{order.id}/lines", data={
        "description": "Monogram", "unit_price": "45", "quantity": "2",
    })

    lines = db.session.get(Order, order.id).lines
    assert len(lines) == before + 1
    added = [line for line in lines if line.description == "Monogram"][0]
    assert added.quantity == 2
    assert added.unit_price == 45.0
    assert added.sort_order == before  # appended after the existing line(s)


def test_add_order_line_defaults_a_nonpositive_or_nonnumeric_quantity_to_one(logged_in, order):
    logged_in.post(f"/orders/{order.id}/lines", data={
        "description": "Zero qty", "unit_price": "10", "quantity": "0",
    })
    logged_in.post(f"/orders/{order.id}/lines", data={
        "description": "Junk qty", "unit_price": "10", "quantity": "abc",
    })

    lines = {line.description: line for line in db.session.get(Order, order.id).lines}
    assert lines["Zero qty"].quantity == 1
    assert lines["Junk qty"].quantity == 1


def test_add_order_line_ignores_a_blank_description(logged_in, order):
    before = len(order.lines)

    logged_in.post(f"/orders/{order.id}/lines", data={
        "description": "   ", "unit_price": "45",
    })

    assert len(db.session.get(Order, order.id).lines) == before


def test_add_order_line_ignores_a_missing_price(logged_in, order):
    before = len(order.lines)

    logged_in.post(f"/orders/{order.id}/lines", data={"description": "No price"})

    assert len(db.session.get(Order, order.id).lines) == before


def test_add_order_line_is_scoped_to_the_tenant(logged_in, other_company):
    foreign_order, _ = _foreign_order(other_company)
    before = len(foreign_order.lines)

    response = logged_in.post(f"/orders/{foreign_order.id}/lines", data={
        "description": "Injected", "unit_price": "999",
    })

    assert response.status_code == 404
    assert len(db.session.get(Order, foreign_order.id).lines) == before


def test_add_order_line_requires_a_login(app, order):
    response = app.test_client().post(
        f"/orders/{order.id}/lines", data={"description": "x", "unit_price": "1"})

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


# --- deleting a line -------------------------------------------------------

def test_delete_order_line_removes_it(logged_in, order):
    line = order.lines[0]

    logged_in.post(f"/orders/{order.id}/lines/{line.id}/delete")

    assert db.session.get(OrderLine, line.id) is None


def test_delete_order_line_ignores_a_line_from_another_order(logged_in, order, other_company):
    """The route filters on order_id as well as line id, so passing this
    order's id with a foreign line's id must not delete the foreign line."""
    _, foreign_line = _foreign_order(other_company)

    logged_in.post(f"/orders/{order.id}/lines/{foreign_line.id}/delete")

    assert db.session.get(OrderLine, foreign_line.id) is not None


def test_delete_order_line_is_scoped_to_the_tenant(logged_in, other_company):
    foreign_order, foreign_line = _foreign_order(other_company)

    response = logged_in.post(
        f"/orders/{foreign_order.id}/lines/{foreign_line.id}/delete")

    assert response.status_code == 404
    assert db.session.get(OrderLine, foreign_line.id) is not None


def test_delete_order_line_requires_a_login(app, order):
    line = order.lines[0]

    response = app.test_client().post(f"/orders/{order.id}/lines/{line.id}/delete")

    assert response.status_code == 302
    assert db.session.get(OrderLine, line.id) is not None
