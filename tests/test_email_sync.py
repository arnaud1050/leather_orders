"""
Mailbox sync: storage, idempotence, client association, failure handling.

The two properties worth protecting here are the ones the module's
docstring claims: running a sync twice produces the same rows, and a thread
can only ever match a client of the mailbox's own company.
"""

from datetime import timedelta

from models import Client, db

from communications.models import (
    EmailAccount, EmailAttachment, EmailMessage, EmailSyncSettings,
    EmailThread, utcnow,
)
from communications.providers.base import ProviderError, ReauthorizationRequired
from communications.sync import email_sync

from tests import fakes


def test_stores_threads_and_messages(account, client_record):
    with fakes.fake_providers(threads=[fakes.thread()]):
        result = email_sync.sync_account(account)

    assert result.ok
    assert result.threads_created == 1
    assert result.messages_created == 1
    stored = EmailThread.query.one()
    assert stored.provider_thread_id == "t-1"
    assert stored.subject == "Briefcase timeline"
    assert stored.messages[0].body_text == "Any update?"
    assert stored.company_id == account.company_id


def test_matches_a_thread_to_a_client_by_address(account, client_record):
    with fakes.fake_providers(threads=[fakes.thread()]):
        result = email_sync.sync_account(account)

    assert result.threads_matched == 1
    assert EmailThread.query.one().client_id == client_record.id


def test_address_matching_is_case_insensitive(account, client_record):
    incoming = fakes.thread(messages=[fakes.message(sender="MARIE@Example.COM")])
    with fakes.fake_providers(threads=[incoming]):
        email_sync.sync_account(account)
    assert EmailThread.query.one().client_id == client_record.id


def test_unknown_sender_is_stored_unmatched(account, client_record):
    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-lead", messages=[fakes.message(sender="stranger@example.com")],
    )]):
        result = email_sync.sync_account(account)

    assert result.threads_matched == 0
    assert EmailThread.query.one().client_id is None


def test_unknown_sender_is_discarded_when_keep_unmatched_is_off(account, client_record):
    settings = EmailSyncSettings.for_company(account.company_id)
    settings.keep_unmatched = False
    db.session.flush()

    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-lead", messages=[fakes.message(sender="stranger@example.com")],
    )]):
        result = email_sync.sync_account(account)

    assert result.threads_skipped == 1
    assert EmailThread.query.count() == 0


def test_turning_keep_unmatched_off_does_not_delete_existing_threads(
    account, client_record, lead_thread,
):
    """The setting gates new threads only — flipping it must not silently
    destroy history someone may be reading."""
    settings = EmailSyncSettings.for_company(account.company_id)
    settings.keep_unmatched = False
    db.session.flush()

    with fakes.fake_providers(threads=[]):
        email_sync.sync_account(account)

    assert db.session.get(EmailThread, lead_thread.id) is not None


def test_the_mailboxs_own_address_never_matches(account, company):
    """A studio that has itself on file as a client would otherwise match
    every single thread to itself."""
    db.session.add(Client(
        company_id=company.id, first_name="By", last_name="Monsieur",
        email="studio@example.com",
    ))
    db.session.flush()

    with fakes.fake_providers(threads=[fakes.thread(messages=[
        fakes.message(sender="studio@example.com", recipients=["studio@example.com"]),
    ])]):
        email_sync.sync_account(account)

    assert EmailThread.query.one().client_id is None


def test_outgoing_message_matches_the_recipient(account, client_record):
    """An outgoing message to a new client is just as much evidence of who
    the thread is with as an incoming one from them."""
    with fakes.fake_providers(threads=[fakes.thread(messages=[fakes.message(
        sender="studio@example.com", recipients=["marie@example.com"],
        direction="outgoing",
    )])]):
        email_sync.sync_account(account)

    assert EmailThread.query.one().client_id == client_record.id


# --- idempotence ----------------------------------------------------------

def test_running_the_same_sync_twice_creates_nothing_new(account, client_record):
    with fakes.fake_providers(threads=[fakes.thread()]):
        email_sync.sync_account(account)
        account.last_sync_at = None  # force the identical window again
        db.session.flush()
        second = email_sync.sync_account(account)

    assert second.threads_created == 0
    assert second.messages_created == 0
    assert EmailThread.query.count() == 1
    assert EmailMessage.query.count() == 1


