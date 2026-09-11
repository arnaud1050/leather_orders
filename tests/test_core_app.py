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
import math
import re
from datetime import date, timedelta

import pytest

import app as app_module
from models import Client, Company, Order, OrderLine, OrderType, Payment, SourceOption, db

from billing.services import invoicing
from billing_adapter import billable_for

# ---------------------------------------------------------------------------
# Tenancy & auth (CO2-CO6)
# ---------------------------------------------------------------------------

CORE_GET_ROUTES = [
    "/", "/orders", "/clients", "/analytics", "/orders/new", "/clients/new",
    "/help/orders", "/help/clients",
]


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
        start=date(2026, 1, 1), due=date(2026, 1, 10), status="confirmed",
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
# Help pages (OR1i, CL22)
# ---------------------------------------------------------------------------

def test_order_lifecycle_help_renders_for_a_logged_in_user(logged_in):
    response = logged_in.get("/help/orders")
    assert response.status_code == 200
    assert b"The life of an order" in response.data


def test_client_lifecycle_help_renders_for_a_logged_in_user(logged_in):
    response = logged_in.get("/help/clients")
    assert response.status_code == 200
    assert b"The life of a client" in response.data


def test_footer_links_to_both_lifecycle_guides_for_a_tenant_user(logged_in):
    response = logged_in.get("/")
    assert b'href="/help/orders"' in response.data
    assert b'href="/help/clients"' in response.data


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
            start=date(2026, 1, 1), due=date(2026, 1, 10), status="confirmed",
        ))
    db.session.commit()
    assert client_record.is_returning is True


def test_prior_order_count_counts_toward_is_returning(client_record):
    """CL2b — a longtime client shouldn't need a second order logged here to
    show as returning."""
    assert client_record.is_returning is False
    client_record.prior_order_count = 1
    db.session.commit()
    assert client_record.is_returning is False

    db.session.add(Order(
        client_id=client_record.id, item="Item",
        start=date(2026, 1, 1), due=date(2026, 1, 10), status="confirmed",
    ))
    db.session.commit()
    assert client_record.is_returning is True


def test_prior_order_count_is_not_cleared_by_adding_and_removing_an_order(client_record):
    """CL2b — the manual count lives in its own column, so it can't be
    undone by unrelated changes to the orders table: adding then deleting a
    second real order leaves it, and is_returning, exactly where they were."""
    client_record.prior_order_count = 1
    kept = Order(
        client_id=client_record.id, item="Kept",
        start=date(2026, 1, 1), due=date(2026, 1, 10), status="confirmed",
    )
    db.session.add(kept)
    db.session.commit()
    assert client_record.is_returning is True  # 1 prior + 1 real

    added = Order(
        client_id=client_record.id, item="Added",
        start=date(2026, 1, 1), due=date(2026, 1, 10), status="tentative",
    )
    db.session.add(added)
    db.session.commit()
    assert client_record.is_returning is True  # 1 prior + 2 real

    db.session.delete(added)
    db.session.commit()
    assert client_record.prior_order_count == 1
    assert client_record.is_returning is True  # back to 1 prior + 1 real, still returning


def test_prior_order_count_never_reaches_lifetime_value(client_record):
    """CL2a/CL2b — the manual count is a count, not money.

    There is no dollar figure behind "this client had 5 orders before we
    started using the app", so it must not move `lifetime_value` (which
    ranks Analytics' top-5 and the timeline's highest-paying sort). The
    temptation to estimate one is exactly what this asserts against.
    """
    client_record.prior_order_count = 5
    db.session.commit()

    assert client_record.is_returning is True
    assert client_record.lifetime_value == 0


def test_a_prior_only_client_is_returning_while_still_having_no_orders(logged_in, client_record):
    """CL2b x LST5 — the two facts are independent and both render.

    A client whose only history is the manual count sits in the *No orders*
    filter group (they genuinely have none in the app) while still counting
    toward the Returning stat card. Asserting both off the same row is the
    point: they're driven by different properties and a change to either
    one could quietly start answering the other's question.
    """
    client_record.prior_order_count = 3
    db.session.commit()

    body = logged_in.get("/clients").data.decode()

    row = re.search(r'<tr data-group="(\w+)" data-returning="(\d)"', body)
    assert row is not None, "no client row rendered"
    assert row.group(1) == "none"       # no real orders -> the "No orders" group
    assert row.group(2) == "1"          # ...but still returning
    assert re.search(r'id="client-returning-count">(\d+)<', body).group(1) == "1"


