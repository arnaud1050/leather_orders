"""
Which province's tax applies, and the warnings around it (billing `C10`-`C12`).

Three cases move the tax away from "the client's province":
  - collected at the studio  -> the studio's province (Order.picked_up)
  - shipped outside Canada   -> no Canadian tax (Client.outside_canada)
  - PST charged for a province the studio isn't in -> charged, but flagged,
    because one pst_number gates BC, SK and MB alike.
"""

from datetime import date

import pytest

from billing.models import Invoice
from billing.services import invoicing
from billing.tax import place_of_supply, status_for, taxes_elsewhere, taxes_for
from models import Client, Order, OrderLine, db

GST_AND_PST = {"gst_number": "123456789 RT0001", "pst_number": "PST-1234"}


@pytest.fixture
def studio_in_qc(company):
    invoicing.update_profile(
        company.id, company.name, province="QC",
        gst_number="123456789 RT0001", qst_number="1234567890 TQ0001",
        pst_number=None,
    )
    db.session.flush()
    return company


def make_client(company, **fields):
    row = Client(company_id=company.id, first_name="Test", last_name="Client", **fields)
    db.session.add(row)
    db.session.flush()
    return row


def make_order(client_row, unit_price=100.0, **fields):
    row = Order(
        client_id=client_row.id, item="Bag", start=date(2026, 7, 1),
        due=date(2026, 7, 15), status="confirmed", **fields,
    )
    db.session.add(row)
    db.session.flush()
    db.session.add(OrderLine(order_id=row.id, description="Bag", quantity=1,
                             unit_price=unit_price))
    db.session.flush()
    return row


# --- the pure rule ---------------------------------------------------------

def test_place_of_supply():
    assert place_of_supply("BC", "QC") == "BC"
    assert place_of_supply("BC", "QC", picked_up=True) == "QC"
    assert place_of_supply(None, "QC", outside_canada=True) is None
    # Collected in person is a sale in Canada, wherever the buyer lives.
    assert place_of_supply(None, "QC", picked_up=True, outside_canada=True) == "QC"


def test_status_for_the_new_cases():
    assert status_for(None, GST_AND_PST, [], outside_canada=True) == "outside_canada"
    assert status_for(None, GST_AND_PST, [], picked_up=True) == "no_seller_province"


def test_taxes_elsewhere_flags_pst_for_another_province():
    charged = taxes_for("BC", GST_AND_PST, 100.0)
    assert taxes_elsewhere("BC", "SK", charged) == ["PST"]
    assert taxes_elsewhere("BC", "BC", charged) == []
    assert taxes_elsewhere("BC", None, charged) == []   # can't tell, so silent
    # QST has its own number, so it's never ambiguous.
    qc = taxes_for("QC", {**GST_AND_PST, "qst_number": "Q"}, 100.0)
    assert taxes_elsewhere("QC", "ON", qc) == []


# --- orders ----------------------------------------------------------------

def test_a_picked_up_order_is_taxed_in_the_studio_province(studio_in_qc):
    bc_client = make_client(studio_in_qc, province="BC")
    shipped = make_order(bc_client)
    collected = make_order(bc_client, picked_up=True)
    assert [t.label for t in shipped.tax_lines] == ["GST"]
    assert [t.label for t in collected.tax_lines] == ["GST", "QST"]
    assert collected.tax_province == "QC"


def test_a_picked_up_order_with_no_studio_province_says_so(company):
    invoicing.update_profile(company.id, company.name, province=None)
    db.session.flush()
    order = make_order(make_client(company, province="BC"), picked_up=True)
    assert order.tax_lines == []
    assert order.tax_status == "no_seller_province"


def test_an_export_is_charged_nothing_and_says_why(studio_in_qc):
    abroad = make_client(studio_in_qc, outside_canada=True)
    order = make_order(abroad)
    assert order.tax_lines == []
    assert order.tax_status == "outside_canada"


def test_an_export_collected_at_the_studio_is_taxed_there(studio_in_qc):
    abroad = make_client(studio_in_qc, outside_canada=True)
    order = make_order(abroad, picked_up=True)
    assert [t.label for t in order.tax_lines] == ["GST", "QST"]


