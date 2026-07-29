"""Attachment storage on disk, and the background job entry points."""

import os
from datetime import timedelta

import pytest

from models import db

from communications import config, jobs
from communications.models import EmailAccount, EmailSyncSettings, EmailThread, utcnow
from communications.providers.base import ReauthorizationRequired
from communications.storage import attachment_storage

from tests import fakes


# --- attachment storage ---------------------------------------------------

@pytest.fixture
def storage_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ATTACHMENT_DIR", str(tmp_path))
    return tmp_path


def test_save_and_read_back(storage_dir):
    name = attachment_storage.save(1, "mockup.pdf", b"pdf-bytes")
    path = attachment_storage.path_for(1, name)
    assert path is not None
    with open(path, "rb") as handle:
        assert handle.read() == b"pdf-bytes"


def test_stored_name_is_generated_not_the_senders(storage_dir):
    """The sender's filename is display-only metadata. Using it on disk is
    how you get a file written outside the directory."""
    name = attachment_storage.save(1, "mockup.pdf", b"x")
    assert name != "mockup.pdf"
    assert name.endswith(".pdf")  # extension kept so it opens sensibly


def test_a_traversal_filename_cannot_escape_the_directory(storage_dir):
    name = attachment_storage.save(1, "../../../../etc/passwd", b"x")
    assert ".." not in name
    assert os.sep not in name and "/" not in name
    path = attachment_storage.path_for(1, name)
    assert str(storage_dir) in path


def test_path_for_rejects_a_traversal_in_a_stored_name(storage_dir):
    """A row is data, and a path built from data gets validated before it
    opens a file — even though save() generates the name."""
    assert attachment_storage.path_for(1, "../../secrets.txt") is None


def test_path_for_of_a_missing_file(storage_dir):
    assert attachment_storage.path_for(1, "nothing-here.pdf") is None


@pytest.mark.parametrize("empty", [None, ""])
def test_path_for_of_an_empty_name(storage_dir, empty):
    assert attachment_storage.path_for(1, empty) is None


def test_companies_get_separate_directories(storage_dir):
    """The same isolation the database rows have, applied to the filesystem."""
    first = attachment_storage.save(1, "a.pdf", b"one")
    second = attachment_storage.save(2, "a.pdf", b"two")
    assert attachment_storage.path_for(2, first) is None
    assert attachment_storage.path_for(1, second) is None


def test_delete_removes_the_file(storage_dir):
    name = attachment_storage.save(1, "a.pdf", b"x")
    attachment_storage.delete(1, name)
    assert attachment_storage.path_for(1, name) is None


def test_delete_of_a_missing_file_is_silent(storage_dir):
    attachment_storage.delete(1, "gone.pdf")  # must not raise


def test_odd_extensions_are_sanitised(storage_dir):
    name = attachment_storage.save(1, 'weird."; rm -rf /.pdf', b"x")
    assert all(ch.isalnum() or ch == "." for ch in name)


# --- scheduler gating -----------------------------------------------------

def test_scheduler_is_off_by_default(monkeypatch):
    monkeypatch.delenv("RUN_SCHEDULER", raising=False)
    assert jobs.scheduler_enabled() is False


def test_scheduler_requires_the_explicit_flag(monkeypatch):
    """Two gunicorn workers each starting a scheduler would sync every
    mailbox twice and race on the same rows."""
    monkeypatch.setenv("RUN_SCHEDULER", "true")
    assert jobs.scheduler_enabled() is False
    monkeypatch.setenv("RUN_SCHEDULER", "1")
    assert jobs.scheduler_enabled() is True


def test_start_scheduler_returns_none_when_disabled(app, monkeypatch):
    monkeypatch.delenv("RUN_SCHEDULER", raising=False)
    assert jobs.start_scheduler(app) is None


def test_start_scheduler_registers_the_three_jobs(app, monkeypatch):
    monkeypatch.setenv("RUN_SCHEDULER", "1")
    scheduler = jobs.start_scheduler(app)
    try:
        assert scheduler is not None
        assert {job.id for job in scheduler.get_jobs()} == {
            "sync_email_accounts", "sync_calendar_events", "refresh_oauth_tokens",
        }
        # An overrunning first sync must skip the next tick, not queue
        # behind it — two syncs of one mailbox can never overlap.
        assert all(job.max_instances == 1 for job in scheduler.get_jobs())
    finally:
        scheduler.shutdown(wait=False)


def test_start_scheduler_survives_apscheduler_being_absent(app, monkeypatch):
    """A missing background job must not stop the web app from serving."""
    import builtins

    monkeypatch.setenv("RUN_SCHEDULER", "1")
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name.startswith("apscheduler"):
            raise ImportError("no apscheduler")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    assert jobs.start_scheduler(app) is None


# --- due-account selection ------------------------------------------------

def test_an_account_never_synced_is_due(app, account):
    account.last_sync_at = None
    db.session.flush()
    assert [a.id for a in jobs._accounts_due()] == [account.id]


