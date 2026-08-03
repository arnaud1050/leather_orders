"""
Unread mail from clients already on file.

The gap this fills: a *lead* arriving is loud — it lands in the lead inbox,
it's counted, it carries a pill. A **client** writing in was silent. Their
thread quietly updated on a page nobody had reason to open.

Counted per **message**, not per thread, which is the difference from every
other notification in this module: a client conversation stays alive for
years, so "they replied" has to register in a thread that's been opened
dozens of times. That's what `EmailMessage.read_at` is for.
"""

import pytest

from models import Client, User, db

from communications.models import (
    DISMISSED_HIDDEN, EmailMessage, EmailThread, utcnow,
)
from communications.services import email_service

from tests import fakes


def add_message(thread, message_id="m-new", direction="incoming",
                sender="marie@example.com", read=False):
    row = EmailMessage(
        thread_id=thread.id, provider_message_id=message_id, sender=sender,
        direction=direction, body_text="Any update?",
        read_at=utcnow() if read else None,
    )
    db.session.add(row)
    db.session.flush()
    return row


def client_thread(company, account, client, thread_id="t-c", messages=1):
    row = EmailThread(
        company_id=company.id, email_account_id=account.id,
        provider_thread_id=thread_id, subject="Briefcase",
        client_id=client.id, last_message_date=utcnow(),
    )
    db.session.add(row)
    db.session.flush()
    for index in range(messages):
        add_message(row, message_id=f"m-{thread_id}-{index}")
    return row


# --- what counts ----------------------------------------------------------

def test_nothing_unread_means_no_count(app, company, account, client_record):
    assert email_service.unread_client_mail_count(company.id) == 0


def test_an_unread_message_from_a_client_counts(app, company, account, client_record):
    client_thread(company, account, client_record)
    assert email_service.unread_client_mail_count(company.id) == 1


def test_every_unread_message_counts_not_every_thread(app, company, account, client_record):
    """Three replies in one conversation is three pieces of unread mail —
    "new convos + new emails in existing threads", as asked."""
    client_thread(company, account, client_record, messages=3)
    assert email_service.unread_client_mail_count(company.id) == 3


def test_several_threads_are_summed(app, company, account, client_record):
    client_thread(company, account, client_record, thread_id="t-1", messages=2)
    client_thread(company, account, client_record, thread_id="t-2", messages=1)
    assert email_service.unread_client_mail_count(company.id) == 3


def test_our_own_replies_never_count(app, company, account, client_record):
    """You can't have unread mail you sent yourself."""
    thread = client_thread(company, account, client_record, messages=0)
    add_message(thread, message_id="m-out", direction="outgoing",
                sender="studio@example.com")
    assert email_service.unread_client_mail_count(company.id) == 0


def test_a_lead_does_not_count_here(app, company, account, lead_thread):
    """Leads have their own badge. Someone not on file yet isn't a client
    waiting on a reply."""
    assert email_service.unread_client_mail_count(company.id) == 0


def test_a_read_message_stops_counting(app, company, account, client_record):
    thread = client_thread(company, account, client_record, messages=0)
    add_message(thread, read=True)
    assert email_service.unread_client_mail_count(company.id) == 0


def test_a_dismissed_thread_does_not_count(app, company, account, client_record):
    """Hiding a conversation means "don't tell me about this" — including
    when a sender rule hid it and the address also happens to be a client."""
    thread = client_thread(company, account, client_record)
    thread.dismiss(DISMISSED_HIDDEN)
    db.session.flush()
    assert email_service.unread_client_mail_count(company.id) == 0


def test_the_count_is_tenant_scoped(app, company, other_company, account, client_record):
    client_thread(company, account, client_record)
    assert email_service.unread_client_mail_count(other_company.id) == 0


def test_the_count_is_not_per_user(app, company, account, client_record):
    """Same rule as every other badge here: one studio, one inbox."""
    client_thread(company, account, client_record)
    assert email_service.unread_client_mail_count(company.id) == 1


# --- per client -----------------------------------------------------------

def test_counts_are_broken_down_by_client(app, company, account, client_record):
    second = Client(company_id=company.id, first_name="Ada", last_name="Lovelace",
                    email="ada@example.com")
    db.session.add(second)
    db.session.flush()

    client_thread(company, account, client_record, thread_id="t-1", messages=2)
    client_thread(company, account, second, thread_id="t-2", messages=1)

    counts = email_service.unread_counts_by_client(company.id)
    assert counts == {client_record.id: 2, second.id: 1}


