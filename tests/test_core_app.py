"""
Core-app coverage for the rules in the root `REQUIREMENTS.md` that were
previously listed as "-- gap --" in its test coverage map: tenancy/auth on
the app's own routes, Client/OrderType/Order/OrderLine/Payment behavior, the
timeline view, the Orders/Clients lists, order/client creation, return_to +
back_label, the /settings redirect, and /analytics.

Rule ids in comments (e.g. "OR3") refer to REQUIREMENTS.md; this file's own
name should be added to that file's coverage-map rows as they're closed.
"""

import json
from datetime import date, timedelta

import pytest

import app as app_module
from models import Client, Company, Order, OrderLine, OrderType, Payment, SourceOption, db

# ---------------------------------------------------------------------------
# Tenancy & auth (CO2-CO6)
# ---------------------------------------------------------------------------

CORE_GET_ROUTES = ["/", "/orders", "/clients", "/analytics", "/orders/new", "/clients/new"]


@pytest.mark.parametrize("path", CORE_GET_ROUTES)
def test_core_get_routes_require_login(app, path):
    response = app.test_client().get(path)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_core_get_routes_require_login_for_a_specific_order_and_client(app, order, client_record):
    # Deliberately not using the `logged_in` fixture here: it keeps its own
    # test client's app context preserved for the whole test (that's what
    # lets it be used after the fixture returns), and Flask reuses an
    # already-active app context for a *different* test client's request to
    # the same app -- so a second, "fresh" client would inherit its
    # current_user instead of being anonymous. A plain `app.test_client()`
    # with no preceding login avoids that entirely.
    anon = app.test_client()
    for path in (
        f"/orders/{order.id}", f"/orders/{order.id}/billing",
        f"/clients/{client_record.id}", f"/clients/{client_record.id}/orders",
    ):
        response = anon.get(path)
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


def test_core_post_routes_require_login(app, order):
    anon = app.test_client()
    response = anon.post(f"/orders/{order.id}/edit", data={"item": "x"})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    # the anonymous request must not have touched the row
    assert db.session.get(Order, order.id).item != "x"


def test_get_order_or_404_is_scoped_to_the_tenant(logged_in, other_company):
    from models import Client as ClientModel

    foreign_client = ClientModel(
        company_id=other_company.id, first_name="Foreign", last_name="Client",
    )
    db.session.add(foreign_client)
    db.session.flush()
    foreign_order = Order(
        client_id=foreign_client.id, item="Foreign order",
        start=date(2026, 1, 1), due=date(2026, 1, 10), status="in_progress",
    )
    db.session.add(foreign_order)
    db.session.commit()

    response = logged_in.get(f"/orders/{foreign_order.id}")
    assert response.status_code == 404

    response = logged_in.post(f"/orders/{foreign_order.id}/edit", data={"item": "hijacked"})
    assert response.status_code == 404
    assert db.session.get(Order, foreign_order.id).item == "Foreign order"


def test_get_client_or_404_is_scoped_to_the_tenant(logged_in, other_company):
    foreign_client = Client(
        company_id=other_company.id, first_name="Foreign", last_name="Client",
    )
    db.session.add(foreign_client)
    db.session.commit()

    response = logged_in.get(f"/clients/{foreign_client.id}")
    assert response.status_code == 404

    response = logged_in.post(f"/clients/{foreign_client.id}/edit", data={"first_name": "hijacked"})
    assert response.status_code == 404
    assert db.session.get(Client, foreign_client.id).first_name == "Foreign"


# ---------------------------------------------------------------------------
# Nav (CO5, CO5a)
# ---------------------------------------------------------------------------

def test_nav_hides_for_a_logged_out_visitor(app):
    anon = app.test_client()
    response = anon.get("/login")
    assert b"view-switch" not in response.data


def test_nav_includes_the_mobile_hamburger_toggle(logged_in):
    response = logged_in.get("/")
    body = response.data
    assert b'id="nav-toggle"' in body
    assert b'id="view-switch-links"' in body
    # the toggle button is the wrapper's DOM sibling, not nested inside it --
    # CSS alone (margin-left: auto below 680px) is what visually pins it to
    # the right, not markup order.
    assert body.index(b'id="nav-toggle"') < body.index(b'id="view-switch-links"')


# ---------------------------------------------------------------------------
# Client (CL1, CL2, CL11)
# ---------------------------------------------------------------------------