def test_an_account_synced_recently_is_not_due(app, account):
    EmailSyncSettings.for_company(account.company_id).sync_frequency = 15
    account.last_sync_at = utcnow() - timedelta(minutes=5)
    db.session.flush()
    assert jobs._accounts_due() == []


def test_an_account_past_its_interval_is_due(app, account):
    EmailSyncSettings.for_company(account.company_id).sync_frequency = 15
    account.last_sync_at = utcnow() - timedelta(minutes=20)
    db.session.flush()
    assert [a.id for a in jobs._accounts_due()] == [account.id]


def test_per_tenant_frequency_is_honoured(app, company, other_company, account):
    """One scheduler tick, per-company intervals — no job per tenant."""
    theirs = EmailAccount(company_id=other_company.id, provider="gmail",
                          email_address="theirs@example.com")
    db.session.add(theirs)
    db.session.flush()

    EmailSyncSettings.for_company(company.id).sync_frequency = 5
    EmailSyncSettings.for_company(other_company.id).sync_frequency = 60
    account.last_sync_at = utcnow() - timedelta(minutes=10)
    theirs.last_sync_at = utcnow() - timedelta(minutes=10)
    db.session.flush()

    assert [a.id for a in jobs._accounts_due()] == [account.id]


def test_a_company_with_sync_switched_off_is_skipped(app, company, account):
    EmailSyncSettings.for_company(company.id).sync_enabled = False
    account.last_sync_at = None
    db.session.flush()
    assert jobs._accounts_due() == []


def test_a_paused_account_is_skipped(app, account):
    account.sync_enabled = False
    account.last_sync_at = None
    db.session.flush()
    assert jobs._accounts_due() == []


# --- job bodies -----------------------------------------------------------

def test_sync_email_accounts_runs_due_accounts(app, account, client_record):
    account.last_sync_at = None
    db.session.commit()
    with fakes.fake_providers(threads=[fakes.thread()]):
        results = jobs.sync_email_accounts(app)
    assert len(results) == 1 and results[0].ok
    assert EmailThread.query.count() == 1


def test_one_failing_account_does_not_stop_the_others(app, company, other_company, account):
    """A studio with a revoked grant shouldn't stall everyone else's mail."""
    theirs = EmailAccount(company_id=other_company.id, provider="gmail",
                          email_address="theirs@example.com")
    theirs.access_token = "a"
    theirs.refresh_token = "r"
    db.session.add(theirs)
    db.session.commit()

    class HalfBroken(fakes.FakeEmailProvider):
        def fetch_threads(self, since=None, limit=None, include_sent=True):
            if self.account.email_address == "theirs@example.com":
                raise RuntimeError("kaboom")
            return []

    with fakes.fake_providers():
        from communications.sync import email_sync as module

        module.email_provider_for = HalfBroken
        results = jobs.sync_email_accounts(app)

    assert len(results) == 2
    assert sum(1 for r in results if r.ok) == 1


def test_sync_calendar_events_job(app, account):
    db.session.commit()
    with fakes.fake_providers(events=[fakes.event()]):
        results = jobs.sync_calendar_events(app)
    assert len(results) == 1 and results[0].ok


def test_calendar_job_skips_a_company_with_sync_off(app, company, account):
    EmailSyncSettings.for_company(company.id).sync_enabled = False
    db.session.commit()
    with fakes.fake_providers(events=[fakes.event()]):
        assert jobs.sync_calendar_events(app) == []


def test_refresh_job_marks_a_revoked_grant_on_the_account(app, account, monkeypatch):
    """Turns a revoked grant into a visible error within the hour, rather
    than at whatever future moment someone next tries to send."""
    from communications.oauth import google_oauth

    def explode(acc):
        raise ReauthorizationRequired("grant revoked")

    monkeypatch.setattr(jobs, "TOKEN_REFRESH_MARGIN", timedelta(minutes=20))
    monkeypatch.setattr(google_oauth, "credentials_for", explode)
    account.token_expiry = None
    db.session.commit()

    jobs.refresh_oauth_tokens(app)
    db.session.refresh(account)
    assert "revoked" in account.last_sync_error


def test_refresh_job_skips_tokens_with_plenty_of_life_left(app, account, monkeypatch):
    from communications.oauth import google_oauth

    called = []
    monkeypatch.setattr(google_oauth, "credentials_for", lambda acc: called.append(acc))
    account.token_expiry = utcnow() + timedelta(hours=5)
    db.session.commit()

    assert jobs.refresh_oauth_tokens(app) == 0
    assert called == []


def test_refresh_job_clears_a_stale_error_on_success(app, account, monkeypatch):
    from communications.oauth import google_oauth

    monkeypatch.setattr(google_oauth, "credentials_for", lambda acc: object())
    account.token_expiry = None
    account.last_sync_error = "old failure"
    db.session.commit()

    assert jobs.refresh_oauth_tokens(app) == 1
    db.session.refresh(account)
    assert account.last_sync_error is None
