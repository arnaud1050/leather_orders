"""
The lead badge, the "New" pill, and the Sync now button on the leads page.

Two separate questions, deliberately answered by two different rules — the
tests here mostly exist to keep them from collapsing back into one:

- **The badge** counts leads *awaiting triage*. Looking is not doing, so
  opening the inbox changes nothing; it clears when a lead is converted to a
  client, hidden, or trashed.
- **The "New" pill** marks a conversation nobody has *opened*. It clears when
  that thread is read, and only that thread.

A lead that's been read but not dealt with is therefore pill-less and still
counted, which is the state the old single "when did you last look"
timestamp couldn't express — it cleared both at once, on a page view.
"""

from models import Client, User, db

from communications.models import EmailAccount, EmailMessage, EmailThread, utcnow
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

def test_no_leads_means_no_badge(app, company, account):
    assert email_service.pending_lead_count(company.id) == 0


def test_a_waiting_lead_is_counted(app, company, account, lead_thread):
    assert email_service.pending_lead_count(company.id) == 1


def test_matched_threads_are_not_leads(app, company, account, thread, lead_thread):
    assert email_service.pending_lead_count(company.id) == 1


def test_an_outgoing_only_thread_does_not_count(app, company, account):
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
    assert email_service.pending_lead_count(company.id) == 0


def test_the_count_matches_the_list_it_points_at(app, company, account, lead_thread):
    """One query behind both, so the number beside the link and the rows
    behind it can't disagree."""
    add_lead(company, account, thread_id="t-2", sender="other@example.com")
    assert email_service.pending_lead_count(company.id) == len(
        email_service.lead_threads(company.id)
    )


# --- what clears it, and what deliberately doesn't ------------------------

def test_reading_a_lead_does_not_clear_the_count(app, company, account, lead_thread):
    """The whole point of the change: an enquiry you've read but not answered
    is still an enquiry you haven't answered."""
    email_service.mark_thread_opened(company.id, lead_thread.id)
    assert email_service.pending_lead_count(company.id) == 1


def test_converting_a_lead_clears_it(app, company, account, lead_thread):
    email_service.create_client_from_thread(company.id, lead_thread.id)
    assert email_service.pending_lead_count(company.id) == 0


def test_hiding_a_lead_clears_it(app, company, account, lead_thread):
    email_service.dismiss_thread(company.id, lead_thread.id)
    assert email_service.pending_lead_count(company.id) == 0


def test_trashing_a_lead_clears_it(app, company, account, lead_thread):
    with fakes.fake_providers():
        email_service.trash_thread(company.id, lead_thread.id)
    assert email_service.pending_lead_count(company.id) == 0


def test_linking_a_client_by_hand_clears_it(app, company, account, lead_thread):
    manual = Client(company_id=company.id, first_name="Jean", last_name="Tremblay")
    db.session.add(manual)
    db.session.flush()
    lead_thread.client_id = manual.id
    db.session.flush()
    assert email_service.pending_lead_count(company.id) == 0


def test_restoring_a_hidden_lead_counts_it_again(app, company, account, lead_thread):
    email_service.dismiss_thread(company.id, lead_thread.id)
    email_service.restore_thread(company.id, lead_thread.id)
    assert email_service.pending_lead_count(company.id) == 1


def test_a_sync_that_brings_a_new_lead_raises_the_count(app, company, account, client_record):
    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-fresh", messages=[fakes.message(sender="stranger@example.com")],
    )]):
        email_service.sync_now(company.id)
    assert email_service.pending_lead_count(company.id) == 1


def test_a_sync_that_brings_nothing_new_leaves_the_count_alone(
    app, company, account, client_record, lead_thread,
):
    with fakes.fake_providers(threads=[]):
        email_service.sync_now(company.id)
    assert email_service.pending_lead_count(company.id) == 1


# --- isolation ------------------------------------------------------------

def test_the_count_is_tenant_scoped(app, company, other_company, account, lead_thread):
    assert email_service.pending_lead_count(other_company.id) == 0


def test_the_count_is_not_per_user(app, company, account, lead_thread):
    """One studio, one shared inbox: an enquiry is outstanding for everyone
    until somebody deals with it, not until each person has glanced at it."""
    assert email_service.pending_lead_count(company.id) == 1
    email_service.mark_thread_opened(company.id, lead_thread.id)
    assert email_service.pending_lead_count(company.id) == 1


