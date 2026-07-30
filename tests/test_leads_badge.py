"""
The "new leads" badge, and the Sync now button on the leads page.

The badge is derived from one timestamp per user rather than stored, so the
properties worth pinning are the three ways it's meant to change: up on a
sync that brings new leads, down when a lead becomes a client, and to zero
when someone opens the inbox.
"""

from datetime import timedelta

from models import Client, User, db

from communications.models import EmailAccount, EmailMessage, EmailThread, LeadReadState, utcnow
from communications.services import email_service

from tests import fakes


def add_lead(company, account, thread_id="t-extra", sender="new@example.com", created_at=None):
    """A lead thread with one incoming message."""
    row = EmailThread(
        company_id=company.id, email_account_id=account.id,
        provider_thread_id=thread_id, subject="Enquiry",
        last_message_date=utcnow(),
    )
    if created_at is not None:
        row.created_at = created_at
    db.session.add(row)
    db.session.flush()
    db.session.add(EmailMessage(
        thread_id=row.id, provider_message_id=f"m-{thread_id}",
        sender=sender, direction="incoming", body_text="Hello",
    ))
    db.session.flush()
    return row


# --- the count ------------------------------------------------------------

def test_no_leads_means_no_badge(app, company, user, account):
    assert email_service.new_lead_count(company.id, user.id) == 0


def test_every_lead_is_new_before_you_have_ever_looked(app, company, user, account, lead_thread):
    """A null marker means "never looked", so a first sync's whole backfill
    counts — it's all new to the person reading it."""
    assert email_service.leads_seen_at(company.id, user.id) is None
    assert email_service.new_lead_count(company.id, user.id) == 1


def test_matched_threads_are_not_leads(app, company, user, account, thread, lead_thread):
    assert email_service.new_lead_count(company.id, user.id) == 1


def test_an_outgoing_only_thread_does_not_count(app, company, user, account):
    """Us mailing a supplier isn't a lead — the badge and the list agree
    because they share one query."""
    row = EmailThread(
        company_id=company.id, email_account_id=account.id,
        provider_thread_id="t-out", subject="Order of hides",
    )
    db.session.add(row)
    db.session.flush()
    db.session.add(EmailMessage(
        thread_id=row.id, provider_message_id="m-out",
        sender="studio@example.com", recipients="supplier@example.com",
        direction="outgoing",
    ))
    db.session.flush()
    assert email_service.new_lead_count(company.id, user.id) == 0


def test_marking_seen_clears_the_count(app, company, user, account, lead_thread):
    email_service.mark_leads_seen(company.id, user.id)
    assert email_service.new_lead_count(company.id, user.id) == 0


def test_a_lead_arriving_after_you_looked_counts_again(app, company, user, account, lead_thread):
    email_service.mark_leads_seen(company.id, user.id)
    add_lead(company, account, thread_id="t-after")
    assert email_service.new_lead_count(company.id, user.id) == 1


def test_a_lead_downloaded_before_you_looked_does_not_count(app, company, user, account):
    """Compared against created_at — when we downloaded it — not the message
    date, so old mail synced today still counts as new."""
    email_service.mark_leads_seen(company.id, user.id)
    add_lead(company, account, thread_id="t-old",
             created_at=utcnow() - timedelta(days=5))
    assert email_service.new_lead_count(company.id, user.id) == 0


def test_converting_a_lead_lowers_the_count(app, company, user, account, lead_thread):
    assert email_service.new_lead_count(company.id, user.id) == 1
    email_service.create_client_from_thread(company.id, lead_thread.id)
    assert email_service.new_lead_count(company.id, user.id) == 0


def test_linking_a_client_by_hand_lowers_the_count(app, company, user, account, lead_thread):
    manual = Client(company_id=company.id, first_name="Jean", last_name="Tremblay")
    db.session.add(manual)
    db.session.flush()
    lead_thread.client_id = manual.id
    db.session.flush()
    assert email_service.new_lead_count(company.id, user.id) == 0