def test_timeline_star_counts_prior_orders_too(logged_in, client_record):
    """TL6 x CL2b — the star reads `is_returning`, so the manual count earns
    it without waiting for a second order to be logged here.

    Same counting trick as `test_timeline_returning_client_star_shown_only
    _once_returning`: the legend always carries one star of its own, so the
    client's is the *second* occurrence.
    """
    db.session.add(Order(
        client_id=client_record.id, item="Only order in the app",
        start=date(2026, 1, 1), due=date(2026, 1, 10), status="confirmed",
    ))
    db.session.commit()
    assert logged_in.get("/timeline/2026/1/4").data.count(b"timeline__star") == 1

    client_record.prior_order_count = 1
    db.session.commit()

    assert logged_in.get("/timeline/2026/1/4").data.count(b"timeline__star") == 2


def test_client_lifetime_value_sums_order_totals(client_record):
    for price in (100.0, 250.0):
        order = Order(
            client_id=client_record.id, item="Item",
            start=date(2026, 1, 1), due=date(2026, 1, 10), status="confirmed",
        )
        db.session.add(order)
        db.session.flush()
        db.session.add(OrderLine(order_id=order.id, description="Item", quantity=1, unit_price=price))
    db.session.commit()

    assert client_record.lifetime_value == sum(o.total for o in client_record.orders)
    assert client_record.lifetime_value > 0


def test_client_lifetime_value_skips_cancelled_orders(client_record):
    """CL2a — a commission that was called off was never business done, and
    letting it count would inflate the analytics top-client ranking."""
    kept, called_off = None, None
    for price, status in ((100.0, "confirmed"), (250.0, "cancelled")):
        order = Order(
            client_id=client_record.id, item="Item",
            start=date(2026, 1, 1), due=date(2026, 1, 10), status=status,
        )
        db.session.add(order)
        db.session.flush()
        db.session.add(OrderLine(
            order_id=order.id, description="Item", quantity=1, unit_price=price))
        if status == "cancelled":
            called_off = order
        else:
            kept = order
    db.session.commit()

    assert client_record.lifetime_value == kept.total
    assert called_off.total > 0  # the cancelled order is still worth something
    assert client_record.lifetime_value < sum(o.total for o in client_record.orders)


def test_a_client_whose_only_order_was_cancelled_is_worth_nothing(client_record):
    order = Order(
        client_id=client_record.id, item="Item",
        start=date(2026, 1, 1), due=date(2026, 1, 10), status="cancelled",
    )
    db.session.add(order)
    db.session.flush()
    db.session.add(OrderLine(
        order_id=order.id, description="Item", quantity=1, unit_price=400.0))
    db.session.commit()

    assert client_record.lifetime_value == 0
    # Still a client with an order on file, though — is_returning and the
    # orders list both deliberately keep counting it (CL2a).
    assert len(client_record.orders) == 1


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


def test_edit_client_without_prior_order_count_field_leaves_it_untouched(logged_in, client_record):
    client_record.prior_order_count = 3
    db.session.commit()

    logged_in.post(
        f"/clients/{client_record.id}/edit",
        data={"first_name": "Marie", "last_name": "Alarie", "email": "", "phone": ""},
    )

    assert db.session.get(Client, client_record.id).prior_order_count == 3


def test_edit_client_rejects_a_non_numeric_prior_order_count(logged_in, client_record):
    logged_in.post(
        f"/clients/{client_record.id}/edit",
        data={
            "first_name": "Marie", "last_name": "Alarie", "email": "", "phone": "",
            "prior_order_count": "-1",
        },
    )

    assert db.session.get(Client, client_record.id).prior_order_count == 0


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
    response = logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(), "due": order.due.isoformat(),
        "status": "not-a-real-status",
    })
    assert response.status_code == 400
    assert db.session.get(Order, order.id).status == "confirmed"


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
        "status": "confirmed",
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
        start=date(2026, 1, 1), due=date(2026, 1, 10), status="confirmed",
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
        start=date(2026, 1, 1), due=date(2026, 1, 10), status="confirmed",
    )
    db.session.add(order)
    db.session.flush()
    db.session.add(OrderLine(order_id=order.id, description="A", quantity=1, unit_price=10.0))
    db.session.add(Payment(order_id=order.id, amount=10.004, paid_date=date(2026, 1, 2)))
    db.session.commit()

    assert order.is_settled is True


