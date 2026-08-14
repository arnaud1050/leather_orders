"""
Hiding a client — REQUIREMENTS.md CL17 … CL21.

The rules worth protecting, in order of how much damage getting them wrong
would do:

1. **Hiding never touches an order.** It is a statement about the client
   list and nothing else. An order is time the studio spent and money it
   was owed, and no decision about a contact list gets to retract that —
   so the timeline, /orders and every figure on /analytics must be
   bit-identical either side of a hide. Most of this file is that one
   assertion from different angles.
2. There is no delete. Hiding is the whole of the vocabulary, and it is
   reversible from both directions.
3. A hidden client who writes in comes back by themselves (CL21), on the
   same terms as the lead inbox's L-15 — incoming mail only, and only mail
   the sync hasn't already stored.
"""

from datetime import date

import pytest

from models import Client, Order, OrderLine, db

from communications.sync import email_sync

from tests import fakes


@pytest.fixture
def hidden_client(company):
    row = Client(
        company_id=company.id, first_name="Luc", last_name="Fournier",
        email="luc@example.com",
    )
    row.is_hidden = True
    db.session.add(row)
    db.session.flush()
    return row


def _order_for(client, item="Belt", price=120.0, status="delivered"):
    row = Order(
        client_id=client.id, item=item,
        start=date(2026, 6, 1), due=date(2026, 6, 20), status=status,
    )
    db.session.add(row)
    db.session.flush()
    db.session.add(OrderLine(
        order_id=row.id, description=item, quantity=1, unit_price=price,
    ))
    db.session.flush()
    return row


# --- the toggle itself ----------------------------------------------------

def test_a_new_client_is_not_hidden(app, company):
    """The column defaults off, so every client already on file stays on the
    list when this ships."""
    row = Client(company_id=company.id, first_name="Ada", last_name="Roy")
    db.session.add(row)
    db.session.commit()

    assert row.is_hidden is False


def test_hiding_and_showing_are_the_same_route(app, logged_in, client_record):
    logged_in.post(f"/clients/{client_record.id}/hide")
    assert db.session.get(Client, client_record.id).is_hidden is True

    logged_in.post(f"/clients/{client_record.id}/hide")
    assert db.session.get(Client, client_record.id).is_hidden is False