def test_pst_for_another_province_is_charged_but_flagged(company):
    invoicing.update_profile(company.id, company.name, province="SK", **GST_AND_PST)
    db.session.flush()
    order = make_order(make_client(company, province="BC"))
    assert [t.label for t in order.tax_lines] == ["GST", "PST"]
    assert order.taxed_elsewhere == ("PST",)
    assert order.seller_province == "SK"


def test_freezing_uses_the_pickup_province(studio_in_qc):
    order = make_order(make_client(studio_in_qc, province="BC"), unit_price=1000.0,
                       picked_up=True)
    from billing_adapter import billable_for

    invoice = invoicing.create_invoice(studio_in_qc.id, billable_for(order))
    invoicing.set_status(studio_in_qc.id, invoice, "sent", billable_for(order))
    db.session.flush()
    assert [r.label for r in db.session.get(Invoice, invoice.id).tax_rows] == ["GST", "QST"]


# --- forms -----------------------------------------------------------------

def test_outside_canada_in_the_picker_sets_the_flag_not_the_province(
    logged_in, client_record
):
    logged_in.post(f"/clients/{client_record.id}/edit", data={
        "first_name": "Marie", "last_name": "Alarie", "street": "", "city": "",
        "province": "OUTSIDE", "postal_code": "",
    })
    row = db.session.get(Client, client_record.id)
    assert row.outside_canada is True
    assert row.province is None

    logged_in.post(f"/clients/{client_record.id}/edit", data={
        "first_name": "Marie", "last_name": "Alarie", "street": "", "city": "",
        "province": "BC", "postal_code": "",
    })
    db.session.expire_all()
    row = db.session.get(Client, client_record.id)
    assert row.outside_canada is False
    assert row.province == "BC"


def test_order_page_switches_between_pickup_and_shipped(logged_in, order):
    base = {"item": order.item, "start": order.start.isoformat(),
            "due": order.due.isoformat()}
    logged_in.post(f"/orders/{order.id}", data={**base, "fulfilment": "pickup"})
    assert db.session.get(Order, order.id).picked_up is True

    logged_in.post(f"/orders/{order.id}", data={**base, "fulfilment": "shipped"})
    db.session.expire_all()
    assert db.session.get(Order, order.id).picked_up is False


def test_order_page_shows_the_current_choice(logged_in, order):
    order.picked_up = True
    db.session.commit()
    body = logged_in.get(f"/orders/{order.id}").get_data(as_text=True)
    assert 'value="pickup" checked' in body
    assert 'value="shipped" checked' not in body


def test_a_form_without_the_radios_leaves_picked_up_alone(logged_in, order):
    """The timeline modal doesn't render them — hard rule 9."""
    order.picked_up = True
    db.session.commit()
    logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(), "due": order.due.isoformat(),
    })
    assert db.session.get(Order, order.id).picked_up is True


NEW_ORDER = {"item": "Card holder", "start": "2026-08-01", "due": "2026-08-15",
             "price": "80.00", "status": "confirmed"}


@pytest.mark.parametrize("choice, picked_up", [("pickup", True), ("shipped", False)])
def test_new_order_records_the_choice(logged_in, client_record, choice, picked_up):
    logged_in.post("/orders/new", data={
        **NEW_ORDER, "client_id": str(client_record.id), "fulfilment": choice,
    })
    assert Order.query.filter_by(item="Card holder").one().picked_up is picked_up


def test_new_order_refuses_a_missing_choice(logged_in, client_record):
    """No default: it decides the tax, so somebody has to pick."""
    response = logged_in.post("/orders/new", data={
        **NEW_ORDER, "client_id": str(client_record.id),
    })
    assert response.status_code == 400
    assert "picks this order up or it" in response.get_data(as_text=True)
    assert Order.query.filter_by(item="Card holder").count() == 0


def test_new_order_form_preselects_neither(logged_in, client_record):
    body = logged_in.get("/orders/new").get_data(as_text=True)
    assert 'name="fulfilment"' in body
    assert "checked" not in body.split('name="fulfilment"', 1)[1].split("</fieldset>", 1)[0]