def test_a_client_with_nothing_unread_is_absent(app, company, account, client_record):
    """Absent, not zero — the template renders no badge rather than a "0"."""
    assert email_service.unread_counts_by_client(company.id) == {}


def test_the_total_is_the_sum_of_the_breakdown(app, company, account, client_record):
    """The nav badge and the roster come from one query, so they can't
    disagree — same reasoning as the lead badge sharing its list's query."""
    second = Client(company_id=company.id, first_name="Ada", last_name="Lovelace",
                    email="ada@example.com")
    db.session.add(second)
    db.session.flush()
    client_thread(company, account, client_record, thread_id="t-1", messages=2)
    client_thread(company, account, second, thread_id="t-2", messages=3)

    counts = email_service.unread_counts_by_client(company.id)
    assert email_service.unread_client_mail_count(company.id) == sum(counts.values())


def test_the_breakdown_is_tenant_scoped(app, company, other_company, account,
                                        client_record):
    client_thread(company, account, client_record)
    assert email_service.unread_counts_by_client(other_company.id) == {}


# --- reading it -----------------------------------------------------------

def test_opening_the_thread_clears_it(app, company, account, client_record):
    thread = client_thread(company, account, client_record, messages=2)
    email_service.mark_thread_opened(company.id, thread.id)
    assert email_service.unread_client_mail_count(company.id) == 0


def test_a_reply_after_reading_counts_again(app, company, account, client_record):
    """The rule that needed per-message state. Under the old per-thread
    marker this conversation would have stayed silent forever after the
    first open."""
    thread = client_thread(company, account, client_record)
    email_service.mark_thread_opened(company.id, thread.id)
    assert email_service.unread_client_mail_count(company.id) == 0

    add_message(thread, message_id="m-later")
    assert email_service.unread_client_mail_count(company.id) == 1


def test_opening_one_thread_leaves_another_unread(app, company, account, client_record):
    first = client_thread(company, account, client_record, thread_id="t-1")
    client_thread(company, account, client_record, thread_id="t-2")

    email_service.mark_thread_opened(company.id, first.id)
    assert email_service.unread_client_mail_count(company.id) == 1


def test_the_new_pill_does_not_come_back_on_a_reply(app, company, account, lead_thread):
    """The two markers stay separate: `opened_at` is stamped once, so a
    reply makes mail unread without claiming nobody ever looked at the
    conversation."""
    email_service.mark_thread_opened(company.id, lead_thread.id)
    opened = lead_thread.opened_at

    add_message(lead_thread, message_id="m-reply")
    email_service.mark_thread_opened(company.id, lead_thread.id)

    assert lead_thread.opened_at == opened


def test_marking_another_tenants_thread_does_nothing(app, other_company, account,
                                                     client_record, company):
    thread = client_thread(company, account, client_record)
    email_service.mark_thread_opened(other_company.id, thread.id)
    assert email_service.unread_client_mail_count(company.id) == 1


def test_a_synced_reply_arrives_unread(app, company, account, client_record):
    """The path that actually matters: mail coming down the wire."""
    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-sync",
        messages=[fakes.message(message_id="m-sync", thread_id="t-sync",
                                sender=client_record.email)],
    )]):
        email_service.sync_now(company.id)

    assert email_service.unread_client_mail_count(company.id) == 1


# --- the backfill ---------------------------------------------------------

def test_the_backfill_marks_already_opened_threads_read(app, company, account,
                                                        client_record):
    """Adding per-message read state must not declare months-old mail
    unread. A thread carries `opened_at` because someone looked at it, so
    its messages were seen — and that timestamp is the honest answer to
    when."""
    from communications import migrations

    thread = client_thread(company, account, client_record, messages=2)
    thread.opened_at = utcnow()
    for message in thread.messages:
        message.read_at = None
    db.session.commit()

    migrations._backfill_read_messages()

    assert email_service.unread_client_mail_count(company.id) == 0
    assert all(m.read_at == thread.opened_at for m in thread.messages)


def test_the_backfill_leaves_unopened_threads_unread(app, company, account,
                                                     client_record):
    from communications import migrations

    client_thread(company, account, client_record)
    db.session.commit()

    migrations._backfill_read_messages()
    assert email_service.unread_client_mail_count(company.id) == 1


