"""
Adding and deleting order lines: POST /orders/<id>/lines and
POST /orders/<id>/lines/<line_id>/delete.

Untested before now, despite both mutating what an order is worth (lines
are what an invoice is built from). Covers the happy path, the quantity
sanitising, the two "ignore incomplete input" branches, the
line-belongs-to-this-order guard on delete, and tenant isolation on both.
"""

from datetime import date

import pytest

from models import Client, Order, OrderLine, db
from tests.test_save_notices import located


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


# --- locked once the invoice is sent or void (OR7a) -------------------------

def _invoice(logged_in, order, status):
    from billing.models import Invoice

    logged_in.post(f"/subjects/{order.id}/invoice", data={})
    invoice = Invoice.query.filter_by(subject_id=order.id).one()
    if status != "draft":
        logged_in.post(f"/invoices/{invoice.id}/status",
                       data={"status": status, "due_date": "", "notes": ""})
    db.session.expire_all()
    return invoice


def _billing(logged_in, order):
    return logged_in.get(f"/orders/{order.id}/billing").get_data(as_text=True)


def test_a_draft_invoice_leaves_the_lines_editable(logged_in, order):
    _invoice(logged_in, order, "draft")
    before = len(order.lines)

    logged_in.post(f"/orders/{order.id}/lines", data={"description": "Monogram", "unit_price": "45"})

    assert len(db.session.get(Order, order.id).lines) == before + 1
    assert "Add line" in _billing(logged_in, order)



@pytest.mark.parametrize("status, done", [("sent", "sent"), ("void", "voided")])
def test_adding_a_line_is_refused_once_the_invoice_is_out(logged_in, order, status, done):
    invoice = _invoice(logged_in, order, status)
    before = len(order.lines)

    response = logged_in.post(f"/orders/{order.id}/lines", data={
        "description": "Monogram", "unit_price": "45",
        "return_to": f"/orders/{order.id}/billing", "notice_section": "line-items",
    }, follow_redirects=True)

    assert len(db.session.get(Order, order.id).lines) == before
    body = response.get_data(as_text=True)
    message = f"Invoice {invoice.number} has been {done}, so this order's lines can't change."
    assert located(body, message) == ("line-items", "error")


def test_deleting_a_line_is_refused_once_the_invoice_is_sent(logged_in, order):
    _invoice(logged_in, order, "sent")
    line = order.lines[0]

    logged_in.post(f"/orders/{order.id}/lines/{line.id}/delete")

    assert db.session.get(OrderLine, line.id) is not None


def test_a_sent_invoice_hides_the_line_controls_and_says_how_to_unlock(logged_in, order):
    _invoice(logged_in, order, "sent")
    body = _billing(logged_in, order)

    assert "Add line" not in body
    assert f"/orders/{order.id}/lines/" not in body  # no trash buttons
    assert "set" in body and "back to Draft first" in body


def test_back_to_draft_unlocks_the_lines(logged_in, order):
    """The correction flow (billing F15) is the way through."""
    invoice = _invoice(logged_in, order, "sent")
    logged_in.post(f"/invoices/{invoice.id}/status",
                   data={"status": "draft", "due_date": "", "notes": ""})
    before = len(db.session.get(Order, order.id).lines)

    logged_in.post(f"/orders/{order.id}/lines", data={"description": "Monogram", "unit_price": "45"})

    assert len(db.session.get(Order, order.id).lines) == before + 1