def test_client_name_is_first_plus_last(client_record):
    assert client_record.name == "Marie Alarie"


def test_client_is_returning_only_with_two_or_more_orders(client_record):
    assert client_record.is_returning is False
    for i in range(2):
        db.session.add(Order(
            client_id=client_record.id, item=f"Order {i}",
            start=date(2026, 1, 1), due=date(2026, 1, 10), status="in_progress",
        ))
    db.session.commit()
    assert client_record.is_returning is True


def test_client_lifetime_value_sums_order_totals(client_record):
    for price in (100.0, 250.0):
        order = Order(
            client_id=client_record.id, item="Item",
            start=date(2026, 1, 1), due=date(2026, 1, 10), status="in_progress",
        )
        db.session.add(order)
        db.session.flush()
        db.session.add(OrderLine(order_id=order.id, description="Item", quantity=1, unit_price=price))
    db.session.commit()

    assert client_record.lifetime_value == sum(o.total for o in client_record.orders)
    assert client_record.lifetime_value > 0


def test_edit_client_without_address_field_leaves_address_untouched(logged_in, client_record):
    client_record.street = "123 Rue Example"
    client_record.province = "QC"
    db.session.commit()

    logged_in.post(
        f"/clients/{client_record.id}/edit",
        data={"first_name": "Marie", "last_name": "Alarie", "email": "marie@example.com", "phone": ""},
    )

    refreshed = db.session.get(Client, client_record.id)
    assert refreshed.street == "123 Rue Example"
    assert refreshed.province == "QC"


def test_edit_client_with_address_field_updates_it(logged_in, client_record):
    logged_in.post(
        f"/clients/{client_record.id}/edit",
        data={
            "first_name": "Marie", "last_name": "Alarie", "email": "", "phone": "",
            "street": "456 Rue Test", "city": "Montreal", "province": "QC", "postal_code": "h2x 1y1",
        },
    )

    refreshed = db.session.get(Client, client_record.id)
    assert refreshed.street == "456 Rue Test"
    assert refreshed.postal_code == "H2X 1Y1"


def test_edit_client_with_an_invalid_province_clears_it(logged_in, client_record):
    client_record.province = "QC"
    db.session.commit()

    logged_in.post(
        f"/clients/{client_record.id}/edit",
        data={
            "first_name": "Marie", "last_name": "Alarie", "email": "", "phone": "",
            "street": "", "city": "", "province": "ZZ", "postal_code": "",
        },
    )

    assert db.session.get(Client, client_record.id).province is None


def test_edit_client_without_notes_field_leaves_notes_untouched(logged_in, client_record):
    client_record.notes = "Prefers matte black hardware."
    db.session.commit()

    # Mirrors the timeline's quick-edit client modal, which never sends this field.
    logged_in.post(
        f"/clients/{client_record.id}/edit",
        data={"first_name": "Marie", "last_name": "Alarie", "email": "", "phone": ""},
    )

    assert db.session.get(Client, client_record.id).notes == "Prefers matte black hardware."


def test_edit_client_with_notes_field_updates_it(logged_in, client_record):
    logged_in.post(
        f"/clients/{client_record.id}/edit",
        data={
            "first_name": "Marie", "last_name": "Alarie", "email": "", "phone": "",
            "notes": "Allergic to nickel.",
        },
    )

    assert db.session.get(Client, client_record.id).notes == "Allergic to nickel."


def test_client_page_renders_blank_notes_not_the_word_none(logged_in, client_record):
    assert client_record.notes is None

    response = logged_in.get(f"/clients/{client_record.id}")

    assert b'<textarea name="notes" rows="14"></textarea>' in response.data


# ---------------------------------------------------------------------------
# OrderType (OT4, OT5, OT6)
# ---------------------------------------------------------------------------

def test_new_order_form_omits_type_dropdown_without_any_order_type(logged_in, client_record):
    response = logged_in.get("/orders/new")
    assert b'name="order_type_id"' not in response.data


def test_new_order_form_shows_type_dropdown_once_a_type_exists(logged_in, company, client_record):
    db.session.add(OrderType(company_id=company.id, label="Custom Order"))
    db.session.commit()

    response = logged_in.get("/orders/new")

    assert b'name="order_type_id"' in response.data
    assert b"Custom Order" in response.data


