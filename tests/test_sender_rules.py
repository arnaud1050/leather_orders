"""
Automatic handling of mail from known senders.

Two actions with opposite risk profiles, which is why they're tested
differently:

- **Hide** is cheap to get wrong: the thread is kept and restorable. The
  rules worth pinning are that it doesn't resurface (the whole point) and
  that it never reaches back through history.
- **Convert** writes a row into the client roster without anyone asking.
  So: only on new threads, only on incoming mail, never twice, and always
  recorded — both in the audit log and as a badge somebody has to see.
"""

import pytest

from models import Client, db

from communications.models import (
    AUDIT_CLIENT_AUTO_CREATED, AUDIT_SENDER_RULE_CHANGED, DISMISSED_AUTO,
    RULE_CONVERT, RULE_HIDE, AuditLog, AutoCreatedClient, EmailThread,
    SenderRule,
)
from communications.services import email_service, sender_rules
from communications.sync import email_sync

from tests import fakes


FORM = "form-submission@squarespace.info"


def rule(company, pattern=FORM, action=RULE_CONVERT):
    return sender_rules.add_rule(company.id, pattern, action)


def incoming(company, account, sender=FORM, thread_id="t-auto", subject="Enquiry"):
    """Sync one fresh incoming thread from `sender`."""
    with fakes.fake_providers(threads=[fakes.thread(
        thread_id=thread_id, subject=subject,
        messages=[fakes.message(
            message_id=f"m-{thread_id}", thread_id=thread_id, sender=sender,
        )],
    )]):
        return email_sync.sync_account(account)


def stored(thread_id="t-auto"):
    return EmailThread.query.filter_by(provider_thread_id=thread_id).one()


# --- pattern parsing ------------------------------------------------------

@pytest.mark.parametrize("typed, expected", [
    ("Form@Example.COM", "form@example.com"),
    ("  form@example.com  ", "form@example.com"),
    ("Squarespace <form-submission@squarespace.info>", FORM),
    ("@example.com", "@example.com"),
    ("example.com", "@example.com"),          # a bare domain means the domain
    ("Example.COM", "@example.com"),
])
def test_patterns_are_normalised(app, company, typed, expected):
    assert rule(company, typed, RULE_HIDE).pattern == expected


@pytest.mark.parametrize("bad", ["", "   ", "not an address", "@", "@nodot", "a@b"])
def test_unusable_patterns_are_refused(app, company, bad):
    """A pattern that silently matches nothing is a rule that appears to be
    working, which is worse than an error message."""
    with pytest.raises(sender_rules.SenderRuleError):
        rule(company, bad, RULE_HIDE)


def test_an_unknown_action_is_refused(app, company):
    with pytest.raises(sender_rules.SenderRuleError):
        sender_rules.add_rule(company.id, FORM, "delete_everything")


def test_a_duplicate_rule_is_refused(app, company):
    rule(company, FORM, RULE_HIDE)
    with pytest.raises(sender_rules.SenderRuleError, match="already a rule"):
        rule(company, FORM, RULE_CONVERT)


# --- matching -------------------------------------------------------------

def test_an_exact_address_matches(app, company):
    rules = [rule(company, FORM, RULE_HIDE)]
    assert sender_rules.match(rules, FORM) is not None
    assert sender_rules.match(rules, "someone@squarespace.info") is None


def test_a_domain_rule_matches_anything_from_it(app, company):
    rules = [rule(company, "@squarespace.info", RULE_HIDE)]
    assert sender_rules.match(rules, FORM) is not None
    assert sender_rules.match(rules, "hello@squarespace.info.example.com") is None


def test_an_exact_address_beats_a_domain_rule(app, company):
    """So "hide everything from this provider *except* the contact form" is
    expressible. Without the precedence the specific rule is unreachable, and
    a week of enquiries goes quietly into the dismissed pile."""
    rule(company, "@squarespace.info", RULE_HIDE)
    rule(company, FORM, RULE_CONVERT)
    rules = sender_rules.rules_for(company.id)

    assert sender_rules.match(rules, FORM).action == RULE_CONVERT
    assert sender_rules.match(rules, "marketing@squarespace.info").action == RULE_HIDE