# ---------------------------------------------------------------------------
# Input that isn't a number, a date, or a name (OR2a, OR2b, OR6a, OR12)
#
# Everything below reached the user as a raw 500 or as silently wrong data
# before these rules existed. The app registers no error handler, so an
# unhandled exception here is a traceback page, not a message.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    {"start": "not-a-date", "due": "2026-08-15"},
    {"start": "2026-08-01", "due": "15/08/2026"},
    {"due": "2026-08-15"},                          # start missing entirely
    {"start": "2026-08-01"},                        # due missing entirely
])
def test_new_order_rejects_a_date_it_cannot_read(logged_in, client_record, payload):
    """OR2b — `date.fromisoformat` raises rather than answering, and a
    missing field raises `TypeError` rather than `ValueError`. Both used to
    surface as a 500."""
    response = logged_in.post("/orders/new", data={
        "client_id": str(client_record.id), "item": "Bad dates",
        "price": "100.00", "status": "confirmed", **payload,
    })

    assert response.status_code == 400
    assert Order.query.filter_by(item="Bad dates").first() is None


def test_new_order_rejects_a_due_date_before_the_start(logged_in, client_record):
    """OR2a — reachable straight from the UI: the two date pickers have no
    relationship to each other, so nothing but the server catches it."""
    response = logged_in.post("/orders/new", data={
        "client_id": str(client_record.id), "item": "Backwards",
        "start": "2026-08-20", "due": "2026-08-01",
        "price": "100.00", "status": "confirmed",
    })

    assert response.status_code == 400
    assert Order.query.filter_by(item="Backwards").first() is None


def test_new_order_allows_a_single_day_order(logged_in, client_record):
    """OR2a is `due < start`, not `due <= start` — a job that starts and
    finishes on one day is ordinary, and the boundary is where an
    off-by-one would hide."""
    logged_in.post("/orders/new", data={
        "client_id": str(client_record.id), "item": "Same day",
        "start": "2026-08-01", "due": "2026-08-01",
        "price": "100.00", "status": "confirmed",
    })

    assert Order.query.filter_by(item="Same day").first() is not None


def test_new_order_rejects_a_whitespace_only_item(logged_in, client_record):
    """OR12 — `required` accepts "   ", which strips to nothing and leaves
    an order with no name anywhere it renders."""
    response = logged_in.post("/orders/new", data={
        "client_id": str(client_record.id), "item": "   ",
        "start": "2026-08-01", "due": "2026-08-15",
        "price": "100.00", "status": "confirmed",
    })

    assert response.status_code == 400
    assert Order.query.filter_by(client_id=client_record.id).first() is None


def _window_showing(order):
    """A timeline URL whose window contains `order` — the path its quick-edit
    dialog posts as `return_to`, and so the one a refused save comes back to."""
    return f"/timeline/{order.start.year}/{order.start.month}/{order.start.day}"


def _order_dialog(body: str, order) -> str:
    match = re.search(rf'<dialog id="order-modal-{order.id}"[^>]*>.*?</dialog>', body, re.S)
    assert match is not None, f"no dialog rendered for order {order.id}"
    return match.group(0)


def test_edit_order_refuses_an_unreadable_date_but_ignores_an_absent_one(logged_in, order):
    """OR2b x hard rule 9 — the two cases must not be confused: a field the
    form never rendered is left alone, a field it rendered as nonsense is
    refused — and refusing means *nothing* is written, not even the item
    that was fine."""
    original_start = order.start

    response = logged_in.post(f"/orders/{order.id}/edit", data={
        "item": "Still fine", "start": "nonsense", "due": order.due.isoformat(),
    })
    assert response.status_code == 302  # back to the window, message stashed (OR13)
    db.session.expire_all()
    refused = db.session.get(Order, order.id)
    assert refused.start == original_start
    assert refused.item == "Full-grain briefcase"

    # No date fields at all: a partial form, which must save happily.
    assert logged_in.post(f"/orders/{order.id}/edit", data={
        "item": "Renamed",
    }).status_code == 302
    db.session.expire_all()
    reloaded = db.session.get(Order, order.id)
    assert reloaded.item == "Renamed"
    assert reloaded.start == original_start


def test_edit_order_refuses_moving_the_start_past_the_existing_due_date(logged_in, order):
    """OR2a — the check is on the *resulting* pair. A form that sends only a
    new start can still produce a backwards order, and comparing the two
    submitted fields against each other would miss it entirely."""
    window = _window_showing(order)
    too_late = order.due + timedelta(days=1)

    logged_in.post(f"/orders/{order.id}/edit", data={
        "return_to": window, "start": too_late.isoformat(),
    })

    db.session.expire_all()
    assert db.session.get(Order, order.id).start != too_late
    assert "is before the start date" in _order_dialog(logged_in.get(window).data.decode(), order)