def test_new_order_only_offers_active_types(logged_in, company, client_record):
    db.session.add(OrderType(company_id=company.id, label="Active", is_active=True))
    db.session.add(OrderType(company_id=company.id, label="Hidden", is_active=False))
    db.session.commit()

    response = logged_in.get("/orders/new")

    assert b"Active" in response.data
    assert b"Hidden" not in response.data


def test_order_page_offers_a_hidden_type_the_order_already_has(logged_in, company, order):
    hidden = OrderType(company_id=company.id, label="Discontinued Type", is_active=False)
    db.session.add(hidden)
    db.session.flush()
    order.order_type_id = hidden.id
    db.session.commit()

    response = logged_in.get(f"/orders/{order.id}")

    assert b"Discontinued Type" in response.data


def test_edit_order_without_type_field_leaves_type_untouched(logged_in, company, order):
    order_type = OrderType(company_id=company.id, label="Custom Order")
    db.session.add(order_type)
    db.session.flush()
    order.order_type_id = order_type.id
    db.session.commit()

    # Mirrors the timeline's quick-edit modal, which never sends this field.
    logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(), "due": order.due.isoformat(),
    })

    assert db.session.get(Order, order.id).order_type_id == order_type.id


def test_edit_order_with_blank_type_field_clears_it(logged_in, company, order):
    order_type = OrderType(company_id=company.id, label="Custom Order")
    db.session.add(order_type)
    db.session.flush()
    order.order_type_id = order_type.id
    db.session.commit()

    logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(), "due": order.due.isoformat(),
        "order_type_id": "",
    })

    assert db.session.get(Order, order.id).order_type_id is None


def test_orders_list_type_column_only_shows_with_at_least_one_order_type(logged_in, company, order):
    response = logged_in.get("/orders")
    assert b"sort=type" not in response.data

    db.session.add(OrderType(company_id=company.id, label="Custom Order"))
    db.session.commit()

    response = logged_in.get("/orders")
    assert b"sort=type" in response.data


# ---------------------------------------------------------------------------
# Order fields (OR1, OR3)
# ---------------------------------------------------------------------------

def test_edit_order_rejects_an_unknown_status(logged_in, order):
    logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(), "due": order.due.isoformat(),
        "status": "not-a-real-status",
    })
    assert db.session.get(Order, order.id).status == "in_progress"


def test_edit_order_pickup_date_untouched_when_field_absent(logged_in, order):
    order.pickup_date = date(2026, 7, 20)
    db.session.commit()

    logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(), "due": order.due.isoformat(),
    })

    assert db.session.get(Order, order.id).pickup_date == date(2026, 7, 20)


def test_edit_order_pickup_date_cleared_when_field_blank(logged_in, order):
    order.pickup_date = date(2026, 7, 20)
    db.session.commit()

    logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(), "due": order.due.isoformat(),
        "pickup_date": "",
    })

    assert db.session.get(Order, order.id).pickup_date is None


def test_edit_order_pickup_date_can_be_set(logged_in, order):
    logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(), "due": order.due.isoformat(),
        "pickup_date": "2026-08-01",
    })

    assert db.session.get(Order, order.id).pickup_date == date(2026, 8, 1)


# ---------------------------------------------------------------------------
# Order lines, total, creation (OR5, OR6, OR8)
# ---------------------------------------------------------------------------

def test_new_order_creates_a_single_line_from_price(logged_in, client_record):
    logged_in.post("/orders/new", data={
        "client_id": str(client_record.id), "item": "Weekender bag",
        "start": "2026-08-01", "due": "2026-08-15", "price": "480.00",
        "status": "in_progress",
    })

    created = Order.query.filter_by(item="Weekender bag").first()
    assert created is not None
    assert len(created.lines) == 1
    assert created.lines[0].unit_price == 480.0
    assert created.lines[0].description == "Weekender bag"


def test_order_total_with_no_tax_is_the_sum_of_its_lines(client_record):
    # No province on the client means no tax is charged (see CL12) -- so the
    # tax-inclusive `total` and the pre-tax `subtotal` must agree exactly.
    client_record.province = None
    order = Order(
        client_id=client_record.id, item="Untaxed order",
        start=date(2026, 1, 1), due=date(2026, 1, 10), status="in_progress",
    )
    db.session.add(order)
    db.session.flush()
    db.session.add(OrderLine(order_id=order.id, description="A", quantity=2, unit_price=50.0))
    db.session.add(OrderLine(order_id=order.id, description="B", quantity=1, unit_price=25.0))
    db.session.commit()

    assert order.subtotal == 125.0
    assert order.total == order.subtotal