def test_no_rules_match_nothing(app, company):
    assert sender_rules.match([], FORM) is None


def test_rules_are_tenant_scoped(app, company, other_company):
    rule(company, FORM, RULE_HIDE)
    assert sender_rules.rules_for(other_company.id) == []


# --- hiding ---------------------------------------------------------------

def test_a_hide_rule_keeps_mail_out_of_the_lead_inbox(app, company, account):
    rule(company, FORM, RULE_HIDE)
    result = incoming(company, account)

    assert result.threads_auto_hidden == 1
    assert email_service.lead_threads(company.id) == []
    assert stored().dismissed_reason == DISMISSED_AUTO


def test_an_auto_hidden_thread_is_still_stored_and_restorable(app, company, account):
    """Kept, not discarded: a rule added by mistake shouldn't destroy mail,
    and it shows up in the dismissed view like anything else."""
    rule(company, FORM, RULE_HIDE)
    incoming(company, account)

    thread = stored()
    assert [t.id for t in email_service.dismissed_lead_threads(company.id)] == [thread.id]
    email_service.restore_thread(company.id, thread.id)
    assert [t.id for t in email_service.lead_threads(company.id)] == [thread.id]


def test_an_auto_hidden_sender_writing_again_does_not_resurface_it(
    app, company, account,
):
    """The difference between a rule and a hand-hidden thread. A newsletter
    arrives every week; resurfacing it every week is a rule that appears not
    to work."""
    rule(company, FORM, RULE_HIDE)
    incoming(company, account)
    account.last_sync_at = None
    db.session.commit()

    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-auto", messages=[
            fakes.message(message_id="m-t-auto", thread_id="t-auto", sender=FORM),
            fakes.message(message_id="m-week-2", thread_id="t-auto", sender=FORM),
        ],
    )]):
        result = email_sync.sync_account(account)

    assert result.threads_resurfaced == 0
    assert email_service.lead_threads(company.id) == []


def test_a_hand_hidden_sender_still_resurfaces(app, company, account, lead_thread):
    """The existing behaviour, unchanged — only rule-hidden threads are
    exempt."""
    email_service.dismiss_thread(company.id, lead_thread.id)
    account.last_sync_at = None
    db.session.commit()

    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-lead", messages=[
            fakes.message(message_id="m-lead", thread_id="t-lead"),
            fakes.message(message_id="m-again", thread_id="t-lead"),
        ],
    )]):
        result = email_sync.sync_account(account)

    assert result.threads_resurfaced == 1


def test_hiding_does_not_touch_the_real_mailbox(app, company, account):
    """"Hide" is local, whether a person or a rule did it. Reaching into
    someone's Gmail on a rule they typed once would be a much bigger promise
    than this feature makes."""
    rule(company, FORM, RULE_HIDE)
    incoming(company, account)
    assert fakes.TRASH_LOG == []


# --- converting -----------------------------------------------------------

def test_a_convert_rule_creates_a_client(app, company, account):
    rule(company, FORM, RULE_CONVERT)
    result = incoming(company, account)

    assert result.clients_auto_created == 1
    client = Client.query.filter_by(company_id=company.id, email=FORM).one()
    assert stored().client_id == client.id


def test_the_converted_thread_leaves_the_lead_inbox(app, company, account):
    rule(company, FORM, RULE_CONVERT)
    incoming(company, account)
    assert email_service.lead_threads(company.id) == []


def test_the_first_message_is_kept_on_the_client(app, company, account):
    """Same field the contact-form webhook fills, so a lead that arrived by
    either route reads the same on the client page."""
    rule(company, FORM, RULE_CONVERT)
    incoming(company, account)
    assert Client.query.filter_by(email=FORM).one().first_message == "Any update?"


def test_conversion_is_recorded_in_the_audit_log(app, company, account):
    """Nobody chose this, so there has to be a record of what did."""
    rule(company, FORM, RULE_CONVERT)
    incoming(company, account)

    entry = AuditLog.query.filter_by(event=AUDIT_CLIENT_AUTO_CREATED).one()
    assert FORM in entry.detail