def test_hiding_redirects_to_return_to(app, logged_in, client_record):
    response = logged_in.post(
        f"/clients/{client_record.id}/hide", data={"return_to": "/clients"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/clients"


def test_hiding_is_scoped_to_the_tenant(app, logged_in, other_company):
    """CO3 — another company's id is a 404, not a 403 and not a hide."""
    theirs = Client(
        company_id=other_company.id, first_name="Sam", last_name="Roy",
    )
    db.session.add(theirs)
    db.session.commit()

    assert logged_in.post(f"/clients/{theirs.id}/hide").status_code == 404
    assert db.session.get(Client, theirs.id).is_hidden is False


def test_hiding_requires_login(app, client_record):
    with app.test_client() as anonymous:
        response = anonymous.post(f"/clients/{client_record.id}/hide")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


# --- what hiding takes away -----------------------------------------------

def test_the_roster_leaves_out_hidden_clients(app, logged_in, client_record, hidden_client):
    body = logged_in.get("/clients").get_data(as_text=True)

    assert "Marie Alarie" in body
    assert "Luc Fournier" not in body


def test_the_hidden_view_shows_only_hidden_clients(
    app, logged_in, client_record, hidden_client,
):
    body = logged_in.get("/clients?hidden=1").get_data(as_text=True)

    assert "Luc Fournier" in body
    assert "Marie Alarie" not in body


def test_the_roster_links_to_the_hidden_view_only_when_there_is_one(
    app, logged_in, client_record, hidden_client,
):
    assert "1 hidden client" in logged_in.get("/clients").get_data(as_text=True)

    hidden_client.is_hidden = False
    db.session.commit()

    assert "hidden client" not in logged_in.get("/clients").get_data(as_text=True)


def test_a_hidden_client_is_not_offered_on_a_new_order(
    app, logged_in, client_record, hidden_client,
):
    """OT5's rule one model over: hiding filters the *new* selection."""
    body = logged_in.get("/orders/new").get_data(as_text=True)

    assert "Marie Alarie" in body
    assert "Luc Fournier" not in body


def test_a_hidden_clients_own_page_still_works(app, logged_in, hidden_client):
    response = logged_in.get(f"/clients/{hidden_client.id}")

    assert response.status_code == 200
    assert "Luc Fournier" in response.get_data(as_text=True)


# --- what hiding must not touch -------------------------------------------

def test_hiding_leaves_the_orders_list_alone(app, logged_in, hidden_client):
    _order_for(hidden_client, item="Card wallet")
    db.session.commit()

    body = logged_in.get("/orders").get_data(as_text=True)

    assert "Card wallet" in body
    assert "Luc Fournier" in body


def test_hiding_leaves_the_timeline_alone(app, logged_in, hidden_client):
    """Their bar is still on the schedule, under their own name — those weeks
    were genuinely spent on it."""
    _order_for(hidden_client, item="Card wallet", status="confirmed")
    db.session.commit()

    body = logged_in.get("/timeline/2026/6/1").get_data(as_text=True)

    assert "Card wallet" in body
    assert "Luc Fournier" in body


def test_hiding_leaves_lifetime_value_alone(app, hidden_client):
    _order_for(hidden_client, price=120.0)
    db.session.commit()

    assert hidden_client.lifetime_value == pytest.approx(120.0)


def test_hiding_does_not_change_a_single_analytics_figure(app, logged_in, client_record):
    """The one that would be a lie rather than an inconvenience: last year's
    takings must not change because somebody tidied a list this morning."""
    _order_for(client_record, item="Card wallet", price=200.0)
    db.session.commit()
    before = logged_in.get("/analytics").get_data(as_text=True)

    client_record.is_hidden = True
    db.session.commit()

    assert logged_in.get("/analytics").get_data(as_text=True) == before


# --- coming back (CL21) ---------------------------------------------------

def _sync_with(account, sender, direction="incoming", message_id="m-new"):
    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-1", messages=[
            fakes.message(message_id=message_id, thread_id="t-1",
                          sender=sender, direction=direction),
        ],
    )]):
        return email_sync.sync_account(account)


def test_new_mail_puts_a_hidden_client_back_on_the_list(app, account, hidden_client):
    account.last_sync_at = None
    db.session.commit()

    result = _sync_with(account, "luc@example.com")

    assert result.clients_resurfaced == 1
    assert db.session.get(Client, hidden_client.id).is_hidden is False


def test_coming_back_shows_in_the_sync_summary(app, account, hidden_client):
    account.last_sync_at = None
    db.session.commit()

    result = _sync_with(account, "luc@example.com")

    assert "1 hidden client(s) back on the list" in result.summary()


def test_mailing_a_hidden_client_does_not_bring_them_back(app, account, hidden_client):
    """Us writing to them isn't them writing back — L-16, one model over."""
    account.last_sync_at = None
    db.session.commit()

    result = _sync_with(account, "studio@example.com", direction="outgoing")

    assert result.clients_resurfaced == 0
    assert db.session.get(Client, hidden_client.id).is_hidden is True


def test_resyncing_the_same_window_does_not_bring_them_back_twice(
    app, account, hidden_client,
):
    """The guard is _store_message returning early on a message it has
    already stored, so an overlapping window can't re-hide-then-unhide."""
    account.last_sync_at = None
    db.session.commit()
    _sync_with(account, "luc@example.com")

    hidden_client.is_hidden = True
    db.session.commit()

    result = _sync_with(account, "luc@example.com")

    assert result.clients_resurfaced == 0
    assert db.session.get(Client, hidden_client.id).is_hidden is True


def test_mail_from_a_visible_client_counts_nothing(app, account, client_record):
    account.last_sync_at = None
    db.session.commit()

    result = _sync_with(account, "marie@example.com")

    assert result.clients_resurfaced == 0
    assert db.session.get(Client, client_record.id).is_hidden is False