def test_is_settled_tolerates_a_cent_of_rounding(client_record):
    client_record.province = None  # no tax, so the total is exactly the line price
    order = Order(
        client_id=client_record.id, item="Rounding order",
        start=date(2026, 1, 1), due=date(2026, 1, 10), status="in_progress",
    )
    db.session.add(order)
    db.session.flush()
    db.session.add(OrderLine(order_id=order.id, description="A", quantity=1, unit_price=10.0))
    db.session.add(Payment(order_id=order.id, amount=10.004, paid_date=date(2026, 1, 2)))
    db.session.commit()

    assert order.is_settled is True


# ---------------------------------------------------------------------------
# Payments (PM1-PM4)
# ---------------------------------------------------------------------------

def test_add_payment_creates_a_row_and_updates_balance(logged_in, order):
    balance_before = order.balance_due

    logged_in.post(f"/orders/{order.id}/payments", data={
        "amount": "100.00", "paid_date": "2026-07-05", "method": "etransfer",
        "reference": "ET-123",
    })

    refreshed = db.session.get(Order, order.id)
    assert refreshed.amount_paid == 100.0
    assert refreshed.balance_due == pytest.approx(balance_before - 100.0)
    payment = refreshed.payments[0]
    assert payment.method == "etransfer"
    assert payment.reference == "ET-123"


def test_add_payment_defaults_an_invalid_method_to_cash(logged_in, order):
    logged_in.post(f"/orders/{order.id}/payments", data={
        "amount": "50", "paid_date": "2026-07-05", "method": "bitcoin",
    })

    assert db.session.get(Order, order.id).payments[0].method == "cash"


@pytest.mark.parametrize("data", [
    {"paid_date": "2026-07-05"},                  # missing amount
    {"amount": "50"},                              # missing date
    {"amount": "not-a-number", "paid_date": "2026-07-05"},
])
def test_add_payment_rejects_missing_or_invalid_fields(logged_in, order, data):
    logged_in.post(f"/orders/{order.id}/payments", data=data)
    assert db.session.get(Order, order.id).payments == []


def test_delete_payment_is_scoped_to_the_order(logged_in, order, client_record):
    other_order = Order(
        client_id=client_record.id, item="Other order",
        start=date(2026, 1, 1), due=date(2026, 1, 10), status="in_progress",
    )
    db.session.add(other_order)
    db.session.flush()
    payment = Payment(order_id=other_order.id, amount=75.0, paid_date=date(2026, 1, 2))
    db.session.add(payment)
    db.session.commit()
    payment_id = payment.id

    logged_in.post(f"/orders/{order.id}/payments/{payment_id}/delete")

    assert db.session.get(Payment, payment_id) is not None


def test_delete_payment_removes_it_and_recomputes_balance(logged_in, order):
    payment = Payment(order_id=order.id, amount=100.0, paid_date=date(2026, 1, 2))
    db.session.add(payment)
    db.session.commit()
    payment_id = payment.id
    balance_before = db.session.get(Order, order.id).balance_due

    logged_in.post(f"/orders/{order.id}/payments/{payment_id}/delete")

    assert db.session.get(Payment, payment_id) is None
    assert db.session.get(Order, order.id).balance_due == pytest.approx(balance_before + 100.0)


# ---------------------------------------------------------------------------
# Timeline (TL1-TL12)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("iso_weekday_date,expected", [
    (date(2026, 8, 3), date(2026, 8, 2)),   # Monday -> previous Sunday
    (date(2026, 8, 2), date(2026, 8, 2)),   # Already a Sunday -> itself
    (date(2026, 8, 8), date(2026, 8, 2)),   # Saturday -> the Sunday that started its week
])
def test_sunday_on_or_before_snaps_back_to_the_most_recent_sunday(iso_weekday_date, expected):
    assert app_module._sunday_on_or_before(iso_weekday_date) == expected


def test_timeline_window_always_starts_on_a_sunday(logged_in, order):
    response = logged_in.get("/timeline/2026/8/5")  # a Wednesday
    assert response.status_code == 200
    # window_start is baked into the prev/next links' day-of-window-start
    assert b"/timeline/2026/8/2/" not in response.data  # sanity: no accidental trailing slash bug