# ---------------------------------------------------------------------------
# Refused saves explain themselves (OR13)
#
# Three surfaces, two mechanisms. The new-order form and the order page save
# to their own URL and re-render in place with a message under each bad
# field. The timeline modal can't — every link on the timeline is built from
# request.path — so it redirects back and reopens the dialog. Either way
# the rule is the same: a sentence under the field at fault, everything
# typed still there, and nothing written.
# ---------------------------------------------------------------------------

def test_a_refused_quick_edit_reopens_its_dialog_with_the_message_and_what_was_typed(logged_in, order):
    window = _window_showing(order)
    backwards_due = (order.start - timedelta(days=3)).isoformat()

    response = logged_in.post(f"/orders/{order.id}/edit", data={
        "return_to": window, "item": "Retyped name",
        "start": order.start.isoformat(), "due": backwards_due, "status": order.status,
    })

    assert response.status_code == 302
    assert response.headers["Location"].endswith(window)
    db.session.expire_all()
    assert db.session.get(Order, order.id).item == "Full-grain briefcase"

    dialog = _order_dialog(logged_in.get(window).data.decode(), order)
    opening_tag = dialog.split(">", 1)[0]
    assert "data-open-on-load" in opening_tag
    assert "is before the start date" in dialog
    assert 'value="Retyped name"' in dialog
    assert f'value="{backwards_due}"' in dialog
    assert 'aria-invalid="true"' in dialog


def test_a_refused_quick_edit_is_not_replayed_on_a_later_visit(logged_in, order):
    """The stash is one-shot and window-bound. A refusal whose return_to
    never came back to its own window must not lie in wait and reopen an
    old dialog the next time somebody opens the timeline."""
    window = _window_showing(order)
    logged_in.post(f"/orders/{order.id}/edit", data={
        "return_to": window, "item": "",
    })

    # Looked for on a <dialog> tag, not as a bare substring: the page's own
    # script names the attribute on every single render.
    reopened = re.compile(r"<dialog[^>]*\sdata-open-on-load")
    assert not reopened.search(logged_in.get("/timeline/2026/1/4").data.decode())
    assert not reopened.search(logged_in.get(window).data.decode())


def test_a_quick_edit_with_a_blank_item_is_refused_not_silently_kept(logged_in, order):
    """OR12 — this used to be `strip() or order.item`: the save appeared to
    work and the old name quietly stayed, with nothing on screen saying
    the change had been thrown away."""
    window = _window_showing(order)

    logged_in.post(f"/orders/{order.id}/edit", data={
        "return_to": window, "item": "   ",
        "start": order.start.isoformat(), "due": order.due.isoformat(),
    })

    db.session.expire_all()
    assert db.session.get(Order, order.id).item == "Full-grain briefcase"
    assert "or only spaces" in _order_dialog(logged_in.get(window).data.decode(), order)


def test_new_order_explains_a_backwards_due_date_and_keeps_the_form(logged_in, client_record):
    response = logged_in.post("/orders/new", data={
        "client_id": str(client_record.id), "item": "Weekender",
        "start": "2026-08-20", "due": "2026-08-01",
        "price": "480.00", "status": "confirmed", "notes": "Brass hardware",
    })

    assert response.status_code == 400
    body = response.data.decode()
    assert "The due date (Aug 1, 2026) is before the start date (Aug 20, 2026)." in body
    assert 'aria-invalid="true"' in body
    # Everything typed comes back.
    assert 'value="Weekender"' in body
    assert 'value="2026-08-20"' in body
    assert "Brass hardware" in body
    assert f'<option value="{client_record.id}" selected>' in body
    assert '<option value="confirmed" selected>' in body
    assert Order.query.count() == 0


def test_new_order_explains_a_name_that_is_only_spaces(logged_in, client_record):
    response = logged_in.post("/orders/new", data={
        "client_id": str(client_record.id), "item": "   ",
        "start": "2026-08-01", "due": "2026-08-15", "price": "100.00",
    })

    assert response.status_code == 400
    body = response.data.decode()
    assert "or only spaces" in body
    # One message, for the one field at fault.
    assert body.count('class="field-error"') == 1