def test_a_new_message_in_a_known_thread_is_appended(account, client_record):
    with fakes.fake_providers(threads=[fakes.thread()]):
        email_sync.sync_account(account)

    grown = fakes.thread(messages=[
        fakes.message(message_id="m-1"),
        fakes.message(message_id="m-2", body_text="Following up", direction="outgoing"),
    ])
    with fakes.fake_providers(threads=[grown]):
        result = email_sync.sync_account(account)

    assert result.threads_created == 0
    assert result.messages_created == 1
    assert EmailThread.query.one().message_count == 2


def test_a_hand_linked_client_is_not_cleared_by_a_later_sync(account, company):
    """Matching only ever adds. A thread linked by hand has a client the
    address index may not know about."""
    with fakes.fake_providers(threads=[fakes.thread(
        messages=[fakes.message(sender="stranger@example.com")],
    )]):
        email_sync.sync_account(account)

    stored = EmailThread.query.one()
    manual = Client(company_id=company.id, first_name="Jean", last_name="Tremblay")
    db.session.add(manual)
    db.session.flush()
    stored.client_id = manual.id
    db.session.flush()

    account.last_sync_at = None
    with fakes.fake_providers(threads=[fakes.thread(
        messages=[fakes.message(sender="stranger@example.com")],
    )]):
        email_sync.sync_account(account)

    assert EmailThread.query.one().client_id == manual.id


# --- sync window ----------------------------------------------------------

def test_first_sync_reaches_back_initial_sync_days(account):
    settings = EmailSyncSettings.for_company(account.company_id)
    settings.initial_sync_days = 30
    account.last_sync_at = None
    db.session.flush()

    start = email_sync._window_start(account, settings)
    assert abs((utcnow() - start).days - 30) <= 1


def test_incremental_sync_overlaps_the_previous_run(account):
    """Overlap costs a few upserts that become no-ops; a gap loses mail."""
    settings = EmailSyncSettings.for_company(account.company_id)
    account.last_sync_at = utcnow() - timedelta(hours=2)
    start = email_sync._window_start(account, settings)
    assert start < account.last_sync_at
    assert account.last_sync_at - start == timedelta(hours=1)


def test_sync_records_the_time_and_clears_a_previous_error(account, client_record):
    account.last_sync_error = "something went wrong earlier"
    with fakes.fake_providers(threads=[fakes.thread()]):
        email_sync.sync_account(account)
    assert account.last_sync_error is None
    assert account.last_sync_at is not None


def test_sent_mail_setting_is_passed_to_the_provider(account):
    settings = EmailSyncSettings.for_company(account.company_id)
    settings.sync_sent_mail = False
    db.session.flush()

    with fakes.fake_providers(threads=[]):
        email_sync.sync_account(account)
        assert fakes.FETCH_LOG[-1]["include_sent"] is False


def test_sent_mail_is_included_by_default(account):
    with fakes.fake_providers(threads=[]):
        email_sync.sync_account(account)
        assert fakes.FETCH_LOG[-1]["include_sent"] is True


def test_fetch_is_capped_so_a_busy_first_sync_cannot_run_away(account):
    from communications import config

    with fakes.fake_providers(threads=[]):
        email_sync.sync_account(account)
        assert fakes.FETCH_LOG[-1]["limit"] == config.MAX_MESSAGES_PER_SYNC


# --- failure handling -----------------------------------------------------

def test_provider_failure_is_recorded_not_raised(account):
    with fakes.fake_providers(email_error=ProviderError("Gmail is down")):
        result = email_sync.sync_account(account)

    assert not result.ok
    assert "Gmail is down" in result.error
    assert "Gmail is down" in account.last_sync_error


def test_revoked_grant_is_recorded_on_the_account(account):
    """A mailbox that quietly stopped syncing is the worst outcome, so the
    error lands where the integrations page shows it."""
    with fakes.fake_providers(email_error=ReauthorizationRequired("grant revoked")):
        result = email_sync.sync_account(account)

    assert not result.ok
    assert "revoked" in account.last_sync_error
    assert account.status_label == "error"


def test_a_failed_sync_stores_nothing(account, client_record):
    with fakes.fake_providers(email_error=ProviderError("boom")):
        email_sync.sync_account(account)
    assert EmailThread.query.count() == 0


# --- attachments ----------------------------------------------------------

def test_attachment_metadata_is_stored_without_downloading_by_default(account, client_record):
    incoming = fakes.thread(messages=[
        fakes.message(attachments=[fakes.attachment()]),
    ])
    with fakes.fake_providers(threads=[incoming]):
        email_sync.sync_account(account)

    row = EmailAttachment.query.one()
    assert row.filename == "mockup.pdf"
    assert row.size_bytes == 2048
    assert row.is_downloaded is False        # bytes are opt-in
    assert EmailMessage.query.one().has_attachments is True


