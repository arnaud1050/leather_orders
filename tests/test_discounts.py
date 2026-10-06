"""
Whole-order discounts: a percentage or a fixed amount, with an optional
label, taken off the line items **before tax** (billing DS1–DS6, core
OR7b).

The rule this file defends: tax is charged on the discounted amount, and
once the invoice is out the discount is frozen with everything else — a
discount changed afterwards must not change a number the client was given.
"""

import pytest

from billing import pdf, tax
from billing.documents import Branding, Discount, clean_discount
from billing.services import invoicing
from billing_adapter import billable_for
from models import Order, db
from tests.test_invoicing import doc_for, draft, issue, make_order, registered  # noqa: F401
from tests.test_order_lines import _billing, _invoice
from tests.test_save_notices import located


def _discount(order_row, kind, value, label=None):
    order_row.discount_kind, order_row.discount_value = kind, value
    order_row.discount_label = label
    db.session.flush()


# --- clean_discount: what counts as a discount (DS1) -----------------------

@pytest.mark.parametrize("kind, value", [
    (None, 10), ("bogus", 10), ("percent", None), ("percent", 0),
    ("amount", -5), ("percent", float("inf")), ("percent", float("nan")),
    ("amount", "abc"),
])
def test_anything_unusable_is_no_discount(kind, value):
    assert clean_discount(kind, value) is None


def test_a_percentage_is_capped_at_a_hundred():
    assert clean_discount("percent", 250).value == 100.0


def test_a_blank_label_is_none_and_spaces_collapse():
    assert clean_discount("amount", 20, "   ").label is None
    assert clean_discount("amount", 20, "  Returning \n client ").label == "Returning client"


# --- The amount and how it reads (DS2, DS3) --------------------------------

def test_a_percentage_rounds_to_the_cent():
    assert Discount("percent", 12.5).amount_on(99.99) == 12.5  # 12.49875


def test_a_fixed_amount_never_exceeds_what_there_is_to_discount():
    assert Discount("amount", 500).amount_on(120.0) == 120.0


def test_the_description_names_the_rate_only_for_a_percentage():
    assert Discount("percent", 10, "Returning client").description == "Returning client (10%)"
    assert Discount("percent", 12.5).description == "Discount (12.5%)"
    assert Discount("amount", 20, "Friends & family").description == "Friends & family"
    assert Discount("amount", 20).description == "Discount"


# --- Tax is charged on the net (DS4) ---------------------------------------

def test_tax_is_charged_on_the_discounted_subtotal(registered, client_record):
    order = make_order(client_record, 1000.0)
    _discount(order, "percent", 10)

    expected = tax.taxes_for("QC", invoicing.profile_for(registered.id).issuer.tax_registrations, 900.0)
    assert [line.amount for line in order.tax_lines] == [line.amount for line in expected]
    assert order.subtotal == 1000.0          # the lines, at full price
    assert order.discount_amount == 100.0
    assert order.total == pytest.approx(900.0 + sum(line.amount for line in expected))


def test_a_fixed_discount_comes_off_before_tax_too(registered, client_record):
    order = make_order(client_record, 1000.0)
    _discount(order, "amount", 250)

    assert billable_for(order).subtotal == 750.0
    assert order.total == pytest.approx(750.0 + sum(line.amount for line in order.tax_lines))


def test_no_discount_changes_nothing(registered, client_record):
    order = make_order(client_record, 1000.0)

    assert order.discount_amount == 0.0
    assert order.discount_description is None
    assert billable_for(order).subtotal == 1000.0


# --- Frozen at issue (DS5) -------------------------------------------------

def test_issuing_freezes_the_discount(registered, client_record):
    order = make_order(client_record, 1000.0)
    _discount(order, "percent", 10, "Returning client")
    invoice = issue(registered, order)

    assert invoice.issued_subtotal == 900.0
    assert invoice.issued_discount == 100.0
    assert invoice.issued_discount_description == "Returning client (10%)"


