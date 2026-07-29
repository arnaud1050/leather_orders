"""Model behaviour: token properties, derived fields, constraints."""

from datetime import timedelta

import pytest
import sqlalchemy as sa

from models import db

from communications.models import (
    EmailAccount, EmailAttachment, EmailMessage, EmailSyncSettings,
    EmailThread, utcnow,
)

from tests.conftest import ALL_SCOPES, MAIL_ONLY_SCOPES


# --- EmailAccount: tokens -------------------------------------------------

def test_tokens_are_stored_encrypted(account):
    """The column must not hold the plaintext, or the encryption is theatre."""
    assert account.access_token_encrypted != "access-token"
    assert "access-token" not in account.access_token_encrypted
    assert account.access_token == "access-token"
    assert account.refresh_token == "refresh-token"


def test_tokens_survive_a_database_round_trip(account):
    db.session.commit()
    db.session.expire_all()
    reloaded = db.session.get(EmailAccount, account.id)
    assert reloaded.access_token == "access-token"


def test_blank_refresh_token_does_not_erase_the_stored_one(account):
    """Google only issues a refresh token on first consent. A re-auth that
    omits it must not wipe the one we hold, or the account silently stops
    refreshing and nobody finds out until a send fails."""
    original = account.refresh_token_encrypted
    account.refresh_token = None
    assert account.refresh_token_encrypted == original
    account.refresh_token = ""
    assert account.refresh_token_encrypted == original
    account.refresh_token = "brand-new"
    assert account.refresh_token == "brand-new"


# --- EmailAccount: derived state ------------------------------------------

def test_is_expired_without_an_expiry(account):
    """Unknown expiry means refresh before use, not assume it's fine."""
    account.token_expiry = None
    assert account.is_expired is True


def test_is_expired_for_past_and_future(account):
    account.token_expiry = utcnow() - timedelta(minutes=5)
    assert account.is_expired is True
    account.token_expiry = utcnow() + timedelta(hours=1)
    assert account.is_expired is False


def test_has_scope_reads_what_was_granted(account):
    assert account.has_scope("https://www.googleapis.com/auth/calendar") is True
    account.granted_scopes = MAIL_ONLY_SCOPES
    assert account.has_scope("https://www.googleapis.com/auth/calendar") is False
    assert account.has_scope("https://www.googleapis.com/auth/gmail.send") is True


def test_scopes_of_an_account_that_granted_nothing(account):
    account.granted_scopes = None
    assert account.scopes == []
    assert account.has_scope("anything") is False


@pytest.mark.parametrize("sync_enabled,error,expected", [
    (True, None, "connected"),
    (False, None, "paused"),
    (True, "invalid_grant", "error"),
    (False, "invalid_grant", "error"),  # an error outranks paused
])
def test_status_label(account, sync_enabled, error, expected):
    account.sync_enabled = sync_enabled
    account.last_sync_error = error
    assert account.status_label == expected


def test_duplicate_account_for_a_company_is_rejected(company, account):
    """The unique constraint is what stops a double connect creating two
    rows that then both sync the same mailbox."""
    duplicate = EmailAccount(
        company_id=company.id, provider="gmail",
        email_address=account.email_address,
    )
    db.session.add(duplicate)
    with pytest.raises(sa.exc.IntegrityError):
        db.session.flush()
    db.session.rollback()


def test_same_address_under_two_companies_is_allowed(company, other_company, account):
    """Two studios could legitimately connect the same shared mailbox; the
    constraint is per company, not global."""
    db.session.add(EmailAccount(
        company_id=other_company.id, provider="gmail",
        email_address=account.email_address,
    ))
    db.session.flush()  # must not raise


# --- EmailSyncSettings ----------------------------------------------------

def test_for_company_creates_defaults_once(company):
    first = EmailSyncSettings.for_company(company.id)
    assert first.sync_frequency == 15
    assert first.initial_sync_days == 90
    assert first.keep_unmatched is True
    assert EmailSyncSettings.for_company(company.id).id == first.id


def test_settings_are_per_tenant(company, other_company):
    mine = EmailSyncSettings.for_company(company.id)
    mine.initial_sync_days = 7
    db.session.flush()
    assert EmailSyncSettings.for_company(other_company.id).initial_sync_days == 90


# --- EmailThread ----------------------------------------------------------

def test_is_lead_tracks_the_client_link(thread, lead_thread):
    assert thread.is_lead is False
    assert lead_thread.is_lead is True


def test_counterparty_prefers_the_incoming_sender(thread):
    """It's the human's address the lead form pre-fills, not ours."""
    assert thread.counterparty == "marie@example.com"