def test_a_sync_that_brings_a_new_lead_raises_the_count(app, company, user, account, client_record):
    email_service.mark_leads_seen(company.id, user.id)
    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-fresh", messages=[fakes.message(sender="stranger@example.com")],
    )]):
        email_service.sync_now(company.id)
    assert email_service.new_lead_count(company.id, user.id) == 1


def test_a_sync_that_brings_nothing_new_leaves_the_count_alone(
    app, company, user, account, client_record, lead_thread,
):
    email_service.mark_leads_seen(company.id, user.id)
    with fakes.fake_providers(threads=[]):
        email_service.sync_now(company.id)
    assert email_service.new_lead_count(company.id, user.id) == 0


# --- isolation ------------------------------------------------------------

def test_the_marker_is_per_user(app, company, user, account, lead_thread):
    """"Unseen by me" is personal — one user clearing the badge for everyone
    would make it useless."""
    second = User(company_id=company.id, username="assistant")
    second.set_password("x")
    db.session.add(second)
    db.session.flush()

    email_service.mark_leads_seen(company.id, user.id)
    assert email_service.new_lead_count(company.id, user.id) == 0
    assert email_service.new_lead_count(company.id, second.id) == 1


def test_the_count_is_tenant_scoped(app, company, other_company, user, account, lead_thread):
    other_user = User(company_id=other_company.id, username="theirs")
    other_user.set_password("x")
    db.session.add(other_user)
    db.session.flush()
    assert email_service.new_lead_count(other_company.id, other_user.id) == 0


def test_marking_seen_is_idempotent(app, company, user, account, lead_thread):
    email_service.mark_leads_seen(company.id, user.id)
    email_service.mark_leads_seen(company.id, user.id)
    assert LeadReadState.query.filter_by(company_id=company.id, user_id=user.id).count() == 1


def test_reading_the_marker_does_not_create_a_row(app, company, user):
    """Called on every page render — a GET must not write."""
    email_service.leads_seen_at(company.id, user.id)
    email_service.new_lead_count(company.id, user.id)
    assert LeadReadState.query.count() == 0


# --- the badge in the UI --------------------------------------------------

def test_the_badge_shows_in_the_top_nav_on_any_page(logged_in, account, lead_thread):
    """Visible from the timeline, so a new lead doesn't need going looking for."""
    body = logged_in.get("/").get_data(as_text=True)
    assert "nav-badge" in body
    assert "1 new lead in the inbox" in body


def test_the_badge_shows_on_the_leads_sub_nav(logged_in, account, lead_thread):
    body = logged_in.get("/clients").get_data(as_text=True)
    assert "nav-badge" in body
    assert "since you last looked" in body


def test_the_badge_counts_several_leads(logged_in, company, account, lead_thread):
    add_lead(company, account, thread_id="t-2", sender="another@example.com")
    db.session.commit()
    body = logged_in.get("/").get_data(as_text=True)
    assert "2 new leads in the inbox" in body


def test_no_badge_is_rendered_at_zero(logged_in, account, thread):
    """Nothing new means no decoration at all, not a grey "0"."""
    assert "nav-badge" not in logged_in.get("/").get_data(as_text=True)


def test_opening_the_leads_page_clears_the_badge(logged_in, account, lead_thread):
    assert "nav-badge" in logged_in.get("/").get_data(as_text=True)
    logged_in.get("/mail/leads")
    assert "nav-badge" not in logged_in.get("/").get_data(as_text=True)


def test_the_leads_page_still_flags_what_was_new_on_that_render(
    logged_in, account, lead_thread,
):
    """The cutoff is read before the marker moves, so the visit that clears
    the badge is also the one that shows you which threads were new."""
    body = logged_in.get("/mail/leads").get_data(as_text=True)
    assert "pill--new" in body