def test_an_existing_client_is_reused_not_duplicated(app, company, account):
    db.session.add(Client(
        company_id=company.id, first_name="Form", last_name="Submissions",
        email=FORM,
    ))
    db.session.commit()
    rule(company, FORM, RULE_CONVERT)
    incoming(company, account)

    assert Client.query.filter_by(company_id=company.id, email=FORM).count() == 1


def test_a_thread_already_matched_to_a_client_is_left_alone(
    app, company, account, client_record,
):
    """Ordinary matching got there first; the rule has nothing to add."""
    rule(company, client_record.email, RULE_CONVERT)
    result = incoming(company, account, sender=client_record.email)

    assert result.clients_auto_created == 0
    assert Client.query.filter_by(company_id=company.id).count() == 1


def test_outgoing_mail_never_triggers_a_rule(app, company, account):
    """Us mailing that address is not that address writing to us."""
    rule(company, FORM, RULE_CONVERT)
    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-out", messages=[fakes.message(
            message_id="m-out", thread_id="t-out", sender="studio@example.com",
            recipients=(FORM,), direction="outgoing",
        )],
    )]):
        result = email_sync.sync_account(account)

    assert result.clients_auto_created == 0
    assert Client.query.filter_by(company_id=company.id).count() == 0


# --- when rules apply -----------------------------------------------------

def test_rules_do_not_reach_back_through_existing_mail(app, company, account, lead_thread):
    """Adding a rule is an instruction about future mail. Retroactively
    converting or hiding history someone may be reading is not something a
    checkbox should do — same reasoning as keep_unmatched."""
    rule(company, lead_thread.messages[0].sender, RULE_HIDE)
    account.last_sync_at = None
    db.session.commit()

    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-lead",
        messages=[fakes.message(message_id="m-lead", thread_id="t-lead")],
    )]):
        result = email_sync.sync_account(account)

    assert result.threads_auto_hidden == 0
    assert [t.id for t in email_service.lead_threads(company.id)] == [lead_thread.id]


def test_resyncing_does_not_convert_twice(app, company, account):
    """The window overlaps by an hour by design, so this runs constantly."""
    rule(company, FORM, RULE_CONVERT)
    incoming(company, account)
    account.last_sync_at = None
    db.session.commit()
    result = incoming(company, account)

    assert result.clients_auto_created == 0
    assert Client.query.filter_by(company_id=company.id, email=FORM).count() == 1


def test_removing_a_rule_leaves_what_it_already_did(app, company, account):
    """A rule is an instruction for future mail, not a historical answer
    other rows depend on — so deleting it is safe, and deliberately doesn't
    unwind anything."""
    created = rule(company, FORM, RULE_CONVERT)
    incoming(company, account)
    sender_rules.delete_rule(company.id, created.id)

    assert Client.query.filter_by(company_id=company.id, email=FORM).count() == 1
    assert stored().client_id is not None


def test_a_removed_rule_stops_applying(app, company, account):
    created = rule(company, FORM, RULE_HIDE)
    sender_rules.delete_rule(company.id, created.id)
    result = incoming(company, account)

    assert result.threads_auto_hidden == 0
    assert len(email_service.lead_threads(company.id)) == 1


def test_another_tenants_rule_does_not_apply(app, company, other_company, account):
    sender_rules.add_rule(other_company.id, FORM, RULE_HIDE)
    result = incoming(company, account)

    assert result.threads_auto_hidden == 0
    assert len(email_service.lead_threads(company.id)) == 1


def test_deleting_another_tenants_rule_is_refused(app, company, other_company):
    created = sender_rules.add_rule(other_company.id, FORM, RULE_HIDE)
    with pytest.raises(sender_rules.SenderRuleError):
        sender_rules.delete_rule(company.id, created.id)


