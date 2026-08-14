"""The order lifecycle — transitions, rush, cancelling and deleting.

Covers REQUIREMENTS OR1 … OR1h. The rules worth defending here are the ones
where the UI and the server could drift apart: a button the template hides is
not a permission check, and every `can_*` property is re-checked by the route
that acts on it.

The deliberate regressions this file was written against:

* letting `edit_order()` write any status in `STATUS_LABELS` (which is how it
  worked before the lifecycle) lets a delivered order go back to tentative
* deleting an order with `db.session.delete()` alone leaves its documents'
  bytes on disk and its materials' stock still drawn down, because neither
  module hangs off an `Order` relationship
* deriving "in progress" from a stored stage instead of the start date lets an
  order sit at "Confirmed" weeks into the work
"""

from datetime import date, timedelta

import pytest

from models import Client, Order, OrderLine, Payment, db


def issue_invoice(logged_in, order, status="draft"):
    """Put an invoice on `order`, optionally moving it out of draft.

    Goes through the real routes rather than constructing an Invoice — the
    thing under test is `is_issued`, and only the module gets to decide what
    that means.
    """
    logged_in.post(f"/subjects/{order.id}/invoice", data={})
    db.session.expire_all()
    invoice = db.session.get(Order, order.id).invoice
    if status != "draft":
        logged_in.post(f"/invoices/{invoice.id}/status", data={"status": status})
        db.session.expire_all()
    return db.session.get(Order, order.id).invoice


@pytest.fixture
def tentative_order(client_record):
    row = Order(
        client_id=client_record.id, item="Saddle bag",
        start=date.today() + timedelta(days=10),
        due=date.today() + timedelta(days=24),
        status="tentative",
    )
    db.session.add(row)
    db.session.flush()
    db.session.add(OrderLine(
        order_id=row.id, description="Saddle bag", quantity=1, unit_price=390.0,
    ))
    db.session.flush()
    return row


# ---------------------------------------------------------------------------
# display_status — one stored stage, two labels (OR1c)
# ---------------------------------------------------------------------------

def test_confirmed_order_reads_as_confirmed_before_its_start(order):
    order.start = date.today() + timedelta(days=3)
    assert order.status == "confirmed"
    assert order.display_status == "confirmed"


def test_confirmed_order_reads_as_in_progress_once_started(order):
    order.start = date.today() - timedelta(days=1)
    assert order.status == "confirmed"
    assert order.display_status == "in_progress"


def test_confirmed_order_reads_as_in_progress_on_its_start_date(order):
    """The boundary is inclusive — work starting today has started."""
    order.start = date.today()
    assert order.display_status == "in_progress"


def test_display_status_leaves_every_other_stage_alone(order):
    order.start = date.today() - timedelta(days=1)
    for stage in ("tentative", "ready", "delivered", "cancelled"):
        order.status = stage
        assert order.display_status == stage


def test_in_progress_is_never_a_stored_status():
    """It's a label, not a stage — so it has no transitions of its own and
    nothing may be moved *to* it."""
    from app import ALLOWED_TRANSITIONS

    assert "in_progress" not in ALLOWED_TRANSITIONS
    assert not any("in_progress" in nxt for nxt in ALLOWED_TRANSITIONS.values())


# ---------------------------------------------------------------------------
# Transitions (OR1a)
# ---------------------------------------------------------------------------

def test_forward_transition_is_accepted(logged_in, order):
    logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(),
        "due": order.due.isoformat(), "status": "ready",
    })
    assert db.session.get(Order, order.id).status == "ready"


@pytest.mark.parametrize("start,target", [
    ("delivered", "tentative"),   # backwards from a final stage
    ("delivered", "ready"),       # backwards by one
    ("ready", "confirmed"),       # backwards by one
    ("tentative", "ready"),       # skipping confirmed
    ("tentative", "delivered"),   # skipping the middle entirely
    ("cancelled", "confirmed"),   # resurrecting a cancelled order
])
def test_illegal_transitions_are_refused(logged_in, order, start, target):
    order.status = start
    db.session.commit()

    response = logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(),
        "due": order.due.isoformat(), "status": target,
    })

    assert response.status_code == 400
    assert db.session.get(Order, order.id).status == start


def test_cancelling_is_not_reachable_through_the_edit_form(logged_in, order):
    """A legal transition, but not one the plain form may make — it has to
    collect a reason, so it goes through cancel_order()."""
    response = logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(),
        "due": order.due.isoformat(), "status": "cancelled",
    })

    assert response.status_code == 400
    assert db.session.get(Order, order.id).status == "confirmed"