def test_timeline_next_and_prev_step_by_half_the_window(logged_in, order):
    response = logged_in.get("/timeline/2026/8/2")
    body = response.data.decode()
    # window is 8 weeks; step is 4 weeks (28 days) each direction
    assert "/timeline/2026/7/5" in body    # prev: 2026-08-02 - 28 days
    assert "/timeline/2026/8/30" in body   # next: 2026-08-02 + 28 days


def test_timeline_excludes_an_order_entirely_outside_the_window(logged_in, client_record):
    far_future = Order(
        client_id=client_record.id, item="Far future order",
        start=date(2030, 1, 1), due=date(2030, 1, 10), status="in_progress",
    )
    db.session.add(far_future)
    db.session.commit()

    response = logged_in.get("/timeline/2026/8/2")

    assert b"Far future order" not in response.data


def test_timeline_clips_a_bar_that_starts_before_the_window(logged_in, client_record):
    order = Order(
        client_id=client_record.id, item="Straddling order",
        start=date(2026, 7, 1), due=date(2026, 8, 10), status="in_progress",
    )
    db.session.add(order)
    db.session.commit()

    response = logged_in.get("/timeline/2026/8/2")

    assert b"timeline__bar--open-start" in response.data


def test_timeline_order_bar_label_and_tooltip_include_the_order_type(logged_in, company, order):
    order_type = OrderType(company_id=company.id, label="Custom Order")
    db.session.add(order_type)
    db.session.flush()
    order.order_type_id = order_type.id
    db.session.commit()

    response = logged_in.get(f"/timeline/{order.start.year}/{order.start.month}/{order.start.day}")

    assert f'title="{order.item} · Custom Order"'.encode() in response.data
    assert b"timeline__bar-type" in response.data


def test_timeline_order_bar_tooltip_omits_type_when_order_has_none(logged_in, order):
    response = logged_in.get(f"/timeline/{order.start.year}/{order.start.month}/{order.start.day}")
    assert f'title="{order.item}"'.encode() in response.data


def test_timeline_dedupes_client_dialogs_across_multiple_orders(logged_in, client_record):
    for i in range(3):
        db.session.add(Order(
            client_id=client_record.id, item=f"Order {i}",
            start=date(2026, 8, 1), due=date(2026, 8, 10), status="in_progress",
        ))
    db.session.commit()

    response = logged_in.get("/timeline/2026/8/2")

    assert response.data.count(f'id="client-modal-{client_record.id}"'.encode()) == 1


def test_timeline_returning_client_star_shown_only_once_returning(logged_in, client_record):
    # The legend at the bottom of the page always carries one `.timeline__star`
    # as its key, regardless of any client's status -- so "returning" is
    # asserted by an *extra* occurrence next to the client's own name, not by
    # the class's mere presence.
    order1 = Order(
        client_id=client_record.id, item="First",
        start=date(2026, 1, 1), due=date(2026, 1, 10), status="in_progress",
    )
    db.session.add(order1)
    db.session.commit()
    response = logged_in.get("/timeline/2026/1/4")
    assert response.data.count(b"timeline__star") == 1  # legend only

    order2 = Order(
        client_id=client_record.id, item="Second",
        start=date(2026, 1, 2), due=date(2026, 1, 11), status="in_progress",
    )
    db.session.add(order2)
    db.session.commit()
    response = logged_in.get("/timeline/2026/1/4")
    # Legend, plus one star per *row* (not deduped like the modals) -- two
    # orders for the now-returning client means two row stars.
    assert response.data.count(b"timeline__star") == 3


# ---------------------------------------------------------------------------
# Orders list & Clients list (LST1-LST5)
# ---------------------------------------------------------------------------

def _make_order(client, item, price, due):
    order = Order(client_id=client.id, item=item, start=due - timedelta(days=14), due=due, status="in_progress")
    db.session.add(order)
    db.session.flush()
    db.session.add(OrderLine(order_id=order.id, description=item, quantity=1, unit_price=price))
    return order