def test_an_issued_invoice_ignores_a_later_discount_change(registered, client_record):
    order = make_order(client_record, 1000.0)
    _discount(order, "percent", 10)
    invoice = issue(registered, order)
    total = order.total

    _discount(order, "amount", 400, "Changed my mind")

    doc = doc_for(registered, invoice)
    assert doc.discount == 100.0
    assert doc.discount_description == "Discount (10%)"
    assert doc.subtotal == 900.0
    assert order.total == total


def test_a_draft_follows_the_discount_live(registered, client_record):
    order = make_order(client_record, 1000.0)
    invoice = draft(registered, order)

    _discount(order, "amount", 50)

    doc = doc_for(registered, invoice)
    assert (doc.items_total, doc.discount, doc.subtotal) == (1000.0, 50.0, 950.0)


# --- What prints (DS6) -----------------------------------------------------

@pytest.mark.parametrize("layout", ["classic", "banded"])
def test_both_pdf_layouts_print_the_discount_line(registered, client_record, layout):
    order = make_order(client_record, 1000.0)
    _discount(order, "percent", 10, "Returning client")
    doc = doc_for(registered, draft(registered, order))

    html = pdf.render_html(doc, Branding(template=layout))

    assert "Returning client (10%)" in html
    assert "&minus;$100.00" in html
    assert "$1000.00" in html  # the Subtotal row shows the lines at full price


def test_no_discount_prints_no_discount_line(registered, client_record):
    order = make_order(client_record, 1000.0)
    html = pdf.render_html(doc_for(registered, draft(registered, order)))

    assert "Discount" not in html


# --- The order page (OR7b) -------------------------------------------------

def test_saving_a_discount_stores_it(logged_in, order):
    logged_in.post(f"/orders/{order.id}/discount", data={
        "discount_kind": "amount", "discount_value": "20", "discount_label": "Friends",
    })

    row = db.session.get(Order, order.id)
    assert (row.discount_kind, row.discount_value, row.discount_label) == ("amount", 20.0, "Friends")
    assert "Friends" in _billing(logged_in, order)


def test_an_empty_amount_clears_the_discount(logged_in, order):
    _discount(order, "percent", 10, "Old")
    db.session.commit()

    logged_in.post(f"/orders/{order.id}/discount", data={
        "discount_kind": "percent", "discount_value": "", "discount_label": "Old",
    })

    row = db.session.get(Order, order.id)
    assert (row.discount_kind, row.discount_value, row.discount_label) == (None, None, None)


def test_the_discount_is_refused_once_the_invoice_is_out(logged_in, order):
    invoice = _invoice(logged_in, order, "sent")

    response = logged_in.post(f"/orders/{order.id}/discount", data={
        "discount_kind": "percent", "discount_value": "10",
        "return_to": f"/orders/{order.id}/billing", "notice_section": "discount",
    }, follow_redirects=True)

    assert db.session.get(Order, order.id).discount_kind is None
    message = f"Invoice {invoice.number} has been sent, so this order's discount can't change."
    assert located(response.get_data(as_text=True), message) == ("discount", "error")
    assert "Save discount" not in _billing(logged_in, order)


def test_a_draft_invoice_leaves_the_discount_editable(logged_in, order):
    _invoice(logged_in, order, "draft")

    logged_in.post(f"/orders/{order.id}/discount", data={
        "discount_kind": "percent", "discount_value": "10",
    })

    assert db.session.get(Order, order.id).discount_value == 10.0


def test_another_tenants_order_is_not_found(logged_in, other_company):
    from tests.test_order_lines import _foreign_order

    foreign, _ = _foreign_order(other_company)
    db.session.commit()

    response = logged_in.post(f"/orders/{foreign.id}/discount", data={
        "discount_kind": "percent", "discount_value": "50",
    })

    assert response.status_code == 404
    assert db.session.get(Order, foreign.id).discount_kind is None