def test_attachments_are_downloaded_when_enabled(account, client_record, tmp_path, monkeypatch):
    from communications import config

    monkeypatch.setattr(config, "ATTACHMENT_DIR", str(tmp_path))
    settings = EmailSyncSettings.for_company(account.company_id)
    settings.sync_attachments = True
    db.session.flush()

    incoming = fakes.thread(messages=[fakes.message(attachments=[fakes.attachment()])])
    with fakes.fake_providers(threads=[incoming]):
        result = email_sync.sync_account(account)

    assert result.attachments_saved == 1
    row = EmailAttachment.query.one()
    assert row.is_downloaded is True
    assert row.stored_filename != "mockup.pdf"  # generated, not sender-supplied


def test_one_unreadable_attachment_does_not_lose_its_message(
    account, client_record, tmp_path, monkeypatch,
):
    from communications import config
    from communications.sync import email_sync as module

    monkeypatch.setattr(config, "ATTACHMENT_DIR", str(tmp_path))
    settings = EmailSyncSettings.for_company(account.company_id)
    settings.sync_attachments = True
    db.session.flush()

    class Exploding(fakes.FakeEmailProvider):
        def fetch_attachment(self, message_id, attachment_id):
            raise ProviderError("attachment gone")

    incoming = fakes.thread(messages=[fakes.message(attachments=[fakes.attachment()])])
    with fakes.fake_providers(threads=[incoming]):
        module.email_provider_for = Exploding
        result = email_sync.sync_account(account)

    assert result.ok
    assert EmailMessage.query.count() == 1
    assert EmailAttachment.query.one().is_downloaded is False
    assert any("attachment gone" in error for error in result.errors)


# --- match_client / rematch ----------------------------------------------

def test_match_client_returns_none_for_an_empty_index():
    assert email_sync.match_client(["a@example.com"], {}, set()) is None


def test_match_client_skips_blank_addresses(client_record, account):
    index = email_sync._client_index(client_record.company_id)
    assert email_sync.match_client(["", None, "marie@example.com"], index, set()) is not None


def test_client_index_is_scoped_to_one_company(company, other_company, client_record):
    db.session.add(Client(
        company_id=other_company.id, first_name="Someone", last_name="Else",
        email="theirs@example.com",
    ))
    db.session.flush()

    index = email_sync._client_index(company.id)
    assert "marie@example.com" in index
    assert "theirs@example.com" not in index


def test_client_index_ignores_clients_with_no_email(company):
    db.session.add(Client(
        company_id=company.id, first_name="No", last_name="Email", email="",
    ))
    db.session.flush()
    assert "" not in email_sync._client_index(company.id)


def test_rematch_links_threads_that_predate_the_client(company, account, lead_thread):
    """Someone who emailed before being added as a client should attach
    without waiting for them to send another message."""
    db.session.add(Client(
        company_id=company.id, first_name="Jean", last_name="Tremblay",
        email="stranger@example.com",
    ))
    db.session.flush()

    assert email_sync.rematch_unassigned(company.id) == 1
    assert db.session.get(EmailThread, lead_thread.id).client_id is not None


def test_rematch_does_not_reach_across_tenants(company, other_company, account, lead_thread):
    db.session.add(Client(
        company_id=other_company.id, first_name="Jean", last_name="Tremblay",
        email="stranger@example.com",
    ))
    db.session.flush()

    assert email_sync.rematch_unassigned(company.id) == 0
    assert db.session.get(EmailThread, lead_thread.id).client_id is None


def test_rematch_with_no_clients_is_a_noop(other_company):
    assert email_sync.rematch_unassigned(other_company.id) == 0


def test_rematch_ignores_the_mailboxs_own_address(company, account, lead_thread):
    db.session.add(Client(
        company_id=company.id, first_name="By", last_name="Monsieur",
        email="studio@example.com",
    ))
    db.session.flush()
    assert email_sync.rematch_unassigned(company.id) == 0


# --- result reporting -----------------------------------------------------

def test_summary_of_a_successful_run(account, client_record):
    with fakes.fake_providers(threads=[fakes.thread()]):
        result = email_sync.sync_account(account)
    summary = result.summary()
    assert "studio@example.com" in summary and "1 new message" in summary


def test_summary_of_a_failed_run(account):
    with fakes.fake_providers(email_error=ProviderError("nope")):
        result = email_sync.sync_account(account)
    assert result.summary() == "studio@example.com: nope"