def test_the_backfill_is_a_no_op_the_second_time(app, company, account, client_record):
    """It runs on every boot, so it has to be."""
    from communications import migrations

    thread = client_thread(company, account, client_record)
    thread.opened_at = utcnow()
    for message in thread.messages:
        message.read_at = None
    db.session.commit()

    migrations._backfill_read_messages()
    stamps = [m.read_at for m in thread.messages]
    migrations._backfill_read_messages()
    assert [m.read_at for m in thread.messages] == stamps


# --- the UI ---------------------------------------------------------------

def test_the_badge_shows_in_the_top_nav_on_any_page(logged_in, company, account,
                                                    client_record):
    client_thread(company, account, client_record, messages=2)
    db.session.commit()

    body = logged_in.get("/").get_data(as_text=True)
    assert "nav-badge--mail" in body
    assert "2 unread emails from clients" in body


def test_the_badge_shows_on_the_clients_sub_nav(logged_in, company, account,
                                                client_record):
    client_thread(company, account, client_record)
    db.session.commit()

    body = logged_in.get("/clients").get_data(as_text=True)
    # Once in the top nav, once on the Clients sub-nav link, once in the row.
    assert body.count("nav-badge--mail") == 3


def test_the_badge_shows_next_to_the_client_in_the_table(logged_in, company, account,
                                                         client_record):
    client_thread(company, account, client_record, messages=2)
    db.session.commit()

    body = logged_in.get("/clients").get_data(as_text=True)
    assert f"2 unread emails from {client_record.name}" in body


def test_the_row_badge_is_not_a_link(logged_in, company, account, client_record):
    """The client's name beside it is already the link to their page; two
    targets a few pixels apart makes a worse row to click. Every badge in
    the app is a plain span."""
    client_thread(company, account, client_record)
    db.session.commit()

    body = logged_in.get("/clients").get_data(as_text=True)
    badge = body.index("nav-badge--mail", body.index("<tbody"))
    assert body.rfind("<", 0, badge) == body.rfind("<span", 0, badge)


def test_the_badge_shows_on_the_clients_emails_tab(logged_in, company, account,
                                                   client_record):
    """Narrowed to that client — the same callable as the nav badges, so the
    roster, the top nav and this tab are three views of one number."""
    client_thread(company, account, client_record, messages=2)
    db.session.commit()

    body = logged_in.get(f"/clients/{client_record.id}").get_data(as_text=True)
    assert "nav-badge--mail" in body
    assert 'title="2 unread emails"' in body


def test_the_emails_tab_badge_is_that_clients_count_only(logged_in, company, account,
                                                         client_record):
    other = Client(company_id=company.id, first_name="Ada", last_name="Lovelace",
                   email="ada@example.com")
    db.session.add(other)
    db.session.flush()
    client_thread(company, account, client_record, thread_id="t-1", messages=1)
    client_thread(company, account, other, thread_id="t-2", messages=4)
    db.session.commit()

    body = logged_in.get(f"/clients/{client_record.id}").get_data(as_text=True)
    assert 'title="1 unread email"' in body
    assert "4 unread email" not in body


def test_a_client_with_nothing_unread_gets_no_tab_badge(logged_in, client_record):
    body = logged_in.get(f"/clients/{client_record.id}").get_data(as_text=True)
    assert "nav-badge--mail" not in body


def test_the_emails_tab_badge_is_tenant_scoped(logged_in, other_company, company,
                                               account, client_record):
    """The count is filtered inside the tenant-scoped query, so a client id
    from another company reads as zero rather than as their mail."""
    assert email_service.unread_client_mail_count(
        other_company.id, client_record.id) == 0


def test_no_badge_is_rendered_at_zero(logged_in, account, client_record):
    assert "nav-badge--mail" not in logged_in.get("/clients").get_data(as_text=True)


def test_reading_the_thread_clears_the_badge(logged_in, company, account, client_record):
    thread = client_thread(company, account, client_record)
    db.session.commit()

    assert "nav-badge--mail" in logged_in.get("/").get_data(as_text=True)
    logged_in.get(f"/mail/threads/{thread.id}")
    assert "nav-badge--mail" not in logged_in.get("/").get_data(as_text=True)


