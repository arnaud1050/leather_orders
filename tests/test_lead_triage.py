"""
Triaging a lead out of the inbox: hide, restore, move to Trash.

The rules worth protecting, in order of how much damage getting them wrong
would do:

1. Nothing here can permanently delete mail. Trash only.
2. A hidden thread stays hidden across syncs — but the row is kept, so
   "hide" can't be silently undone by the next download.
3. A dismissed sender writing again brings the thread back, unless it was
   trashed.
"""

from datetime import timedelta

import pytest

from models import db

from communications import config
from communications.models import (
    AUDIT_THREAD_TRASHED, DISMISSED_HIDDEN, DISMISSED_TRASHED, AuditLog,
    EmailAccount, EmailThread, utcnow,
)
from communications.providers.base import ProviderError
from communications.services import email_service
from communications.sync import email_sync

from tests import fakes


# --- hiding ---------------------------------------------------------------

def test_hiding_removes_a_lead_from_the_inbox(app, company, account, lead_thread):
    email_service.dismiss_thread(company.id, lead_thread.id)

    assert email_service.lead_threads(company.id) == []
    assert [t.id for t in email_service.dismissed_lead_threads(company.id)] == [lead_thread.id]


def test_hiding_keeps_the_row(app, company, account, lead_thread):
    """Deleting it would only mean the next sync downloads the thread again
    and it reappears — a hide button that looks broken."""
    email_service.dismiss_thread(company.id, lead_thread.id)
    assert db.session.get(EmailThread, lead_thread.id) is not None


def test_hiding_does_not_touch_the_mailbox(app, company, account, lead_thread):
    with fakes.fake_providers():
        email_service.dismiss_thread(company.id, lead_thread.id)
    assert fakes.TRASH_LOG == []


def test_hiding_records_the_reason(app, company, account, lead_thread):
    thread = email_service.dismiss_thread(company.id, lead_thread.id)
    assert thread.dismissed_reason == DISMISSED_HIDDEN
    assert thread.is_dismissed is True
    assert thread.was_trashed is False


def test_hiding_lowers_the_badge(app, company, account, lead_thread):
    """Hiding is one of the three things that resolve a lead, so it's one of
    the three things that clear the count."""
    assert email_service.pending_lead_count(company.id) == 1
    email_service.dismiss_thread(company.id, lead_thread.id)
    assert email_service.pending_lead_count(company.id) == 0


def test_a_hidden_thread_stays_hidden_across_a_sync(app, company, account, lead_thread):
    email_service.dismiss_thread(company.id, lead_thread.id)
    account.last_sync_at = None
    db.session.commit()

    # The same thread and the same message come back down the wire.
    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-lead", messages=[fakes.message(
            message_id="m-lead", thread_id="t-lead", sender="stranger@example.com",
        )],
    )]):
        email_sync.sync_account(account)

    assert email_service.lead_threads(company.id) == []


def test_restoring_puts_it_back(app, company, account, lead_thread):
    email_service.dismiss_thread(company.id, lead_thread.id)
    email_service.restore_thread(company.id, lead_thread.id)

    assert [t.id for t in email_service.lead_threads(company.id)] == [lead_thread.id]
    assert email_service.dismissed_lead_threads(company.id) == []


def test_hiding_another_tenants_thread_is_refused(app, other_company, lead_thread):
    with pytest.raises(email_service.EmailServiceError, match="no longer exists"):
        email_service.dismiss_thread(other_company.id, lead_thread.id)
    assert db.session.get(EmailThread, lead_thread.id).is_dismissed is False


# --- trashing -------------------------------------------------------------

def test_trashing_calls_the_provider_and_dismisses_locally(app, company, account, lead_thread):
    with fakes.fake_providers():
        thread = email_service.trash_thread(company.id, lead_thread.id)

    assert fakes.TRASH_LOG == ["t-lead"]
    assert thread.dismissed_reason == DISMISSED_TRASHED
    assert email_service.lead_threads(company.id) == []