# --- the opened marker ----------------------------------------------------

def test_a_thread_starts_unopened(app, company, account, lead_thread):
    assert lead_thread.is_unopened is True


def test_opening_marks_it_read(app, company, account, lead_thread):
    email_service.mark_thread_opened(company.id, lead_thread.id)
    assert lead_thread.is_unopened is False
    assert lead_thread.opened_at is not None


def test_only_the_first_open_is_stamped(app, company, account, lead_thread):
    """The pill answers "has anyone looked at this yet" — re-reading a thread
    shouldn't rewrite when it stopped being new."""
    email_service.mark_thread_opened(company.id, lead_thread.id)
    first = lead_thread.opened_at
    email_service.mark_thread_opened(company.id, lead_thread.id)
    assert lead_thread.opened_at == first


def test_opening_one_thread_leaves_the_others_new(app, company, account, lead_thread):
    """The failure of the old design: one marker per user meant reading any
    thread marked every thread read."""
    second = add_lead(company, account, thread_id="t-2", sender="other@example.com")
    db.session.commit()

    email_service.mark_thread_opened(company.id, lead_thread.id)

    assert lead_thread.is_unopened is False
    assert second.is_unopened is True


def test_marking_another_tenants_thread_does_nothing(app, other_company, account, lead_thread):
    email_service.mark_thread_opened(other_company.id, lead_thread.id)
    assert lead_thread.is_unopened is True


def test_a_new_message_does_not_make_a_read_thread_new_again(
    app, company, account, lead_thread,
):
    """Debatable, and worth pinning either way: "New" means the conversation
    has never been opened, not that it has unread messages. Unread state per
    message is a separate feature (see Known gaps) and would need Gmail's
    labels written back to be honest about it."""
    email_service.mark_thread_opened(company.id, lead_thread.id)
    account.last_sync_at = None
    db.session.commit()

    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-lead", messages=[
            fakes.message(message_id="m-lead", thread_id="t-lead"),
            fakes.message(message_id="m-new", thread_id="t-lead"),
        ],
    )]):
        email_service.sync_now(company.id)

    assert lead_thread.is_unopened is False


# --- the badge in the UI --------------------------------------------------

def test_the_badge_shows_in_the_top_nav_on_any_page(logged_in, account, lead_thread):
    """Visible from the timeline, so a waiting lead doesn't need going looking
    for."""
    body = logged_in.get("/").get_data(as_text=True)
    assert "nav-badge" in body
    assert "1 lead waiting" in body


def test_the_badge_shows_on_the_leads_sub_nav(logged_in, account, lead_thread):
    body = logged_in.get("/clients").get_data(as_text=True)
    assert "nav-badge" in body
    assert "convert, hide or trash to clear this" in body


def test_the_badge_counts_several_leads(logged_in, company, account, lead_thread):
    add_lead(company, account, thread_id="t-2", sender="another@example.com")
    db.session.commit()
    assert "2 leads waiting" in logged_in.get("/").get_data(as_text=True)


def test_no_badge_is_rendered_at_zero(logged_in, account, thread):
    """Nothing waiting means no lead decoration at all, not a grey "0".

    Asserted on the unmodified `class="nav-badge"` rather than on the
    substring: the Clients link carries other badges (unread client mail,
    here — the fixture thread belongs to a client), and this test is about
    the lead one."""
    assert 'class="nav-badge"' not in logged_in.get("/").get_data(as_text=True)


def test_opening_the_leads_page_does_not_clear_the_badge(logged_in, account, lead_thread):
    """The bug this replaced: the reminder disappeared on the way to the
    page, before anything had been done about it."""
    logged_in.get("/mail/leads")
    assert "nav-badge" in logged_in.get("/").get_data(as_text=True)


def test_opening_a_lead_does_not_clear_the_badge(logged_in, account, lead_thread):
    logged_in.get(f"/mail/threads/{lead_thread.id}")
    assert "nav-badge" in logged_in.get("/").get_data(as_text=True)