def test_counterparty_falls_back_to_the_recipient_when_we_started_it(company, account):
    row = EmailThread(
        company_id=company.id, email_account_id=account.id,
        provider_thread_id="t-out", subject="Following up",
    )
    db.session.add(row)
    db.session.flush()
    db.session.add(EmailMessage(
        thread_id=row.id, provider_message_id="m-out",
        sender="studio@example.com", recipients="prospect@example.com",
        direction="outgoing",
    ))
    db.session.flush()
    assert row.counterparty == "prospect@example.com"


def test_counterparty_of_an_empty_thread(company, account):
    row = EmailThread(
        company_id=company.id, email_account_id=account.id, provider_thread_id="t-x",
    )
    db.session.add(row)
    db.session.flush()
    assert row.counterparty is None


def test_display_subject_falls_back(company, account):
    row = EmailThread(
        company_id=company.id, email_account_id=account.id,
        provider_thread_id="t-nosub", subject=None,
    )
    assert row.display_subject == "(no subject)"


def test_latest_message_is_the_most_recent(thread):
    db.session.add(EmailMessage(
        thread_id=thread.id, provider_message_id="m-2",
        sender="studio@example.com", body_text="Friday.",
        received_date=utcnow() + timedelta(hours=1), direction="outgoing",
    ))
    db.session.flush()
    db.session.refresh(thread)
    assert thread.latest_message.provider_message_id == "m-2"
    assert thread.message_count == 2


def test_deleting_an_account_removes_its_threads_and_messages(account, thread):
    """"Disconnect" that leaves a copy of someone's mail behind isn't a
    disconnect — the cascade is the privacy guarantee."""
    thread_id = thread.id
    db.session.delete(account)
    db.session.flush()
    assert db.session.get(EmailThread, thread_id) is None
    assert EmailMessage.query.filter_by(thread_id=thread_id).count() == 0


def test_duplicate_provider_thread_id_per_account_is_rejected(company, account, thread):
    """This constraint is what makes re-running a sync idempotent."""
    db.session.add(EmailThread(
        company_id=company.id, email_account_id=account.id,
        provider_thread_id=thread.provider_thread_id,
    ))
    with pytest.raises(sa.exc.IntegrityError):
        db.session.flush()
    db.session.rollback()


# --- EmailMessage ---------------------------------------------------------

def test_recipient_list_splits_and_trims(thread):
    message = thread.messages[0]
    message.recipients = "a@example.com,  b@example.com , "
    assert message.recipient_list == ["a@example.com", "b@example.com"]


def test_recipient_list_when_empty(thread):
    thread.messages[0].recipients = None
    assert thread.messages[0].recipient_list == []


@pytest.mark.parametrize("name,address,expected", [
    ("Marie Alarie", "marie@example.com", "Marie Alarie <marie@example.com>"),
    (None, "marie@example.com", "marie@example.com"),
    ("Marie Alarie", None, "Marie Alarie"),
    (None, None, "(unknown sender)"),
])
def test_sender_display(thread, name, address, expected):
    message = thread.messages[0]
    message.sender_name, message.sender = name, address
    assert message.sender_display == expected


def test_preview_collapses_whitespace_and_truncates(thread):
    message = thread.messages[0]
    message.body_text = "line one\n\n   line two"
    assert message.preview == "line one line two"
    message.body_text = "x" * 300
    assert len(message.preview) == 141 and message.preview.endswith("…")


def test_preview_of_a_body_less_message(thread):
    thread.messages[0].body_text = None
    assert thread.messages[0].preview == ""


def test_is_incoming(thread):
    assert thread.messages[0].is_incoming is True
    thread.messages[0].direction = "outgoing"
    assert thread.messages[0].is_incoming is False


# --- EmailAttachment ------------------------------------------------------

def test_is_downloaded_tracks_stored_bytes(thread):
    row = EmailAttachment(
        message_id=thread.messages[0].id, filename="mockup.pdf", size_bytes=2048,
    )
    db.session.add(row)
    db.session.flush()
    assert row.is_downloaded is False
    row.stored_filename = "abc123.pdf"
    assert row.is_downloaded is True


@pytest.mark.parametrize("size,expected", [
    (None, "0 B"), (512, "512 B"), (2048, "2.0 KB"), (5 * 1024 * 1024, "5.0 MB"),
])
def test_size_display(size, expected):
    assert EmailAttachment(size_bytes=size).size_display == expected


# --- CalendarEvent --------------------------------------------------------

def test_is_cancelled(company, account):
    from communications.models import CalendarEvent

    row = CalendarEvent(
        company_id=company.id, email_account_id=account.id,
        provider_event_id="e-1", status="confirmed",
    )
    assert row.is_cancelled is False
    row.status = "cancelled"
    assert row.is_cancelled is True


# --- timestamps -----------------------------------------------------------

def test_utcnow_is_naive(account):
    """A tz-aware value in a SQLite DateTime column compares wrong against
    the naive ones around it rather than failing — so it's pinned."""
    assert utcnow().tzinfo is None
    assert account.created_at.tzinfo is None