def test_trashing_records_an_audit_entry(app, company, account, lead_thread):
    with fakes.fake_providers():
        email_service.trash_thread(company.id, lead_thread.id)

    entry = AuditLog.query.filter_by(event=AUDIT_THREAD_TRASHED).one()
    assert "Messenger bag enquiry" in entry.detail
    assert "30 days" in entry.detail  # says it's recoverable


def test_nothing_is_dismissed_locally_if_the_provider_refuses(
    app, company, account, lead_thread,
):
    """The provider call goes first, so the app never claims to have trashed
    mail that's still sitting in the inbox."""
    with fakes.fake_providers(email_error=ProviderError("Gmail said no")):
        with pytest.raises(ProviderError):
            email_service.trash_thread(company.id, lead_thread.id)

    assert db.session.get(EmailThread, lead_thread.id).is_dismissed is False
    assert email_service.dismissed_lead_threads(company.id) == []


def test_a_trashed_thread_cannot_be_restored_from_here(app, company, account, lead_thread):
    """The mail is in Gmail's Trash; restoring it here would show it in Leads
    again while the message stayed trashed — a button that appears to work
    and doesn't."""
    with fakes.fake_providers():
        email_service.trash_thread(company.id, lead_thread.id)

    with pytest.raises(email_service.EmailServiceError, match="Gmail's Trash"):
        email_service.restore_thread(company.id, lead_thread.id)


def test_trashing_a_thread_whose_mailbox_is_gone(app, company, account, lead_thread):
    thread_id = lead_thread.id
    db.session.delete(account)
    db.session.commit()
    # The cascade takes the thread with the account, so this is really
    # asserting the lookup fails cleanly rather than 500ing.
    with pytest.raises(email_service.EmailServiceError):
        email_service.trash_thread(company.id, thread_id)


def test_trashing_another_tenants_thread_is_refused(app, other_company, account, lead_thread):
    with fakes.fake_providers():
        with pytest.raises(email_service.EmailServiceError, match="no longer exists"):
            email_service.trash_thread(other_company.id, lead_thread.id)
    assert fakes.TRASH_LOG == []


def test_the_provider_interface_offers_no_permanent_delete():
    """The module holds no scope that can destroy mail irreversibly, and the
    interface shouldn't imply otherwise. If a `delete_thread` ever appears
    here, the scope question has to be revisited first."""
    from communications.providers.base import EmailProvider

    assert hasattr(EmailProvider, "trash_thread")
    assert not hasattr(EmailProvider, "delete_thread")


def test_gmail_uses_trash_not_delete():
    """`threads().delete()` would need https://mail.google.com/, which config
    deliberately never requests.

    Read off the AST rather than the source text — the method's own docstring
    names `threads().delete()` to explain why it isn't used, and a substring
    check can't tell the prose from the code.
    """
    import ast
    import inspect
    import textwrap

    from communications.providers.gmail_provider import GmailProvider

    tree = ast.parse(textwrap.dedent(inspect.getsource(GmailProvider.trash_thread)))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "trash" in called
    assert "delete" not in called


def test_no_scope_capable_of_permanent_deletion_is_requested():
    """The guarantee behind all of the above: even a bug in this module can't
    destroy mail irreversibly, because the app never holds the scope for it."""
    from communications import config

    assert "https://mail.google.com/" not in config.GOOGLE_SCOPES
    assert not any(scope.endswith("gmail.settings.basic") for scope in config.GOOGLE_SCOPES)


# --- re-surfacing ---------------------------------------------------------

