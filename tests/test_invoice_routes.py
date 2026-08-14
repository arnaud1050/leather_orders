"""
The invoicing routes, and the one rule that only exists in `app.py`:
`freeze()` runs on the draft -> issued transition and at no other time.

That comparison of the status before and after the form is applied is the
whole protection. Freeze too eagerly and re-saving a sent invoice rewrites
what the client was told; freeze not at all and an issued invoice drifts
with company settings forever.
"""

from datetime import date

import pytest

from billing.models import Invoice
from billing.services import invoicing
from models import Client, Order, OrderLine, db


@pytest.fixture
def registered(company):
    invoicing.update_profile(company.id, company.name,
                             gst_number="123456789 RT0001",
                             qst_number="1234567890 TQ0001")
    db.session.flush()
    return company


@pytest.fixture
def order(registered, client_record):
    row = Order(
        client_id=client_record.id, item="Briefcase",
        start=date(2026, 7, 1), due=date(2026, 7, 15), status="confirmed",
    )
    db.session.add(row)
    db.session.flush()
    db.session.add(OrderLine(
        order_id=row.id, description="Briefcase", quantity=1, unit_price=1000.0,
    ))
    db.session.commit()
    return row


def invoice_for(order_row):
    return db.session.get(Order, order_row.id).invoice


# --- Creating -------------------------------------------------------------

def test_creating_an_invoice_assigns_the_next_number(logged_in, order):
    logged_in.post(f"/subjects/{order.id}/invoice", data={})
    db.session.expire_all()
    assert invoice_for(order).number == "BM-2026-0001"


def test_a_new_invoice_starts_as_an_unfrozen_draft(logged_in, order):
    logged_in.post(f"/subjects/{order.id}/invoice", data={})
    db.session.expire_all()
    invoice = invoice_for(order)
    assert invoice.status == "draft"
    assert not invoice.is_frozen


def test_double_submitting_does_not_burn_a_second_number(logged_in, order):
    """A double-clicked button must not consume a number, or the sequence
    grows gaps nobody can account for."""
    logged_in.post(f"/subjects/{order.id}/invoice", data={})
    logged_in.post(f"/subjects/{order.id}/invoice", data={})
    db.session.expire_all()
    assert Invoice.query.count() == 1


def test_creating_an_invoice_for_another_tenants_order_404s(
    logged_in, other_company
):
    """Order lookup is scoped by company — without that filter this route
    would issue one company's number against another's order."""
    outsider = Client(company_id=other_company.id, first_name="X", last_name="Y")
    db.session.add(outsider)
    db.session.flush()
    foreign = Order(client_id=outsider.id, item="Not yours",
                    start=date(2026, 7, 1), due=date(2026, 7, 2), status="confirmed")
    db.session.add(foreign)
    db.session.commit()

    response = logged_in.post(f"/subjects/{foreign.id}/invoice", data={})

    assert response.status_code == 404
    assert Invoice.query.count() == 0


def test_viewing_another_tenants_invoice_404s(logged_in, other_company, order):
    logged_in.post(f"/subjects/{order.id}/invoice", data={})
    db.session.expire_all()
    invoice = invoice_for(order)
    invoice.company_id = other_company.id
    db.session.commit()

    assert logged_in.get(f"/invoices/{invoice.id}").status_code == 404


# --- The transition that freezes -----------------------------------------

def test_marking_it_sent_freezes_the_issuer_and_the_money(logged_in, order, registered):
    logged_in.post(f"/subjects/{order.id}/invoice", data={})
    db.session.expire_all()
    invoice_id = invoice_for(order).id

    logged_in.post(f"/invoices/{invoice_id}/status",
                   data={"status": "sent", "due_date": "", "notes": ""})
    db.session.expire_all()

    invoice = db.session.get(Invoice, invoice_id)
    assert invoice.is_frozen
    assert invoice.issued_subtotal == 1000.0
    assert invoice.issuer_name == "By Monsieur"
    assert [r.label for r in invoice.tax_rows] == ["GST", "QST"]


def test_resaving_a_sent_invoice_does_not_rewrite_history(logged_in, order, registered):
    """The bug this guards against: editing the notes on an issued invoice
    silently re-stamping it with today's company details."""
    logged_in.post(f"/subjects/{order.id}/invoice", data={})
    db.session.expire_all()
    invoice_id = invoice_for(order).id
    logged_in.post(f"/invoices/{invoice_id}/status",
                   data={"status": "sent", "due_date": "", "notes": ""})

    invoicing.update_profile(registered.id, "Renamed Studio",
                             gst_number="999999999 RT0001")
    db.session.commit()

    logged_in.post(f"/invoices/{invoice_id}/status",
                   data={"status": "sent", "due_date": "", "notes": "edited later"})
    db.session.expire_all()

    invoice = db.session.get(Invoice, invoice_id)
    assert invoice.issuer_name == "By Monsieur"
    assert invoice.issuer_gst_number == "123456789 RT0001"
    assert invoice.notes == "edited later"