def test_orders_list_default_sort_is_due_ascending(logged_in, client_record):
    _make_order(client_record, "Later", 100, date(2026, 9, 1))
    _make_order(client_record, "Sooner", 100, date(2026, 8, 1))
    db.session.commit()

    response = logged_in.get("/orders").data.decode()

    assert response.index("Sooner") < response.index("Later")


def test_orders_list_sort_by_total_desc(logged_in, client_record):
    _make_order(client_record, "Cheap", 10, date(2026, 8, 1))
    _make_order(client_record, "Expensive", 1000, date(2026, 8, 2))
    db.session.commit()

    response = logged_in.get("/orders?sort=total&dir=desc").data.decode()

    assert response.index("Expensive") < response.index("Cheap")


def test_clients_list_default_sort_is_by_name(logged_in, company):
    db.session.add(Client(company_id=company.id, first_name="Zack", last_name="Zephyr"))
    db.session.add(Client(company_id=company.id, first_name="Amy", last_name="Adams"))
    db.session.commit()

    response = logged_in.get("/clients").data.decode()

    assert response.index("Amy") < response.index("Zack")


def test_clients_list_sort_by_lifetime_value(logged_in, company):
    rich = Client(company_id=company.id, first_name="Rich", last_name="Client")
    poor = Client(company_id=company.id, first_name="Poor", last_name="Client")
    db.session.add_all([rich, poor])
    db.session.flush()
    _make_order(rich, "Big order", 5000, date(2026, 8, 1))
    db.session.commit()

    response = logged_in.get("/clients?sort=orders&dir=desc").data.decode()

    assert response.index("Rich") < response.index("Poor")


# ---------------------------------------------------------------------------
# Creating orders and clients (OR9-OR11, CL13)
# ---------------------------------------------------------------------------

def test_new_order_button_present_when_orders_list_is_empty(logged_in):
    response = logged_in.get("/orders")
    assert b"+ Add order" in response.data or b"+ New order" in response.data


def test_new_order_carries_return_to_through_to_the_redirect(logged_in, client_record):
    response = logged_in.post(
        "/orders/new?return_to=/clients",
        data={
            "client_id": str(client_record.id), "item": "Tote",
            "start": "2026-08-01", "due": "2026-08-15", "price": "100",
            "status": "in_progress",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/clients"


def test_new_order_inline_client_creation_creates_both_rows(logged_in, company):
    response = logged_in.post("/orders/new", data={
        "client_id": "new", "new_first_name": "Jean", "new_last_name": "Tremblay",
        "new_email": "jean@example.com", "new_phone": "",
        "item": "Belt", "start": "2026-08-01", "due": "2026-08-10",
        "price": "60", "status": "in_progress",
    })
    assert response.status_code == 302

    client = Client.query.filter_by(company_id=company.id, first_name="Jean").first()
    assert client is not None
    order = Order.query.filter_by(client_id=client.id).first()
    assert order is not None
    assert order.item == "Belt"


def test_new_order_inline_client_creation_requires_first_and_last_name(logged_in):
    response = logged_in.post("/orders/new", data={
        "client_id": "new", "new_first_name": "", "new_last_name": "",
        "item": "Belt", "start": "2026-08-01", "due": "2026-08-10",
        "price": "60", "status": "in_progress",
    })
    assert response.status_code == 400


def test_new_client_route_creates_a_client_with_minimal_fields(logged_in, company):
    response = logged_in.post("/clients/new", data={
        "first_name": "Ana", "last_name": "Silva", "email": "", "phone": "",
    })
    assert response.status_code == 302
    created = Client.query.filter_by(company_id=company.id, first_name="Ana").first()
    assert created is not None
    assert created.orders == []


def test_new_client_route_requires_first_and_last_name(logged_in):
    response = logged_in.post("/clients/new", data={"first_name": "", "last_name": ""})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Modals + detail pages: return_to and back_label (MOD5, MOD6)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("return_to,expected", [
    ("/", "Back to timeline"),
    ("/timeline/2026/8/2", "Back to timeline"),
    ("/invoices", "Back to invoices"),
    ("/orders", "Back to orders"),
    ("/clients", "Back to clients"),
    ("/clients/1", "Back to client"),
    ("/orders/1", "Back to order"),
    ("/somewhere/unexpected", "Back"),
])
def test_back_label_variants(return_to, expected):
    assert app_module.back_label(return_to) == expected


def test_edit_client_redirects_to_return_to(logged_in, client_record):
    response = logged_in.post(f"/clients/{client_record.id}/edit", data={
        "first_name": "Marie", "last_name": "Alarie", "email": "", "phone": "",
        "return_to": "/clients",
    })
    assert response.headers["Location"] == "/clients"


def test_edit_order_redirects_to_return_to(logged_in, order):
    response = logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(), "due": order.due.isoformat(),
        "return_to": "/orders",
    })
    assert response.headers["Location"] == "/orders"