def test_a_hidden_sender_writing_again_brings_the_thread_back(
    app, company, account, lead_thread,
):
    """Someone following up is new signal — an enquiry hidden by mistake gets
    a second chance rather than going silent forever."""
    email_service.dismiss_thread(company.id, lead_thread.id)
    account.last_sync_at = None
    db.session.commit()

    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-lead", messages=[
            fakes.message(message_id="m-lead", thread_id="t-lead",
                          sender="stranger@example.com"),
            fakes.message(message_id="m-lead-2", thread_id="t-lead",
                          sender="stranger@example.com", body_text="Following up!"),
        ],
    )]):
        result = email_sync.sync_account(account)

    assert result.threads_resurfaced == 1
    assert [t.id for t in email_service.lead_threads(company.id)] == [lead_thread.id]


def test_resurfacing_shows_in_the_sync_summary(app, company, account, lead_thread):
    email_service.dismiss_thread(company.id, lead_thread.id)
    account.last_sync_at = None
    db.session.commit()

    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-lead", messages=[
            fakes.message(message_id="m-lead", thread_id="t-lead"),
            fakes.message(message_id="m-new", thread_id="t-lead"),
        ],
    )]):
        result = email_sync.sync_account(account)

    assert "1 dismissed reopened" in result.summary()


def test_an_outgoing_message_does_not_resurface_a_hidden_thread(
    app, company, account, lead_thread,
):
    """Us mailing them isn't them writing back."""
    email_service.dismiss_thread(company.id, lead_thread.id)
    account.last_sync_at = None
    db.session.commit()

    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-lead", messages=[
            fakes.message(message_id="m-lead", thread_id="t-lead"),
            fakes.message(message_id="m-out", thread_id="t-lead",
                          sender="studio@example.com", direction="outgoing"),
        ],
    )]):
        result = email_sync.sync_account(account)

    assert result.threads_resurfaced == 0
    assert email_service.lead_threads(company.id) == []


def test_a_trashed_thread_is_not_resurfaced_by_new_mail(app, company, account, lead_thread):
    """Un-hiding something the user explicitly threw away would be the wrong
    way to be wrong."""
    with fakes.fake_providers():
        email_service.trash_thread(company.id, lead_thread.id)
    account.last_sync_at = None
    db.session.commit()

    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-lead", messages=[
            fakes.message(message_id="m-lead", thread_id="t-lead"),
            fakes.message(message_id="m-new", thread_id="t-lead"),
        ],
    )]):
        result = email_sync.sync_account(account)

    assert result.threads_resurfaced == 0
    assert email_service.lead_threads(company.id) == []


def test_resyncing_the_same_window_does_not_resurface_repeatedly(
    app, company, account, lead_thread,
):
    """Only genuinely new messages count — _store_message returns early on
    one it has already stored."""
    email_service.dismiss_thread(company.id, lead_thread.id)

    for _ in range(2):
        account.last_sync_at = None
        db.session.commit()
        with fakes.fake_providers(threads=[fakes.thread(
            thread_id="t-lead",
            messages=[fakes.message(message_id="m-lead", thread_id="t-lead")],
        )]):
            result = email_sync.sync_account(account)
        assert result.threads_resurfaced == 0

    assert email_service.lead_threads(company.id) == []


# --- recovering from Trash ------------------------------------------------
#
# Un-trashing happens in Gmail and produces no new message, so nothing in
# the normal sync flow would ever notice it: every query carries -in:trash,
# and the window is keyed on message dates that recovering doesn't change.
# _recover_untrashed asks about the threads we trashed ourselves — which is
# what makes the promise the UI already makes actually true.

def test_recovering_a_thread_in_gmail_brings_it_back(app, company, account, lead_thread):
    with fakes.fake_providers():
        email_service.trash_thread(company.id, lead_thread.id)

    with fakes.fake_providers(trash_state={"t-lead": False}):
        result = email_sync.sync_account(account)

    assert result.threads_recovered == 1
    assert [t.id for t in email_service.lead_threads(company.id)] == [lead_thread.id]
    assert email_service.dismissed_lead_threads(company.id) == []