def test_rule_changes_are_audited(app, company):
    created = rule(company, FORM, RULE_HIDE)
    sender_rules.delete_rule(company.id, created.id)

    details = [e.detail for e in AuditLog.query.filter_by(
        event=AUDIT_SENDER_RULE_CHANGED).all()]
    assert len(details) == 2
    assert any("Added" in d for d in details)
    assert any("Removed" in d for d in details)


def test_the_sync_summary_reports_what_rules_did(app, company, account):
    rule(company, FORM, RULE_CONVERT)
    rule(company, "@newsletter.example.com", RULE_HIDE)
    incoming(company, account)
    account.last_sync_at = None
    db.session.commit()
    summary = incoming(
        company, account, sender="weekly@newsletter.example.com", thread_id="t-news",
    ).summary()

    assert "1 hidden by a rule" in summary


# --- the new-clients badge ------------------------------------------------
#
# N-10a is what shapes this whole section. A conversion *always* arrives
# with exactly one unread message — the enquiry that caused it — so the
# unread-mail badge is already announcing the same client, on the same nav
# link, in the same purple. This badge stands down for those, which means
# the tests below have to put the mail out of the way to see it at all.

def announced_alone(company, account):
    """A rule-created client the mail badge is silent about.

    Dismissing the conversation is the way that happens: it's how "don't
    tell me about this" is spelled for mail (N-24), and it's the only one
    that doesn't also acknowledge the client — `mark_thread_opened` does
    both, deliberately (N-10a).
    """
    rule(company, FORM, RULE_CONVERT)
    incoming(company, account)
    email_service.dismiss_thread(company.id, stored().id)


def test_a_conversion_raises_one_purple_badge_not_two(app, company, account):
    """The bug N-10a fixes: one contact-form submission used to light both
    purple badges on Clients, each reading "1", for one event."""
    rule(company, FORM, RULE_CONVERT)
    incoming(company, account)

    assert email_service.unread_client_mail_count(company.id) == 1
    assert sender_rules.unseen_client_count(company.id) == 0


def test_the_clients_link_carries_a_single_purple_badge(logged_in, company, account):
    rule(company, FORM, RULE_CONVERT)
    incoming(company, account)
    body = logged_in.get("/").get_data(as_text=True)

    assert "nav-badge--mail" in body
    assert "nav-badge--new" not in body


def test_reading_the_enquiry_does_not_summon_the_other_badge(
    logged_in, company, account,
):
    """The half that makes suppression safe. Without acknowledging on open,
    this badge would *appear* the moment the mail badge cleared — a notice
    arriving after the thing it announces has been dealt with."""
    rule(company, FORM, RULE_CONVERT)
    incoming(company, account)
    logged_in.get(f"/mail/threads/{stored().id}")

    body = logged_in.get("/").get_data(as_text=True)
    assert "nav-badge--mail" not in body
    assert "nav-badge--new" not in body
    assert AutoCreatedClient.query.filter_by(company_id=company.id).one().seen_at


def test_reading_the_enquiry_keeps_the_record(app, company, account):
    """Acknowledged, not deleted — N-12 holds for this clearing rule too."""
    rule(company, FORM, RULE_CONVERT)
    incoming(company, account)
    email_service.mark_thread_opened(company.id, stored().id)

    row = AutoCreatedClient.query.filter_by(company_id=company.id).one()
    assert row.seen_at is not None
    assert row.client_id is not None


def test_a_dismissed_enquiry_still_announces_the_client(app, company, account):
    """What the badge is still for. Hiding the conversation silences the
    mail badge (N-24) — but a client did appear on the roster, and nothing
    else would say so."""
    announced_alone(company, account)

    assert email_service.unread_client_mail_count(company.id) == 0
    assert sender_rules.unseen_client_count(company.id) == 1


def test_a_hand_converted_client_does_not_raise_the_badge(
    app, company, account, lead_thread,
):
    """You already know about a client you just created — the badge is for
    the ones that appeared while nobody was looking."""
    email_service.create_client_from_thread(company.id, lead_thread.id)
    assert sender_rules.unseen_client_count(company.id) == 0