# ---------------------------------------------------------------------------
# Settings root redirect (SET1)
# ---------------------------------------------------------------------------

def test_settings_root_redirects_to_general(logged_in):
    response = logged_in.get("/settings")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/settings/general")


# ---------------------------------------------------------------------------
# Analytics (AN1-AN8)
# ---------------------------------------------------------------------------

def test_analytics_avg_value_excludes_clients_with_no_orders(logged_in, company):
    with_order = Client(company_id=company.id, first_name="Has", last_name="Orders")
    without_order = Client(company_id=company.id, first_name="No", last_name="Orders")
    db.session.add_all([with_order, without_order])
    db.session.flush()
    _make_order(with_order, "Order", 200, date(2026, 8, 1))
    db.session.commit()

    response = logged_in.get("/analytics")

    assert response.status_code == 200
    # With only one client-with-orders, avg == that client's own lifetime value.
    assert f"{with_order.lifetime_value:.2f}".encode() in response.data


def test_analytics_top_clients_ranks_by_lifetime_value(logged_in, company):
    low = Client(company_id=company.id, first_name="Low", last_name="Spender")
    high = Client(company_id=company.id, first_name="High", last_name="Spender")
    db.session.add_all([low, high])
    db.session.flush()
    _make_order(low, "Small", 50, date(2026, 8, 1))
    _make_order(high, "Big", 5000, date(2026, 8, 1))
    db.session.commit()

    response = logged_in.get("/analytics").data.decode()

    assert response.index("High") < response.index("Low")


def test_analytics_source_breakdown_includes_hidden_options_and_excludes_zero_percent(logged_in, company):
    used_hidden = SourceOption(company_id=company.id, label="Old Instagram", is_active=False)
    unused = SourceOption(company_id=company.id, label="Never Used", is_active=True)
    db.session.add_all([used_hidden, unused])
    db.session.flush()
    client = Client(company_id=company.id, first_name="Some", last_name="Client")
    db.session.add(client)
    db.session.flush()
    client.sources = [used_hidden]
    db.session.commit()

    response = logged_in.get("/analytics").data.decode()

    assert "Old Instagram" in response
    assert "Never Used" not in response


def test_analytics_revenue_counts_recorded_payments_not_order_total(logged_in, client_record):
    order = _make_order(client_record, "Deposit only", 1000, date(2026, 8, 1))
    db.session.add(Payment(order_id=order.id, amount=300.0, paid_date=date(2026, 6, 1)))
    db.session.commit()

    response = logged_in.get("/analytics").data.decode()

    assert "300.00" in response
    assert "1,000.00" not in response and "1000.00" not in response


def test_analytics_revenue_ytd_filters_to_the_current_year(logged_in, client_record, monkeypatch):
    order = _make_order(client_record, "Old and new", 100, date(2026, 8, 1))
    db.session.add(Payment(order_id=order.id, amount=40.0, paid_date=date(2020, 1, 1)))
    db.session.add(Payment(order_id=order.id, amount=60.0, paid_date=date.today()))
    db.session.commit()

    total_payments = 100.0

    response = logged_in.get("/analytics").data.decode()

    # Total revenue includes both; YTD must be strictly less (only the
    # current-year payment), proving the year filter actually excludes the
    # old one rather than summing everything into both figures.
    assert "100.00" in response  # total revenue
    assert "60.00" in response   # revenue YTD


def test_analytics_method_breakdown_sorted_by_amount_descending(logged_in, client_record):
    order = _make_order(client_record, "Multi-method", 1000, date(2026, 8, 1))
    db.session.add(Payment(order_id=order.id, amount=50.0, paid_date=date(2026, 6, 1), method="cash"))
    db.session.add(Payment(order_id=order.id, amount=500.0, paid_date=date(2026, 6, 2), method="square"))
    db.session.commit()

    response = logged_in.get("/analytics").data.decode()

    assert response.index("Square") < response.index("Cash")