def test_a_recovered_thread_is_no_longer_marked_trashed(app, company, account, lead_thread):
    """So the row offers Hide/Trash again rather than the "recover it in
    Gmail" note — it really is back in the inbox, in Gmail and here."""
    with fakes.fake_providers():
        email_service.trash_thread(company.id, lead_thread.id)
    with fakes.fake_providers(trash_state={"t-lead": False}):
        email_sync.sync_account(account)

    thread = db.session.get(EmailThread, lead_thread.id)
    assert thread.is_dismissed is False
    assert thread.was_trashed is False
    assert thread.dismissed_reason is None


def test_a_thread_still_in_trash_stays_dismissed(app, company, account, lead_thread):
    with fakes.fake_providers():
        email_service.trash_thread(company.id, lead_thread.id)

    with fakes.fake_providers(trash_state={"t-lead": True}):
        result = email_sync.sync_account(account)

    assert result.threads_recovered == 0
    assert email_service.lead_threads(company.id) == []


def test_a_purged_thread_stays_dismissed(app, company, account, lead_thread):
    """`None` is "can't tell" — a conversation Gmail has purged has nothing to
    recover, and reading that as "recovered" would put mail the user binned a
    month ago back in the lead inbox."""
    with fakes.fake_providers():
        email_service.trash_thread(company.id, lead_thread.id)

    with fakes.fake_providers(trash_state={"t-lead": None}):
        result = email_sync.sync_account(account)

    assert result.threads_recovered == 0
    assert email_service.lead_threads(company.id) == []


def test_recovery_shows_in_the_sync_summary(app, company, account, lead_thread):
    with fakes.fake_providers():
        email_service.trash_thread(company.id, lead_thread.id)
    with fakes.fake_providers(trash_state={"t-lead": False}):
        result = email_sync.sync_account(account)

    assert "1 recovered from Trash" in result.summary()


def test_a_recovered_lead_counts_again(app, company, account, lead_thread):
    """It's back in the inbox, so it's back in the badge — the count is a
    query over the same list, not a separate tally that could disagree."""
    with fakes.fake_providers():
        email_service.trash_thread(company.id, lead_thread.id)
    assert email_service.pending_lead_count(company.id) == 0

    with fakes.fake_providers(trash_state={"t-lead": False}):
        email_sync.sync_account(account)

    assert email_service.pending_lead_count(company.id) == 1


def test_hidden_threads_are_never_checked_against_the_provider(
    app, company, account, lead_thread,
):
    """Hiding is local by definition — asking Gmail about it would be a
    pointless API call, and a `False` answer would silently undo the hide."""
    email_service.dismiss_thread(company.id, lead_thread.id)

    with fakes.fake_providers(trash_state={"t-lead": False}):
        result = email_sync.sync_account(account)

    assert fakes.TRASH_CHECK_LOG == []
    assert result.threads_recovered == 0
    assert email_service.lead_threads(company.id) == []


def test_threads_trashed_long_ago_are_not_checked(app, company, account, lead_thread):
    """Gmail purges its own Trash after 30 days. Past that there's nothing to
    recover, and without a cutoff this costs one API call per thread ever
    trashed, on every sync, forever."""
    with fakes.fake_providers():
        email_service.trash_thread(company.id, lead_thread.id)
    lead_thread.dismissed_at = utcnow() - timedelta(
        days=config.TRASH_RECOVERY_WINDOW_DAYS + 1,
    )
    db.session.commit()

    with fakes.fake_providers(trash_state={"t-lead": False}):
        result = email_sync.sync_account(account)

    assert fakes.TRASH_CHECK_LOG == []
    assert result.threads_recovered == 0


def test_another_accounts_trashed_thread_is_not_checked(app, company, account, lead_thread):
    """The pass runs per mailbox, inside that mailbox's own sync — asking one
    account's provider about another's thread id would 404 at best."""
    second = EmailAccount(
        company_id=company.id, provider="gmail", email_address="other@example.com",
    )
    db.session.add(second)
    db.session.commit()
    with fakes.fake_providers():
        email_service.trash_thread(company.id, lead_thread.id)

    with fakes.fake_providers(trash_state={"t-lead": False}):
        email_sync.sync_account(second)

    assert fakes.TRASH_CHECK_LOG == []
    assert db.session.get(EmailThread, lead_thread.id).was_trashed is True