def test_a_new_order_cannot_be_created_already_finished(logged_in, client_record):
    logged_in.post("/orders/new", data={
        "client_id": str(client_record.id), "item": "Belt",
        "start": "2026-08-01", "due": "2026-08-15", "price": "95",
        "status": "delivered",
    })
    created = Order.query.filter_by(item="Belt").first()
    assert created.status == "tentative"


# ---------------------------------------------------------------------------
# Rush is a flag, not a stage (OR1d)
# ---------------------------------------------------------------------------

def test_rush_can_be_flagged_and_unflagged_on_a_confirmed_order(logged_in, order):
    logged_in.post(f"/orders/{order.id}/rush")
    assert db.session.get(Order, order.id).is_rush is True

    logged_in.post(f"/orders/{order.id}/rush")
    assert db.session.get(Order, order.id).is_rush is False


@pytest.mark.parametrize("stage", ["tentative", "ready", "delivered", "cancelled"])
def test_rush_is_refused_on_anything_but_confirmed(logged_in, order, stage):
    """`ready` is in the list on purpose: the piece is finished and waiting
    on its owner, and the studio can't hurry that. "Rush, ready for pickup"
    describes nothing."""
    order.status = stage
    db.session.commit()

    response = logged_in.post(f"/orders/{order.id}/rush")

    assert response.status_code == 400
    assert db.session.get(Order, order.id).is_rush is False


def test_moving_to_ready_clears_the_rush_flag(logged_in, order):
    order.is_rush = True
    db.session.commit()

    logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(),
        "due": order.due.isoformat(), "status": "ready",
    })

    fresh = db.session.get(Order, order.id)
    assert fresh.status == "ready"
    assert fresh.is_rush is False


def test_rush_cannot_be_smuggled_in_alongside_a_move_to_ready(logged_in, order):
    """The checkbox and the status dropdown post together, so a form that
    ticks rush *and* advances to ready must not end up with both."""
    logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(),
        "due": order.due.isoformat(), "status": "ready",
        "rush_field": "1", "is_rush": "on",
    })

    fresh = db.session.get(Order, order.id)
    assert fresh.status == "ready"
    assert fresh.is_rush is False


# --- the timeline modal's checkbox ----------------------------------------

def test_the_modal_checkbox_sets_rush(logged_in, order):
    logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(),
        "due": order.due.isoformat(), "status": order.status,
        "rush_field": "1", "is_rush": "on",
    })
    assert db.session.get(Order, order.id).is_rush is True


def test_the_modal_checkbox_unticked_clears_rush(logged_in, order):
    """An unticked checkbox posts nothing at all — the marker field is the
    only thing distinguishing that from a form with no rush control."""
    order.is_rush = True
    db.session.commit()

    logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(),
        "due": order.due.isoformat(), "status": order.status,
        "rush_field": "1",
    })

    assert db.session.get(Order, order.id).is_rush is False


def test_a_form_with_no_rush_field_leaves_rush_alone(logged_in, order):
    """Hard rule 9 again: the order page's own edit form has no checkbox
    (it has a button instead), so saving it must not clear the flag."""
    order.is_rush = True
    db.session.commit()

    logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(),
        "due": order.due.isoformat(), "status": order.status,
    })

    assert db.session.get(Order, order.id).is_rush is True


# ---------------------------------------------------------------------------
# Cancelling (OR1f, OR1g)
# ---------------------------------------------------------------------------

def test_cancelling_records_a_dated_reason_in_the_notes(logged_in, order):
    order.notes = "Horween Chromexcel"
    db.session.commit()

    logged_in.post(f"/orders/{order.id}/cancel", data={
        "reason": "Client moved abroad",
    })

    fresh = db.session.get(Order, order.id)
    assert fresh.status == "cancelled"
    assert "Horween Chromexcel" in fresh.notes
    assert f"Cancelled {date.today().isoformat()}: Client moved abroad" in fresh.notes


def test_cancelling_without_a_reason_leaves_the_notes_alone(logged_in, order):
    order.notes = "Horween Chromexcel"
    db.session.commit()

    logged_in.post(f"/orders/{order.id}/cancel", data={"reason": "   "})

    fresh = db.session.get(Order, order.id)
    assert fresh.status == "cancelled"
    assert fresh.notes == "Horween Chromexcel"


def test_cancelling_an_order_with_no_notes_yet(logged_in, order):
    logged_in.post(f"/orders/{order.id}/cancel", data={"reason": "Changed mind"})
    assert db.session.get(Order, order.id).notes.startswith("Cancelled ")


def test_cancelling_clears_the_rush_flag(logged_in, order):
    order.is_rush = True
    db.session.commit()

    logged_in.post(f"/orders/{order.id}/cancel", data={"reason": "Called off"})

    assert db.session.get(Order, order.id).is_rush is False