def test_new_order_with_blank_new_client_names_explains_both_and_creates_nobody(logged_in, company):
    """OR11 x OR13 — the client is only built once every field has passed,
    so a refusal can't leave a nameless Client row behind in the session for
    a later commit to write."""
    clients_before = Client.query.count()

    response = logged_in.post("/orders/new", data={
        "client_id": "new", "new_first_name": "  ", "new_last_name": "",
        "new_email": "ana@example.com",
        "item": "Belt", "start": "2026-08-01", "due": "2026-08-10",
        "price": "60", "status": "confirmed",
    })

    assert response.status_code == 400
    body = response.data.decode()
    assert "first name — it" in body
    assert "last name — it" in body
    assert '<option value="new" selected>' in body
    assert 'value="ana@example.com"' in body
    assert Client.query.count() == clients_before
    assert Order.query.count() == 0


def test_new_order_strips_surrounding_spaces_before_saving(logged_in):
    logged_in.post("/orders/new", data={
        "client_id": "new", "new_first_name": "  Ana ", "new_last_name": " Silva  ",
        "new_email": " ana@example.com ",
        "item": "  Card holder  ", "start": "2026-08-01", "due": "2026-08-10",
        "price": "60", "status": "confirmed", "notes": "  Black edge paint  ",
    })

    created = Order.query.one()
    assert created.item == "Card holder"
    assert created.lines[0].description == "Card holder"
    assert created.notes == "Black edge paint"
    assert (created.client.first_name, created.client.last_name, created.client.email) == (
        "Ana", "Silva", "ana@example.com",
    )


def test_order_page_save_redisplays_a_date_error_in_place_and_keeps_the_notes(logged_in, order):
    """The reason the order page saves to its own URL: it can re-render the
    form with the notes someone was halfway through writing still in the
    box. A redirect would have had to carry them through a ~4KB cookie."""
    response = logged_in.post(f"/orders/{order.id}", data={
        "return_to": "/orders", "item": order.item,
        "start": order.start.isoformat(),
        "due": (order.start - timedelta(days=1)).isoformat(),
        "status": order.status,
        "notes": "A long note the person was halfway through writing",
    })

    assert response.status_code == 400
    body = response.data.decode()
    assert "is before the start date" in body
    assert "A long note the person was halfway through writing" in body
    db.session.expire_all()
    unchanged = db.session.get(Order, order.id)
    assert unchanged.due == date(2026, 7, 15)
    assert not unchanged.notes


def test_order_page_save_explains_a_blank_item_and_keeps_the_stored_name(logged_in, order):
    response = logged_in.post(f"/orders/{order.id}", data={
        "return_to": "/orders", "item": "   ",
        "start": order.start.isoformat(), "due": order.due.isoformat(),
    })

    assert response.status_code == 400
    body = response.data.decode()
    assert "or only spaces" in body
    assert "<h1>Full-grain briefcase</h1>" in body
    db.session.expire_all()
    assert db.session.get(Order, order.id).item == "Full-grain briefcase"


def test_order_page_save_strips_and_returns_to_where_it_came_from(logged_in, order):
    response = logged_in.post(f"/orders/{order.id}", data={
        "return_to": "/orders", "item": "  Weekender  ",
        "start": order.start.isoformat(), "due": order.due.isoformat(),
        "status": order.status, "notes": "  Tan thread  ",
    })

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/orders")
    db.session.expire_all()
    saved = db.session.get(Order, order.id)
    assert saved.item == "Weekender"
    assert saved.notes == "Tan thread"


def test_order_page_save_still_refuses_an_illegal_status_move_with_a_400(logged_in, order):
    """OR1a on the new route — both surfaces write through the same helper,
    and this is the one refusal that stays a 400: the dropdown only offers
    legal moves, so an illegal one didn't come from someone using it."""
    response = logged_in.post(f"/orders/{order.id}", data={
        "item": order.item, "start": order.start.isoformat(),
        "due": order.due.isoformat(), "status": "tentative",
    })

    assert response.status_code == 400
    db.session.expire_all()
    assert db.session.get(Order, order.id).status == "confirmed"


def test_order_page_save_requires_login(app, order):
    response = app.test_client().post(f"/orders/{order.id}", data={"item": "x"})

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert db.session.get(Order, order.id).item != "x"


def test_order_page_renders_empty_notes_not_the_word_none(logged_in, order):
    """Same trap as CL16, on the order page: an order made anywhere but the
    new-order form can carry notes=None, and a bare {{ order.notes }} put
    the word "None" in the box — which the next save then stored."""
    assert order.notes is None

    body = logged_in.get(f"/orders/{order.id}").data.decode()

    assert '<textarea name="notes" rows="14"></textarea>' in body