def test_a_failed_trash_check_does_not_fail_the_sync(app, company, account, lead_thread):
    """Mail that downloaded fine must still be stored — the same rule as an
    attachment that wouldn't download."""
    with fakes.fake_providers():
        email_service.trash_thread(company.id, lead_thread.id)
    account.last_sync_at = None
    db.session.commit()

    with fakes.fake_providers(
        threads=[fakes.thread(thread_id="t-new", messages=[fakes.message(
            message_id="m-new", thread_id="t-new", sender="someone@example.com",
        )])],
        trash_check_error=ProviderError("Gmail hiccuped"),
    ):
        result = email_sync.sync_account(account)

    assert result.ok is True
    assert result.messages_created == 1
    assert result.threads_recovered == 0
    assert any("Gmail hiccuped" in error for error in result.errors)


def test_the_dismissed_view_promises_what_the_sync_now_delivers(
    logged_in, company, account, lead_thread,
):
    """The page has always said a recovered thread comes back on the next
    sync. This is the test that keeps that from being a lie."""
    with fakes.fake_providers():
        email_service.trash_thread(company.id, lead_thread.id)
    # Collapsed: the sentence wraps in the template source.
    body = " ".join(logged_in.get("/mail/leads?show=dismissed").get_data(as_text=True).split())
    assert "come back on the next sync if you recover them there" in body

    with fakes.fake_providers(trash_state={"t-lead": False}):
        email_sync.sync_account(account)

    assert "Messenger bag enquiry" in logged_in.get("/mail/leads").get_data(as_text=True)


# --- routes ---------------------------------------------------------------

def test_hide_button_hides(logged_in, csrf, company, account, lead_thread):
    response = logged_in.post(f"/mail/threads/{lead_thread.id}/dismiss",
                              data={"csrf_token": csrf, "return_to": "/mail/leads"})
    assert response.status_code == 302
    assert db.session.get(EmailThread, lead_thread.id).is_dismissed is True


def test_trash_button_trashes(logged_in, csrf, company, account, lead_thread):
    with fakes.fake_providers():
        response = logged_in.post(f"/mail/threads/{lead_thread.id}/trash",
                                  data={"csrf_token": csrf, "return_to": "/mail/leads"})
    assert response.status_code == 302
    assert fakes.TRASH_LOG == ["t-lead"]


def test_restore_button_restores(logged_in, csrf, company, account, lead_thread):
    email_service.dismiss_thread(company.id, lead_thread.id)
    logged_in.post(f"/mail/threads/{lead_thread.id}/restore",
                   data={"csrf_token": csrf, "return_to": "/mail/leads"})
    assert db.session.get(EmailThread, lead_thread.id).is_dismissed is False


@pytest.mark.parametrize("action", ["dismiss", "restore", "trash"])
def test_triage_routes_require_a_csrf_token(logged_in, lead_thread, action):
    assert logged_in.post(f"/mail/threads/{lead_thread.id}/{action}").status_code == 400


@pytest.mark.parametrize("action", ["dismiss", "restore", "trash"])
def test_triage_routes_require_a_login(app, lead_thread, action):
    db.session.commit()
    response = app.test_client().post(f"/mail/threads/{lead_thread.id}/{action}")
    assert response.status_code in (302, 400)


def test_a_provider_failure_on_trash_is_reported_not_raised(
    logged_in, csrf, company, account, lead_thread,
):
    with fakes.fake_providers(email_error=ProviderError("Gmail said no")):
        response = logged_in.post(f"/mail/threads/{lead_thread.id}/trash",
                                  data={"csrf_token": csrf, "return_to": "/mail/leads"})
    assert response.status_code == 302
    assert "Gmail said no" in logged_in.get("/mail/leads").get_data(as_text=True)