def test_a_tentative_order_can_also_be_cancelled(logged_in, tentative_order):
    """Deleting isn't the only way out of tentative — a quote that was
    declined is worth keeping on the client's record."""
    logged_in.post(f"/orders/{tentative_order.id}/cancel", data={
        "reason": "Quote declined",
    })
    assert db.session.get(Order, tentative_order.id).status == "cancelled"


@pytest.mark.parametrize("stage", ["delivered", "cancelled"])
def test_a_finished_order_cannot_be_cancelled(logged_in, order, stage):
    order.status = stage
    db.session.commit()

    response = logged_in.post(f"/orders/{order.id}/cancel", data={"reason": "x"})

    assert response.status_code == 400
    assert db.session.get(Order, order.id).status == stage


def test_cancelling_is_refused_while_an_issued_invoice_exists(logged_in, order):
    """Hard rule 11 freezes an issued invoice, so cancelling must not void
    one as a side effect — the user voids it first, deliberately."""
    issue_invoice(logged_in, order, status="sent")

    response = logged_in.post(f"/orders/{order.id}/cancel", data={"reason": "x"})

    assert response.status_code == 400
    assert db.session.get(Order, order.id).status == "confirmed"


def test_a_draft_invoice_does_not_block_cancelling(logged_in, order):
    """A draft hasn't gone to anyone, so nothing is frozen yet."""
    issue_invoice(logged_in, order, status="draft")

    logged_in.post(f"/orders/{order.id}/cancel", data={"reason": "x"})

    assert db.session.get(Order, order.id).status == "cancelled"


def test_cancelled_orders_are_off_the_timeline_but_still_in_the_orders_list(
    logged_in, order
):
    logged_in.post(f"/orders/{order.id}/cancel", data={"reason": "Called off"})

    assert order.item not in logged_in.get("/").get_data(as_text=True)
    assert order.item in logged_in.get("/orders").get_data(as_text=True)


# ---------------------------------------------------------------------------
# Deleting (OR1e)
# ---------------------------------------------------------------------------

def test_a_tentative_order_can_be_deleted(logged_in, tentative_order):
    order_id = tentative_order.id
    logged_in.post(f"/orders/{order_id}/delete")
    assert db.session.get(Order, order_id) is None


def test_deleting_takes_the_line_items_with_it(logged_in, tentative_order):
    order_id = tentative_order.id
    logged_in.post(f"/orders/{order_id}/delete")
    assert OrderLine.query.filter_by(order_id=order_id).count() == 0


@pytest.mark.parametrize("stage", ["confirmed", "ready", "delivered", "cancelled"])
def test_only_a_tentative_order_can_be_deleted(logged_in, order, stage):
    order.status = stage
    db.session.commit()

    response = logged_in.post(f"/orders/{order.id}/delete")

    assert response.status_code == 400
    assert db.session.get(Order, order.id) is not None


def test_a_tentative_order_with_a_payment_cannot_be_deleted(
    logged_in, tentative_order
):
    """Money attached means there's a record to keep, even this early."""
    db.session.add(Payment(
        order_id=tentative_order.id, amount=100.0, paid_date=date.today(),
        method="cash",
    ))
    db.session.commit()

    response = logged_in.post(f"/orders/{tentative_order.id}/delete")

    assert response.status_code == 400
    assert db.session.get(Order, tentative_order.id) is not None


def test_a_tentative_order_with_an_invoice_cannot_be_deleted(
    logged_in, tentative_order
):
    issue_invoice(logged_in, tentative_order, status="draft")

    response = logged_in.post(f"/orders/{tentative_order.id}/delete")

    assert response.status_code == 400
    assert db.session.get(Order, tentative_order.id) is not None


@pytest.fixture
def order_with_material(company, tentative_order):
    """A tentative order that has drawn 5 sqft off a 20 sqft item."""
    from inventory import services as inventory_service
    from inventory.models import InventoryItem

    item = InventoryItem(
        company_id=company.id, name="Veg-tan shoulder", unit="sqft",
        quantity_on_hand=20.0, unit_price=12.0,
    )
    db.session.add(item)
    db.session.flush()

    material = inventory_service.add_material(
        company_id=company.id, order_id=tentative_order.id,
        inventory_item_id=item.id, quantity_used=5.0,
    )
    assert item.quantity_on_hand == 15.0
    return tentative_order, item, material


def test_an_order_with_materials_drawn_cannot_be_deleted(
    logged_in, order_with_material
):
    """The app must not decide whether that stock goes back on the shelf —
    it might have been used up on a prototype. The user clears the
    materials themselves, choosing per row, and only then may the order go.
    """
    order, item, _ = order_with_material

    response = logged_in.post(f"/orders/{order.id}/delete")

    assert response.status_code == 400
    assert db.session.get(Order, order.id) is not None
    # And crucially the stock is left exactly where it was — a refused
    # delete must not quietly restock as a side effect either.
    assert db.session.get(item.__class__, item.id).quantity_on_hand == 15.0