def test_a_draft_saved_as_a_draft_stays_unfrozen(logged_in, order):
    logged_in.post(f"/subjects/{order.id}/invoice", data={})
    db.session.expire_all()
    invoice_id = invoice_for(order).id

    logged_in.post(f"/invoices/{invoice_id}/status",
                   data={"status": "draft", "due_date": "", "notes": "still working"})
    db.session.expire_all()

    assert not db.session.get(Invoice, invoice_id).is_frozen


def test_voiding_a_draft_freezes_it_too(logged_in, order):
    """Void is still a way out of draft — freezing keeps the voided
    document reproducible."""
    logged_in.post(f"/subjects/{order.id}/invoice", data={})
    db.session.expire_all()
    invoice_id = invoice_for(order).id

    logged_in.post(f"/invoices/{invoice_id}/status",
                   data={"status": "void", "due_date": "", "notes": ""})
    db.session.expire_all()

    assert db.session.get(Invoice, invoice_id).is_frozen


def test_paid_cannot_be_set_by_hand(logged_in, order):
    """"paid" is derived from payments. Accepting it as a stored status
    would let the invoice disagree with the money actually received."""
    logged_in.post(f"/subjects/{order.id}/invoice", data={})
    db.session.expire_all()
    invoice_id = invoice_for(order).id

    logged_in.post(f"/invoices/{invoice_id}/status",
                   data={"status": "paid", "due_date": "", "notes": ""})
    db.session.expire_all()

    invoice = db.session.get(Invoice, invoice_id)
    assert invoice.status == "draft"
    # ...and it doesn't show as paid either, since nothing has been paid.
    assert db.session.get(Order, order.id).invoice_status == "draft"


def test_an_unknown_status_is_ignored(logged_in, order):
    logged_in.post(f"/subjects/{order.id}/invoice", data={})
    db.session.expire_all()
    invoice_id = invoice_for(order).id

    logged_in.post(f"/invoices/{invoice_id}/status",
                   data={"status": "nonsense", "due_date": "", "notes": ""})
    db.session.expire_all()

    assert db.session.get(Invoice, invoice_id).status == "draft"


# --- What the invoice page actually shows --------------------------------

def test_the_invoice_page_shows_the_tax_breakdown(logged_in, order, registered):
    logged_in.post(f"/subjects/{order.id}/invoice", data={})
    db.session.expire_all()
    invoice_id = invoice_for(order).id

    body = logged_in.get(f"/invoices/{invoice_id}").get_data(as_text=True)

    assert "Subtotal" in body
    assert "GST (5%)" in body
    assert "QST (9.975%)" in body
    assert "$1149.75" in body or "1,149.75" in body


def test_the_order_page_warns_when_tax_cannot_be_calculated(
    logged_in, registered, company
):
    row = Client(company_id=company.id, first_name="No", last_name="Province")
    db.session.add(row)
    db.session.flush()
    order_row = Order(client_id=row.id, item="Belt", start=date(2026, 7, 1),
                      due=date(2026, 7, 2), status="confirmed")
    db.session.add(order_row)
    db.session.flush()
    db.session.add(OrderLine(order_id=order_row.id, description="Belt",
                             quantity=1, unit_price=100.0))
    db.session.commit()

    # Line items and tax live on the Billing tab, not the Details tab.
    body = logged_in.get(f"/orders/{order_row.id}/billing").get_data(as_text=True)

    assert "No sales tax is being charged" in body


def test_the_invoice_list_totals_are_tax_inclusive(logged_in, order, registered):
    logged_in.post(f"/subjects/{order.id}/invoice", data={})

    body = logged_in.get("/invoices").get_data(as_text=True)

    assert "1149.75" in body


def test_invoice_pages_require_a_login(app, order):
    anonymous = app.test_client()
    for path in ("/invoices", f"/orders/{order.id}/billing"):
        response = anonymous.get(path)
        assert response.status_code == 302, path
        assert "/login" in response.headers["Location"]


def test_issuing_an_invoice_requires_a_login(app, order):
    """POST-only, so it needs its own check — a GET would 405 before auth
    is ever consulted and prove nothing."""
    anonymous = app.test_client()

    response = anonymous.post(f"/subjects/{order.id}/invoice", data={})

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert Invoice.query.count() == 0