def test_edit_order_still_saves_an_order_that_was_already_backwards(logged_in, client_record):
    """OR2a — rows entered before the rule existed have to stay editable,
    or there'd be no way to correct them. The guard only runs when a date
    is actually submitted."""
    backwards = Order(
        client_id=client_record.id, item="Legacy backwards",
        start=date(2026, 8, 20), due=date(2026, 8, 1), status="confirmed",
    )
    db.session.add(backwards)
    db.session.commit()

    # An unrelated edit goes through...
    assert logged_in.post(f"/orders/{backwards.id}/edit", data={
        "item": "Legacy renamed",
    }).status_code == 302
    # ...and so does the edit that fixes the dates.
    assert logged_in.post(f"/orders/{backwards.id}/edit", data={
        "start": "2026-08-01", "due": "2026-08-20",
    }).status_code == 302
    db.session.expire_all()
    fixed = db.session.get(Order, backwards.id)
    assert fixed.start == date(2026, 8, 1)
    assert fixed.due == date(2026, 8, 20)


def test_a_backwards_order_still_renders_a_positive_span_on_the_timeline(logged_in, client_record):
    """OR2a — the defensive half. `span: -7` isn't a short bar, it's
    invalid CSS, and the row is still on file from before the rule."""
    db.session.add(Order(
        client_id=client_record.id, item="Legacy backwards",
        start=date(2026, 1, 20), due=date(2026, 1, 8), status="confirmed",
    ))
    db.session.commit()

    body = logged_in.get("/timeline/2026/1/4").data.decode()

    spans = [int(n) for n in re.findall(r"grid-column: -?\d+ / span (-?\d+)", body)]
    assert spans, "no timeline bar rendered"
    assert all(span >= 1 for span in spans)


@pytest.mark.parametrize("amount", ["inf", "-inf", "nan", "Infinity", "NaN"])
def test_a_payment_amount_that_is_not_a_finite_number_is_ignored(logged_in, order, amount):
    """OR6a — `float()` builds all of these without raising. A NaN then
    fails the column's NOT NULL constraint (SQLite stores it as NULL) with
    a 500; an infinity is accepted and quietly makes the order's arithmetic
    meaningless."""
    response = logged_in.post(f"/orders/{order.id}/payments", data={
        "amount": amount, "paid_date": "2026-07-02", "method": "cash",
    })

    assert response.status_code == 302
    db.session.expire_all()
    assert db.session.get(Order, order.id).payments == []


@pytest.mark.parametrize("price", ["inf", "nan"])
def test_a_line_price_that_is_not_a_finite_number_is_ignored(logged_in, order, price):
    """OR6a — the one that spreads: an infinite line price makes
    `Order.total` infinite, and from there `Client.lifetime_value`, the
    Analytics top-5 and every revenue figure."""
    before = len(order.lines)

    logged_in.post(f"/orders/{order.id}/lines", data={
        "description": "Poison", "quantity": "1", "unit_price": price,
    })

    db.session.expire_all()
    reloaded = db.session.get(Order, order.id)
    assert len(reloaded.lines) == before
    assert math.isfinite(reloaded.total)


def test_add_payment_ignores_a_date_it_cannot_read(logged_in, order):
    """OR2b — `add_payment` already ignored an unreadable amount; an
    unreadable date used to raise instead."""
    response = logged_in.post(f"/orders/{order.id}/payments", data={
        "amount": "10.00", "paid_date": "13/45/2026", "method": "cash",
    })

    assert response.status_code == 302
    db.session.expire_all()
    assert db.session.get(Order, order.id).payments == []


def test_an_out_of_range_prior_order_count_is_stored_as_zero(logged_in, client_record):
    """CL2b — `isdigit()` vouches for the shape, not the magnitude: 25 nines
    is all digits and overflows SQLite's INTEGER on the way in."""
    response = logged_in.post(f"/clients/{client_record.id}/edit", data={
        "first_name": "Marie", "last_name": "Alarie",
        "prior_order_count": "9" * 25,
    })

    assert response.status_code == 302
    db.session.expire_all()
    assert db.session.get(Client, client_record.id).prior_order_count == 0


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