def test_acknowledging_clears_the_badge(app, company, account):
    announced_alone(company, account)
    sender_rules.acknowledge_all(company.id)
    assert sender_rules.unseen_client_count(company.id) == 0


def test_acknowledging_keeps_the_record(app, company, account):
    """The row says this client arrived automatically, which stays true once
    the badge is gone."""
    announced_alone(company, account)
    sender_rules.acknowledge_all(company.id)

    row = AutoCreatedClient.query.filter_by(company_id=company.id).one()
    assert row.seen_at is not None
    assert row.client_id is not None


def test_the_badge_is_tenant_scoped(app, company, other_company, account):
    announced_alone(company, account)
    assert sender_rules.unseen_client_count(other_company.id) == 0


def test_the_badge_shows_on_the_clients_link(logged_in, company, account):
    announced_alone(company, account)
    body = logged_in.get("/").get_data(as_text=True)
    assert "nav-badge--new" in body
    assert "1 client added automatically" in body


def test_opening_the_client_list_clears_the_badge(logged_in, company, account):
    announced_alone(company, account)
    logged_in.get("/clients")
    assert "nav-badge--new" not in logged_in.get("/").get_data(as_text=True)


def test_the_lead_badge_and_the_new_client_badge_are_different_things(
    logged_in, company, account, lead_thread,
):
    """Both sit on the same Clients link, and they must not be conflated: one
    counts work outstanding, the other announces something that happened."""
    announced_alone(company, account)

    body = logged_in.get("/").get_data(as_text=True)
    assert "1 lead waiting" in body            # the untouched lead_thread
    assert "1 client added automatically" in body

    logged_in.get("/clients")
    after = logged_in.get("/").get_data(as_text=True)
    assert "1 lead waiting" in after           # still outstanding
    assert "nav-badge--new" not in after       # seen


# --- the settings UI ------------------------------------------------------

def test_the_integrations_page_lists_the_rules(logged_in, company):
    rule(company, FORM, RULE_CONVERT)
    rule(company, "@newsletter.example.com", RULE_HIDE)
    body = logged_in.get("/settings/integrations").get_data(as_text=True)
    assert FORM in body
    assert "@newsletter.example.com" in body


def test_a_rule_deletes_via_a_delete_button_not_remove(logged_in, company):
    # Matches the app-wide delete convention (trash icon, or a "Delete"
    # button) — this page used to read "Remove" for sender rules.
    rule(company, "@newsletter.example.com", RULE_HIDE)
    body = logged_in.get("/settings/integrations").get_data(as_text=True)
    assert ">Remove<" not in body
    assert ">Delete<" in body


def test_adding_a_rule_from_the_page(logged_in, csrf, company):
    logged_in.post("/integrations/rules", data={
        "csrf_token": csrf, "pattern": FORM, "action": RULE_CONVERT,
        "note": "website contact form",
    })
    created = SenderRule.query.filter_by(company_id=company.id).one()
    assert created.pattern == FORM
    assert created.note == "website contact form"


def test_a_bad_pattern_is_reported_not_raised(logged_in, csrf, company):
    response = logged_in.post("/integrations/rules", data={
        "csrf_token": csrf, "pattern": "nonsense", "action": RULE_HIDE,
    })
    assert response.status_code == 302
    assert "isn&#39;t a valid" in logged_in.get(
        "/settings/integrations").get_data(as_text=True)


def test_removing_a_rule_from_the_page(logged_in, csrf, company):
    created = rule(company, FORM, RULE_HIDE)
    logged_in.post(f"/integrations/rules/{created.id}/delete", data={"csrf_token": csrf})
    assert SenderRule.query.count() == 0


@pytest.mark.parametrize("path", ["/integrations/rules", "/integrations/rules/1/delete"])
def test_rule_routes_require_a_csrf_token(logged_in, path):
    assert logged_in.post(path).status_code == 400


@pytest.mark.parametrize("path", ["/integrations/rules", "/integrations/rules/1/delete"])
def test_rule_routes_require_a_login(app, path):
    assert app.test_client().post(path).status_code in (302, 400)