def test_looking_at_the_client_list_does_not_clear_it(logged_in, company, account,
                                                      client_record):
    """Unlike the auto-created-client badge next to it, this one is work:
    seeing that someone wrote isn't the same as reading what they said."""
    client_thread(company, account, client_record)
    db.session.commit()

    logged_in.get("/clients")
    assert "nav-badge--mail" in logged_in.get("/").get_data(as_text=True)


def test_looking_at_the_client_page_does_not_clear_it(logged_in, company, account,
                                                      client_record):
    client_thread(company, account, client_record)
    db.session.commit()

    logged_in.get(f"/clients/{client_record.id}/emails")
    assert "nav-badge--mail" in logged_in.get("/").get_data(as_text=True)


def test_another_tenants_mail_is_not_shown(logged_in, other_company, account):
    from communications.models import EmailAccount

    theirs_account = EmailAccount(
        company_id=other_company.id, provider="gmail",
        email_address="theirs@example.com",
    )
    theirs_client = Client(company_id=other_company.id, first_name="Not",
                           last_name="Yours", email="not@yours.example.com")
    db.session.add_all([theirs_account, theirs_client])
    db.session.flush()
    client_thread(other_company, theirs_account, theirs_client)
    db.session.commit()

    assert "nav-badge--mail" not in logged_in.get("/").get_data(as_text=True)


def test_the_badge_survives_a_logged_out_page(app, account, client_record):
    db.session.commit()
    assert app.test_client().get("/login").status_code == 200


def test_a_second_user_sees_the_same_count(app, company, account, client_record):
    client_thread(company, account, client_record)
    second = User(company_id=company.id, username="colleague")
    second.set_password("changeme")
    db.session.add(second)
    db.session.commit()

    with app.test_client() as client:
        client.post("/login", data={"username": "colleague", "password": "changeme"},
                    follow_redirects=True)
        assert "nav-badge--mail" in client.get("/").get_data(as_text=True)


# --- the three badges on one link -----------------------------------------

def test_all_three_clients_badges_can_show_at_once(logged_in, company, account,
                                                   client_record, lead_thread):
    """They count different things and must stay distinguishable: an
    untriaged lead, a client the app added by itself, and unread mail."""
    from communications.models import AutoCreatedClient

    db.session.add(AutoCreatedClient(company_id=company.id, client_id=client_record.id))
    client_thread(company, account, client_record)
    db.session.commit()

    body = logged_in.get("/").get_data(as_text=True)
    assert "1 lead waiting" in body
    assert "1 client added automatically" in body
    assert "1 unread email from clients" in body


@pytest.mark.parametrize("path", ["/", "/clients", "/orders"])
def test_the_badge_is_visible_from_everywhere(logged_in, company, account,
                                              client_record, path):
    client_thread(company, account, client_record)
    db.session.commit()
    assert "nav-badge--mail" in logged_in.get(path).get_data(as_text=True)


def test_a_lead_and_client_mail_show_as_two_badges_at_once(
    logged_in, company, account, client_record, lead_thread,
):
    """Mail from someone on file and an untriaged enquiry are different
    work, so they get their own badge each — side by side on the same nav
    link, never merged into one total."""
    client_thread(company, account, client_record, messages=2)
    db.session.commit()

    body = logged_in.get("/").get_data(as_text=True)
    assert "1 lead waiting" in body
    assert "2 unread emails from clients" in body
    # A plain (grey) lead badge and a --mail (purple) one, not two of either.
    assert body.count('class="nav-badge"') == 1
    assert body.count("nav-badge--mail") == 1


def test_the_two_badges_keep_their_own_colours(logged_in, company, account,
                                               client_record, lead_thread):
    """Grey says undecided, purple says something happened. If the lead
    badge ever renders with a colour modifier, that distinction is gone."""
    client_thread(company, account, client_record)
    db.session.commit()

    body = logged_in.get("/").get_data(as_text=True)
    lead = body.index("lead waiting")
    lead_tag = body.rfind("<span", 0, lead)
    # The lead badge carries no modifier class — that's what makes it grey.
    assert "nav-badge--" not in body[lead_tag:lead]


def test_both_badges_survive_on_the_clients_sub_nav(logged_in, company, account,
                                                    client_record, lead_thread):
    """The sub-nav splits them by link: unread client mail sits on Clients,
    the waiting lead on Leads."""
    client_thread(company, account, client_record)
    db.session.commit()

    body = logged_in.get("/clients").get_data(as_text=True)
    assert "lead waiting" in body
    assert "unread email from clients" in body