def test_billing_tab_deletes_via_trash_icon_not_a_remove_button(logged_in, order):
    # Line items and Payments used to delete via a text "Remove" button
    # (.doc-list__delete); both now use the same icon-btn trash icon the
    # inventory module's Materials/Others rows use, per the app-wide
    # "trash icon or a 'Delete' button, nothing else" convention.
    logged_in.post(f"/orders/{order.id}/payments", data={
        "amount": "50", "paid_date": "2026-07-05", "method": "cash",
    })

    html = logged_in.get(f"/orders/{order.id}/billing").get_data(as_text=True)

    assert "doc-list__delete" not in html
    assert ">Remove<" not in html
    assert html.count('icon-btn icon-btn--danger') == 2  # one line item, one payment


def test_delete_payment_is_scoped_to_the_order(logged_in, order, client_record):
    other_order = Order(
        client_id=client_record.id, item="Other order",
        start=date(2026, 1, 1), due=date(2026, 1, 10), status="confirmed",
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


def test_billing_tab_forms_carry_their_own_page_as_return_to(logged_in, order):
    # The Billing tab's own return_to (used by its "Back to..." link) points
    # wherever the order page was opened from, e.g. the timeline — but a
    # mutation happening *inside* the tab (add/delete a payment or line item)
    # must redirect back to the Billing tab itself, not out to that outer
    # destination. Payments used to submit the outer return_to by mistake,
    # which bounced you off the page after every delete.
    payment = Payment(order_id=order.id, amount=25.0, paid_date=date(2026, 1, 2))
    db.session.add(payment)
    db.session.commit()

    billing_path = f"/orders/{order.id}/billing"
    html = logged_in.get(billing_path, query_string={"return_to": "/timeline/2026/1/1"}).get_data(as_text=True)

    assert "/payments" in html and "/lines" in html
    # Every hidden return_to in the Payments/Line items forms should be the
    # tab's own path (one delete-line, one add-line, one delete-payment, one
    # add-payment form), never the "/timeline/..." value passed in above.
    assert html.count(f'name="return_to" value="{billing_path}"') == 4


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
        start=date(2030, 1, 1), due=date(2030, 1, 10), status="confirmed",
    )
    db.session.add(far_future)
    db.session.commit()

    response = logged_in.get("/timeline/2026/8/2")

    assert b"Far future order" not in response.data