def test_deleting_is_allowed_once_the_materials_are_cleared(
    logged_in, order_with_material
):
    from inventory import services as inventory_service

    order, _, material = order_with_material
    assert order.can_delete is False

    inventory_service.delete_material(order.id, material.id)

    assert order.can_delete is True
    logged_in.post(f"/orders/{order.id}/delete")
    assert db.session.get(Order, order.id) is None


def test_the_order_page_explains_why_delete_is_missing(
    logged_in, order_with_material
):
    """A control that vanishes with no explanation is the thing this
    avoids — the note has to point at the tab that unblocks it."""
    order, _, _ = order_with_material

    html = logged_in.get(f"/orders/{order.id}").get_data(as_text=True)

    assert "can't be deleted while materials are drawn against it" in html
    assert f"/orders/{order.id}/materials" in html


def test_a_one_off_other_cost_does_not_block_deleting(
    logged_in, company, tentative_order
):
    """"Other" costs carry no stock, so removing them decides nothing —
    they cascade away like the order's own line items."""
    from inventory import services as inventory_service
    from inventory.models import OrderMaterialOther

    inventory_service.add_other(
        company_id=company.id, order_id=tentative_order.id,
        description="Courier", cost=18.0,
    )
    order_id = tentative_order.id

    logged_in.post(f"/orders/{order_id}/delete")

    assert db.session.get(Order, order_id) is None
    assert OrderMaterialOther.query.filter_by(order_id=order_id).count() == 0


def test_cancelling_leaves_materials_and_their_stock_untouched(
    logged_in, order_with_material
):
    """Cancel is the way out that keeps everything — that's the whole
    reason deleting is the restricted one."""
    from inventory.models import OrderMaterial

    order, item, _ = order_with_material

    logged_in.post(f"/orders/{order.id}/cancel", data={"reason": "Called off"})

    assert db.session.get(Order, order.id).status == "cancelled"
    assert OrderMaterial.query.filter_by(order_id=order.id).count() == 1
    assert db.session.get(item.__class__, item.id).quantity_on_hand == 15.0


# ---------------------------------------------------------------------------
# Notes survive a form that doesn't render them (hard rule 9)
# ---------------------------------------------------------------------------

def test_a_quick_edit_without_a_notes_field_leaves_notes_alone(logged_in, order):
    """The timeline modal posts to edit_order() and renders no notes field
    (OR4). This used to blank them on every save — which would also have
    erased any cancellation reason written by cancel_order()."""
    order.notes = "Horween Chromexcel, brass hardware"
    db.session.commit()

    logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(),
        "due": order.due.isoformat(), "status": order.status,
    })

    assert db.session.get(Order, order.id).notes == "Horween Chromexcel, brass hardware"


def test_a_form_that_does_render_notes_still_writes_them(logged_in, order):
    logged_in.post(f"/orders/{order.id}/edit", data={
        "item": order.item, "start": order.start.isoformat(),
        "due": order.due.isoformat(), "status": order.status,
        "notes": "Switched to English bridle",
    })

    assert db.session.get(Order, order.id).notes == "Switched to English bridle"


def test_a_cancellation_reason_survives_a_later_quick_edit(logged_in, order):
    """The two rules meeting: cancel writes to notes, and the modal that
    doesn't render notes must not undo it."""
    logged_in.post(f"/orders/{order.id}/cancel", data={"reason": "Client moved"})

    logged_in.post(f"/orders/{order.id}/edit", data={
        "item": "Renamed", "start": order.start.isoformat(),
        "due": order.due.isoformat(),
    })

    assert "Client moved" in db.session.get(Order, order.id).notes


# ---------------------------------------------------------------------------
# Tenant isolation — the lifecycle routes are new attack surface (hard rule 1)
# ---------------------------------------------------------------------------

@pytest.fixture
def other_companys_order(other_company):
    """An order belonging to a different tenant entirely."""
    client = Client(
        company_id=other_company.id, first_name="Alex", last_name="Doe",
        email="alex@example.com",
    )
    db.session.add(client)
    db.session.flush()
    row = Order(
        client_id=client.id, item="Not yours",
        start=date.today(), due=date.today() + timedelta(days=7),
        status="tentative",
    )
    db.session.add(row)
    db.session.flush()
    return row


@pytest.mark.parametrize("action", ["delete", "cancel", "rush"])
def test_lifecycle_routes_are_scoped_to_the_signed_in_company(
    logged_in, other_companys_order, action
):
    response = logged_in.post(f"/orders/{other_companys_order.id}/{action}")

    assert response.status_code == 404
    assert db.session.get(Order, other_companys_order.id) is not None