# --- which conversation ---------------------------------------------------
#
# The badge on the Emails tab says a client wrote; with several threads open
# it doesn't say *which*. So each row carries its own count — the same
# purple, cleared the same way, and adding up to the badge above it.

def test_a_thread_reports_its_own_unread_count(app, company, account, client_record):
    thread = client_thread(company, account, client_record, messages=3)
    assert thread.unread_count == 3


def test_read_messages_leave_a_thread_at_zero(app, company, account, client_record):
    thread = client_thread(company, account, client_record, messages=0)
    add_message(thread, read=True)
    assert thread.unread_count == 0


def test_our_own_replies_do_not_mark_a_thread(app, company, account, client_record):
    thread = client_thread(company, account, client_record, messages=0)
    add_message(thread, message_id="m-out", direction="outgoing",
                sender="studio@example.com")
    assert thread.unread_count == 0


def test_a_dismissed_thread_reports_zero(app, company, account, client_record):
    """It has to agree with the badge, which excludes dismissed threads — a
    row marked unread while the nav says nothing would be worse than no row
    marker at all."""
    thread = client_thread(company, account, client_record)
    thread.dismiss(DISMISSED_HIDDEN)
    db.session.flush()
    assert thread.unread_count == 0


def test_the_rows_add_up_to_the_tab_badge(app, company, account, client_record):
    first = client_thread(company, account, client_record, thread_id="t-1", messages=2)
    second = client_thread(company, account, client_record, thread_id="t-2", messages=1)

    assert first.unread_count + second.unread_count == \
        email_service.unread_client_mail_count(company.id, client_record.id)


def test_the_emails_tab_marks_the_thread_that_has_unread_mail(
    logged_in, company, account, client_record,
):
    client_thread(company, account, client_record, thread_id="t-1", messages=2)
    quiet = client_thread(company, account, client_record, thread_id="t-2", messages=0)
    add_message(quiet, message_id="m-old", read=True)
    db.session.commit()

    body = logged_in.get(f"/clients/{client_record.id}/emails").get_data(as_text=True)
    # One row marked, not both — the whole point of a per-thread marker.
    assert body.count("thread-list__item--new") == 1
    assert "2 new" in body


def test_the_count_is_shown_not_just_the_word_new(logged_in, company, account,
                                                   client_record):
    """Four unread replies is a different amount of reading from one."""
    client_thread(company, account, client_record, messages=4)
    db.session.commit()

    body = logged_in.get(f"/clients/{client_record.id}/emails").get_data(as_text=True)
    assert "4 new" in body


def test_opening_the_conversation_clears_its_marker(logged_in, company, account,
                                                     client_record):
    thread = client_thread(company, account, client_record)
    db.session.commit()

    logged_in.get(f"/mail/threads/{thread.id}")

    body = logged_in.get(f"/clients/{client_record.id}/emails").get_data(as_text=True)
    assert "thread-list__item--new" not in body


def test_a_reply_marks_a_thread_that_was_already_read(logged_in, company, account,
                                                       client_record):
    """The case the tab's own "New" pill could never cover: this
    conversation has plainly been opened, and they wrote again anyway."""
    thread = client_thread(company, account, client_record)
    db.session.commit()
    logged_in.get(f"/mail/threads/{thread.id}")

    add_message(thread, message_id="m-later")
    db.session.commit()

    body = logged_in.get(f"/clients/{client_record.id}/emails").get_data(as_text=True)
    assert "1 new" in body


def test_a_client_with_nothing_unread_gets_no_row_markers(logged_in, company, account,
                                                           client_record):
    thread = client_thread(company, account, client_record, messages=0)
    add_message(thread, read=True)
    db.session.commit()

    body = logged_in.get(f"/clients/{client_record.id}/emails").get_data(as_text=True)
    assert "thread-list__item--new" not in body
    assert "pill--new" not in body


def test_the_lead_inbox_still_says_new_not_a_count(logged_in, company, lead_thread):
    """Two questions, two markers (N-7). The lead inbox asks whether anyone
    has ever opened the conversation, and answers it with a word."""
    db.session.commit()

    body = logged_in.get("/mail/leads").get_data(as_text=True)
    assert ">New</span>" in body
    assert "1 new" not in body