def test_timeline_clips_a_bar_that_starts_before_the_window(logged_in, client_record):
    order = Order(
        client_id=client_record.id, item="Straddling order",
        start=date(2026, 7, 1), due=date(2026, 8, 10), status="confirmed",
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
            start=date(2026, 8, 1), due=date(2026, 8, 10), status="confirmed",
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
        start=date(2026, 1, 1), due=date(2026, 1, 10), status="confirmed",
    )
    db.session.add(order1)
    db.session.commit()
    response = logged_in.get("/timeline/2026/1/4")
    assert response.data.count(b"timeline__star") == 1  # legend only

    order2 = Order(
        client_id=client_record.id, item="Second",
        start=date(2026, 1, 2), due=date(2026, 1, 11), status="confirmed",
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
    order = Order(client_id=client.id, item=item, start=due - timedelta(days=14), due=due, status="confirmed")
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
            "status": "confirmed",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/clients"


def test_new_order_inline_client_creation_creates_both_rows(logged_in, company):
    response = logged_in.post("/orders/new", data={
        "client_id": "new", "new_first_name": "Jean", "new_last_name": "Tremblay",
        "new_email": "jean@example.com", "new_phone": "",
        "item": "Belt", "start": "2026-08-01", "due": "2026-08-10",
        "price": "60", "status": "confirmed",
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
        "price": "60", "status": "confirmed",
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


def test_analytics_avg_value_ignores_a_client_with_only_prior_orders(logged_in, company):
    """AN2 x CL2b — "has at least one order" means one *in the app*.

    A client carrying only the manual prior-order count has no order and no
    money behind them, so letting them into the denominator would halve the
    average on the strength of a number somebody typed in. They're returning
    (CL2b) and still must not appear here — the two rules pull in opposite
    directions on the same row, which is why this is worth pinning.
    """
    with_order = Client(company_id=company.id, first_name="Has", last_name="Orders")
    prior_only = Client(
        company_id=company.id, first_name="Prior", last_name="Only",
        prior_order_count=4,
    )
    db.session.add_all([with_order, prior_only])
    db.session.flush()
    _make_order(with_order, "Order", 200, date(2026, 8, 1))
    db.session.commit()

    response = logged_in.get("/analytics")

    # One client in the denominator, so the average is their own lifetime
    # value untouched. Halved (a 2-client denominator) would be the failure.
    assert f"{with_order.lifetime_value:.2f}".encode() in response.data
    assert f"{with_order.lifetime_value / 2:.2f}".encode() not in response.data


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


def _issue_invoice(company, order, issued_on):
    """Raise an invoice on `order` and take it out of draft, dated `issued_on`.

    Goes through the billing service the same way the app's own
    `create_invoice()` route does, so the frozen `InvoiceTaxLine` rows the
    analytics card reads actually get written."""
    billable = billable_for(order)
    invoice = invoicing.create_invoice(
        company.id, billable, display_name=company.name, today=issued_on)
    invoicing.set_status(
        company.id, invoice, "sent", billable, display_name=company.name)
    db.session.flush()
    return invoice


def test_analytics_tax_billed_ytd_shows_frozen_tax_for_the_current_year(
    logged_in, company, client_record
):
    # company is GST-registered; client_record is in QC -> one 5% GST line.
    # 5% of 246.80 = 12.34, a figure that appears nowhere else on the page.
    order = _make_order(client_record, "Briefcase", 246.80, date(2026, 8, 1))
    db.session.flush()
    _issue_invoice(company, order, date.today())
    db.session.commit()

    response = logged_in.get("/analytics").data.decode()

    assert "Tax billed YTD" in response
    assert "GST" in response
    assert "12.34" in response


def test_analytics_tax_billed_ytd_excludes_invoices_issued_in_prior_years(
    logged_in, company, client_record
):
    # Same invoice, but issued years ago: it must not count toward YTD, and
    # the card falls back to its empty state.
    order = _make_order(client_record, "Old briefcase", 246.80, date(2020, 8, 1))
    db.session.flush()
    _issue_invoice(company, order, date(2020, 8, 1))
    db.session.commit()

    response = logged_in.get("/analytics").data.decode()

    assert "No tax billed this year." in response
    assert "12.34" not in response


def _outstanding_figure(body: str) -> str:
    """The Outstanding card's own value.

    Parsed off its own `data-card` rather than searched for as a bare
    substring: half the cards on this page render a dollar amount, so a
    plain `"105.00" in body` would pass just as happily on Revenue or Avg
    value per client and prove nothing about this card.
    """
    match = re.search(
        r'data-card="outstanding".*?stat-card__value">\$([\d,.]+)<', body, re.S)
    assert match is not None, "no Outstanding card rendered on /analytics"
    return match.group(1)


def test_analytics_outstanding_counts_invoiced_work_only(logged_in, company, client_record):
    """AN8 — an order nobody has billed yet isn't money anyone is owed.

    The uninvoiced order here is deliberately the larger of the two: if the
    figure ever starts reading off `Order.balance_due` instead of the
    billing module's issued documents, this reads 5,250 instead of 105 and
    the difference is impossible to miss.
    """
    invoiced = _make_order(client_record, "Billed", 100.0, date(2026, 8, 1))
    _make_order(client_record, "Never billed", 5000.0, date(2026, 8, 1))
    db.session.flush()
    _issue_invoice(company, invoiced, date.today())
    db.session.commit()

    body = logged_in.get("/analytics").data.decode()

    # 100.00 + 5% GST (the company holds GST only; the client is in QC).
    assert _outstanding_figure(body) == "105.00"


def test_analytics_outstanding_drops_an_invoice_once_it_is_paid(logged_in, company, client_record):
    """AN8 — `is_settled`, the other half of the predicate. Issued *and*
    unpaid is what "outstanding" means; a paid invoice is history."""
    order = _make_order(client_record, "Billed and paid", 100.0, date(2026, 8, 1))
    db.session.flush()
    _issue_invoice(company, order, date.today())
    db.session.add(Payment(order_id=order.id, amount=105.0, paid_date=date.today()))
    db.session.commit()

    assert _outstanding_figure(logged_in.get("/analytics").data.decode()) == "0.00"


def test_analytics_outstanding_excludes_a_void_invoice(logged_in, company, client_record):
    """AN8 — voiding is how an issued invoice is called off (hard rule 11
    forbids editing one), so a void must stop counting as money owed."""
    order = _make_order(client_record, "Billed then voided", 100.0, date(2026, 8, 1))
    db.session.flush()
    invoice = _issue_invoice(company, order, date.today())
    invoicing.set_status(
        company.id, invoice, "void", billable_for(order), display_name=company.name)
    db.session.commit()

    assert _outstanding_figure(logged_in.get("/analytics").data.decode()) == "0.00"