def test_hiding_a_lead_clears_the_badge(logged_in, csrf, account, lead_thread):
    logged_in.post(f"/mail/threads/{lead_thread.id}/dismiss", data={"csrf_token": csrf})
    assert "nav-badge" not in logged_in.get("/").get_data(as_text=True)


def test_converting_a_lead_clears_the_badge(logged_in, csrf, company, account, lead_thread):
    """The lead badge, specifically. Converting hands the thread to a client,
    so its unread message legitimately starts counting as client mail
    instead — one kind of "deal with this" becoming another."""
    logged_in.post(f"/mail/threads/{lead_thread.id}/create-client",
                   data={"csrf_token": csrf})
    assert 'class="nav-badge"' not in logged_in.get("/").get_data(as_text=True)


def test_the_badge_survives_a_logged_out_page(app, account, lead_thread):
    """The context processor runs on the login page too, where there is no
    user — a decoration must not 500 the only route back in."""
    db.session.commit()
    assert app.test_client().get("/login").status_code == 200


def test_a_second_user_sees_the_same_count(app, company, account, lead_thread):
    """Not per user: the work is outstanding for the studio."""
    second = User(company_id=company.id, username="colleague")
    second.set_password("changeme")
    db.session.add(second)
    db.session.commit()

    with app.test_client() as client:
        client.post("/login", data={"username": "colleague", "password": "changeme"},
                    follow_redirects=True)
        assert "1 lead waiting" in client.get("/").get_data(as_text=True)


def test_another_tenants_lead_is_not_counted(logged_in, other_company):
    theirs = EmailAccount(
        company_id=other_company.id, provider="gmail",
        email_address="theirs@example.com",
    )
    db.session.add(theirs)
    db.session.flush()
    add_lead(other_company, theirs, thread_id="t-theirs")
    db.session.commit()

    assert "nav-badge" not in logged_in.get("/").get_data(as_text=True)


# --- the "New" pill in the UI ---------------------------------------------

def test_an_unopened_lead_is_flagged_new(logged_in, account, lead_thread):
    assert "pill--new" in logged_in.get("/mail/leads").get_data(as_text=True)


def test_listing_leads_does_not_clear_the_pill(logged_in, account, lead_thread):
    """It marks the conversation, not the visit — so browsing past a row
    twice leaves it exactly as new as it was."""
    logged_in.get("/mail/leads")
    assert "pill--new" in logged_in.get("/mail/leads").get_data(as_text=True)


def test_opening_the_thread_clears_the_pill(logged_in, account, lead_thread):
    logged_in.get(f"/mail/threads/{lead_thread.id}")
    assert "pill--new" not in logged_in.get("/mail/leads").get_data(as_text=True)


def test_opening_one_thread_leaves_the_others_flagged(
    logged_in, company, account, lead_thread,
):
    add_lead(company, account, thread_id="t-2", sender="other@example.com")
    db.session.commit()

    logged_in.get(f"/mail/threads/{lead_thread.id}")
    body = logged_in.get("/mail/leads").get_data(as_text=True)
    assert body.count("pill--new") == 1


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


def test_the_client_email_tab_does_not_flag_new_threads(logged_in, client_record, thread):
    """`flag_new` is off there, so the shared list renders without any
    new/seen decoration rather than marking a client's whole history new."""
    body = logged_in.get(f"/clients/{client_record.id}/emails").get_data(as_text=True)
    assert "pill--new" not in body


def test_the_dismissed_view_does_not_flag_threads_as_new(
    logged_in, company, account, lead_thread,
):
    """Something you already triaged away is not "new", however unread it is."""
    email_service.dismiss_thread(company.id, lead_thread.id)
    body = logged_in.get("/mail/leads?show=dismissed").get_data(as_text=True)
    assert "pill--new" not in body
    assert "thread-list__item--new" not in body


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
    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-fresh", subject="Bespoke holster",
        messages=[fakes.message(sender="stranger@example.com")],
    )]):
        logged_in.post("/integrations/sync", data={
            "csrf_token": csrf, "return_to": "/mail/leads",
        })
    body = logged_in.get("/mail/leads").get_data(as_text=True)
    assert "Bespoke holster" in body
    assert "pill--new" in body  # nobody has opened it


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