# --- the leads page -------------------------------------------------------

def test_the_lead_list_offers_hide_and_trash(logged_in, account, lead_thread):
    body = logged_in.get("/mail/leads").get_data(as_text=True)
    assert ">Hide<" in body
    assert ">Move to Trash<" in body


def test_the_trash_button_is_confirmed(logged_in, account, lead_thread):
    """It changes the studio's real mailbox, unlike Hide."""
    body = logged_in.get("/mail/leads").get_data(as_text=True)
    assert "onsubmit=\"return confirm(" in body


def test_hidden_threads_are_not_in_the_default_list(logged_in, company, account, lead_thread):
    email_service.dismiss_thread(company.id, lead_thread.id)
    body = logged_in.get("/mail/leads").get_data(as_text=True)
    assert "Messenger bag enquiry" not in body
    assert "Show 1 dismissed" in body


def test_the_dismissed_view_lists_them(logged_in, company, account, lead_thread):
    email_service.dismiss_thread(company.id, lead_thread.id)
    body = logged_in.get("/mail/leads?show=dismissed").get_data(as_text=True)
    assert "Messenger bag enquiry" in body
    assert ">Restore<" in body


def test_the_dismissed_view_explains_a_trashed_thread_instead_of_offering_restore(
    logged_in, company, account, lead_thread,
):
    with fakes.fake_providers():
        email_service.trash_thread(company.id, lead_thread.id)
    body = logged_in.get("/mail/leads?show=dismissed").get_data(as_text=True)
    assert "In Gmail's Trash" in body
    assert ">Restore<" not in body


def test_no_dismissed_link_when_there_is_nothing_dismissed(logged_in, account, lead_thread):
    assert "dismissed" not in logged_in.get("/mail/leads").get_data(as_text=True)


def test_the_dismissed_view_does_not_clear_the_badge(
    logged_in, company, account, lead_thread,
):
    """Nothing about looking at a list clears the badge any more, but this is
    the case that would be worst to get wrong: the dismissed view is where
    someone goes precisely when they have *not* dealt with the inbox."""
    logged_in.get("/mail/leads?show=dismissed")
    assert email_service.pending_lead_count(company.id) == 1


def test_the_dismissed_view_does_not_flag_threads_as_new(
    logged_in, company, account, lead_thread,
):
    """Something you already triaged away is not "new". The dismissed view
    passes a null cutoff *and* wants no flagging, which is why the list takes
    an explicit flag rather than inferring it from the timestamp."""
    email_service.dismiss_thread(company.id, lead_thread.id)
    body = logged_in.get("/mail/leads?show=dismissed").get_data(as_text=True)
    assert "pill--new" not in body
    assert "thread-list__item--new" not in body


def test_return_to_has_no_stray_question_mark(logged_in, account, lead_thread):
    """Flask's request.full_path appends a bare "?" with no query string, which
    would otherwise be baked into every link and form on the page."""
    body = logged_in.get("/mail/leads").get_data(as_text=True)
    assert "/mail/leads?" not in body


def test_the_dismissed_view_keeps_its_query_string_in_return_to(
    logged_in, company, account, lead_thread,
):
    """Restoring from the dismissed view has to come back to it, not to the
    default inbox."""
    email_service.dismiss_thread(company.id, lead_thread.id)
    body = logged_in.get("/mail/leads?show=dismissed").get_data(as_text=True)
    assert "/mail/leads?show=dismissed" in body


def test_the_client_email_tab_has_no_triage_buttons(logged_in, client_record, thread):
    """Hiding a client's own correspondence isn't a thing anyone wants."""
    body = logged_in.get(f"/clients/{client_record.id}/emails").get_data(as_text=True)
    assert ">Hide<" not in body
    assert ">Move to Trash<" not in body