def test_the_new_pill_sits_with_the_subject_not_the_metadata(
    logged_in, account, lead_thread,
):
    """It belongs beside the thing it labels. The subject and the pill share a
    .thread-list__heading row; the pill appearing after the meta line would
    mean the markup drifted back."""
    body = logged_in.get("/mail/leads").get_data(as_text=True)
    heading = body.index("thread-list__heading")
    pill = body.index("pill--new")
    meta = body.index("thread-list__meta")
    assert heading < pill < meta


def test_a_second_visit_no_longer_flags_them(logged_in, account, lead_thread):
    logged_in.get("/mail/leads")
    body = logged_in.get("/mail/leads").get_data(as_text=True)
    assert "pill--new" not in body


def test_converting_a_lead_clears_the_badge_for_everyone_it_was_counting(
    logged_in, csrf, company, account, lead_thread,
):
    logged_in.post(f"/mail/threads/{lead_thread.id}/create-client",
                   data={"csrf_token": csrf})
    assert "nav-badge" not in logged_in.get("/").get_data(as_text=True)


def test_the_badge_survives_a_logged_out_page(app, account, lead_thread):
    """The context processor runs on the login page too, where there is no
    user — a decoration must not 500 the only route back in."""
    db.session.commit()
    assert app.test_client().get("/login").status_code == 200


def test_the_client_email_tab_does_not_flag_new_threads(logged_in, client_record, thread):
    """`new_since` is undefined there, so the shared list renders without any
    new/seen decoration rather than marking everything new."""
    body = logged_in.get(f"/clients/{client_record.id}/emails").get_data(as_text=True)
    assert "pill--new" not in body


# --- Sync now on the leads page -------------------------------------------

def test_the_leads_page_has_a_sync_button(logged_in, account):
    body = logged_in.get("/mail/leads").get_data(as_text=True)
    assert "/integrations/sync" in body
    assert "Sync now" in body


def test_syncing_from_the_leads_page_returns_there(logged_in, csrf, account, client_record):
    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-fresh", messages=[fakes.message(sender="stranger@example.com")],
    )]):
        response = logged_in.post("/integrations/sync", data={
            "csrf_token": csrf, "return_to": "/mail/leads",
        })
    assert response.headers["Location"].endswith("/mail/leads")


def test_a_lead_synced_from_the_page_appears_on_it(logged_in, csrf, account, client_record):
    logged_in.get("/mail/leads")  # clear the badge first
    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-fresh", subject="Bespoke holster",
        messages=[fakes.message(sender="stranger@example.com")],
    )]):
        logged_in.post("/integrations/sync", data={
            "csrf_token": csrf, "return_to": "/mail/leads",
        })
    body = logged_in.get("/mail/leads").get_data(as_text=True)
    assert "Bespoke holster" in body
    assert "pill--new" in body  # flagged as new, having arrived since the visit above


def test_the_sync_button_is_disabled_without_a_mailbox(logged_in):
    body = logged_in.get("/mail/leads").get_data(as_text=True)
    assert "disabled" in body
    assert "No mailbox is connected" in body


def test_the_leads_page_says_when_automatic_sync_is_off(logged_in, account, monkeypatch):
    monkeypatch.delenv("RUN_SCHEDULER", raising=False)
    body = logged_in.get("/mail/leads").get_data(as_text=True)
    assert "Automatic syncing isn't running" in body


def test_the_sync_status_note_shares_the_row_with_the_button(logged_in, account):
    """The note explains the button, so it sits on the same line rather than
    in a block underneath it."""
    body = logged_in.get("/mail/leads").get_data(as_text=True)
    row_start = body.index('class="legend-row"')
    row_end = body.index("</div>", row_start)
    row = body[row_start:row_end]
    assert "legend-row__note" in row
    assert "Sync now" in row


def test_syncing_from_the_leads_page_needs_a_csrf_token(logged_in, account):
    assert logged_in.post("/integrations/sync",
                          data={"return_to": "/mail/leads"}).status_code == 400
