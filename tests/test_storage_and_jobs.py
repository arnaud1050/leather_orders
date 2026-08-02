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


# --- calendar has its own interval ----------------------------------------
#
# It used to run on a fixed 30 minutes for everybody while the settings page
# only offered a mail frequency — so a company asking for 10-minute syncing
# got it for mail and not for appointments, with nothing saying so.

def test_the_calendar_interval_is_separate_from_the_mail_one(app, company, account):
    """The bug this fixes: one dial can't describe two schedules."""
    settings = EmailSyncSettings.for_company(company.id)
    settings.sync_frequency = 5
    settings.calendar_frequency = 60
    account.last_sync_at = utcnow() - timedelta(minutes=10)
    account.last_calendar_sync_at = utcnow() - timedelta(minutes=10)
    db.session.flush()

    assert [a.id for a in jobs._accounts_due()] == [account.id]
    assert jobs._accounts_due(calendar=True) == []


def test_a_calendar_past_its_interval_is_due(app, company, account):
    EmailSyncSettings.for_company(company.id).calendar_frequency = 30
    account.last_calendar_sync_at = utcnow() - timedelta(minutes=45)
    db.session.flush()
    assert [a.id for a in jobs._accounts_due(calendar=True)] == [account.id]


def test_a_calendar_never_synced_is_due(app, account):
    account.last_calendar_sync_at = None
    db.session.flush()
    assert [a.id for a in jobs._accounts_due(calendar=True)] == [account.id]


def test_the_calendar_check_reads_its_own_timestamp(app, company, account):
    """A mail sync a minute ago says nothing about when the calendar last
    ran — they have separate stamps for that reason."""
    EmailSyncSettings.for_company(company.id).calendar_frequency = 30
    account.last_sync_at = utcnow()
    account.last_calendar_sync_at = utcnow() - timedelta(hours=2)
    db.session.flush()
    assert [a.id for a in jobs._accounts_due(calendar=True)] == [account.id]


def test_per_tenant_calendar_frequency_is_honoured(app, company, other_company, account):
    theirs = EmailAccount(company_id=other_company.id, provider="gmail",
                          email_address="theirs@example.com")
    db.session.add(theirs)
    db.session.flush()

    EmailSyncSettings.for_company(company.id).calendar_frequency = 5
    EmailSyncSettings.for_company(other_company.id).calendar_frequency = 120
    account.last_calendar_sync_at = utcnow() - timedelta(minutes=10)
    theirs.last_calendar_sync_at = utcnow() - timedelta(minutes=10)
    db.session.flush()

    assert [a.id for a in jobs._accounts_due(calendar=True)] == [account.id]


def test_the_master_switch_stops_the_calendar_too(app, company, account):
    """"Sync mail automatically" is the tenant's master switch — off means
    no scheduled traffic of either kind, whatever the intervals say."""
    EmailSyncSettings.for_company(company.id).sync_enabled = False
    account.last_calendar_sync_at = None
    db.session.flush()
    assert jobs._accounts_due(calendar=True) == []


def test_a_paused_account_syncs_no_calendar(app, account):
    account.sync_enabled = False
    account.last_calendar_sync_at = None
    db.session.flush()
    assert jobs._accounts_due(calendar=True) == []


def test_a_company_with_no_settings_row_uses_the_defaults(app, account):
    """A tenant that has never opened the settings page must not sync on
    every tick."""
    EmailSyncSettings.query.delete()
    account.last_sync_at = utcnow() - timedelta(minutes=10)
    account.last_calendar_sync_at = utcnow() - timedelta(minutes=10)
    db.session.flush()

    assert jobs._accounts_due() == []            # default 15
    assert jobs._accounts_due(calendar=True) == []  # default 30


def test_the_calendar_job_skips_accounts_that_are_not_due(app, company, account):
    EmailSyncSettings.for_company(company.id).calendar_frequency = 60
    account.last_calendar_sync_at = utcnow() - timedelta(minutes=5)
    db.session.commit()

    with fakes.fake_providers(events=[fakes.event()]):
        assert jobs.sync_calendar_events(app) == []


def test_both_jobs_tick_on_the_same_short_interval(app, monkeypatch):
    """The per-company frequency is enforced inside the job, so the wake-up
    has to be finer than the smallest interval anyone can choose. A calendar
    job waking every 30 minutes cannot honour a company that asked for 10."""
    monkeypatch.setenv("RUN_SCHEDULER", "1")
    scheduler = jobs.start_scheduler(app)
    if scheduler is None:  # APScheduler not installed
        return
    try:
        intervals = {
            job.id: job.trigger.interval.total_seconds() / 60
            for job in scheduler.get_jobs()
        }
        assert intervals["sync_email_accounts"] == jobs.TICK_MINUTES
        assert intervals["sync_calendar_events"] == jobs.TICK_MINUTES
    finally:
        scheduler.shutdown(wait=False)


# --- three sync buttons, each doing what its label says -------------------
#
# With RUN_SCHEDULER unset — the default — these buttons are the only thing
# that fetches anything at all.

def test_the_mail_button_does_not_touch_the_calendar(logged_in, csrf, account):
    """It sits under the Email sync heading. A button that quietly did more
    than its label says is worse than one that does less."""
    from communications.models import CalendarEvent

    with fakes.fake_providers(events=[fakes.event(event_id="e-manual")]):
        logged_in.post("/integrations/sync", data={"csrf_token": csrf})

    assert CalendarEvent.query.count() == 0


def test_the_combined_button_syncs_both(logged_in, csrf, account, client_record):
    """The one button that covers both halves of the page, which is why it
    sits above the split, next to Connect Gmail."""
    from communications.models import CalendarEvent, EmailThread

    with fakes.fake_providers(
        threads=[fakes.thread()], events=[fakes.event(event_id="e-both")],
    ):
        response = logged_in.post("/integrations/sync-all", data={"csrf_token": csrf})

    assert response.status_code == 302
    assert EmailThread.query.count() == 1
    assert CalendarEvent.query.filter_by(provider_event_id="e-both").count() == 1


def test_the_combined_button_reports_a_calendar_failure(logged_in, csrf, account,
                                                        client_record):
    with fakes.fake_providers(
        threads=[fakes.thread()], calendar_error=fakes.provider_error("Calendar down"),
    ):
        logged_in.post("/integrations/sync-all", data={"csrf_token": csrf})

    assert "Calendar down" in logged_in.get(
        "/settings/integrations").get_data(as_text=True)


def test_the_combined_button_sits_with_connect(logged_in, account):
    body = logged_in.get("/settings/integrations").get_data(as_text=True)
    actions = body.index("integration-actions")
    assert body.index("Connect Gmail") > actions
    assert body.index("Sync mail and calendar now") > actions
    # Above the split into the two sections.
    assert body.index("Sync mail and calendar now") < body.index("Email sync")


def test_the_combined_route_needs_a_csrf_token(logged_in, account):
    assert logged_in.post("/integrations/sync-all").status_code == 400


def test_the_combined_route_needs_a_login(app):
    assert app.test_client().post("/integrations/sync-all").status_code in (302, 400)


def test_the_settings_page_offers_both_intervals(logged_in, account):
    body = logged_in.get("/settings/integrations").get_data(as_text=True)
    assert 'name="sync_frequency"' in body
    assert 'name="calendar_frequency"' in body
    assert "Email sync" in body
    assert "Calendar sync" in body


def test_saving_the_calendar_interval(logged_in, csrf, company):
    logged_in.post("/integrations/sync-settings", data={
        "csrf_token": csrf, "section": "calendar", "calendar_frequency": "45",
    })
    assert EmailSyncSettings.for_company(company.id).calendar_frequency == 45


def test_an_absurd_calendar_interval_is_clamped(logged_in, csrf, company):
    """A dial, not data: 1 minute is a good way to get rate-limited."""
    logged_in.post("/integrations/sync-settings", data={
        "csrf_token": csrf, "section": "calendar", "calendar_frequency": "1",
    })
    assert EmailSyncSettings.for_company(company.id).calendar_frequency == 5


def test_saving_the_calendar_form_leaves_the_mail_settings_alone(
    logged_in, csrf, company,
):
    """The bug two forms invite: an unticked checkbox posts *nothing*, so a
    calendar save processed wholesale would read every mail checkbox as
    "off" and switch them all off."""
    settings = EmailSyncSettings.for_company(company.id)
    settings.sync_enabled = True
    settings.sync_sent_mail = True
    settings.sync_attachments = True
    settings.keep_unmatched = True
    settings.sync_frequency = 10
    settings.initial_sync_days = 120
    db.session.commit()

    logged_in.post("/integrations/sync-settings", data={
        "csrf_token": csrf, "section": "calendar", "calendar_frequency": "45",
    })

    settings = EmailSyncSettings.for_company(company.id)
    assert settings.sync_enabled is True
    assert settings.sync_sent_mail is True
    assert settings.sync_attachments is True
    assert settings.keep_unmatched is True
    assert settings.sync_frequency == 10
    assert settings.initial_sync_days == 120


def test_saving_the_mail_form_leaves_the_calendar_interval_alone(
    logged_in, csrf, company,
):
    EmailSyncSettings.for_company(company.id).calendar_frequency = 45
    db.session.commit()

    logged_in.post("/integrations/sync-settings", data={
        "csrf_token": csrf, "section": "email", "sync_enabled": "on",
        "sync_frequency": "10", "initial_sync_days": "90",
    })

    assert EmailSyncSettings.for_company(company.id).calendar_frequency == 45


def test_a_form_with_no_section_still_saves_everything(logged_in, csrf, company):
    """No marker means "the whole lot", so an older form still works."""
    logged_in.post("/integrations/sync-settings", data={
        "csrf_token": csrf, "sync_enabled": "on",
        "sync_frequency": "10", "calendar_frequency": "45",
        "initial_sync_days": "90",
    })
    settings = EmailSyncSettings.for_company(company.id)
    assert settings.sync_frequency == 10
    assert settings.calendar_frequency == 45


# --- the calendar-only Sync now button ------------------------------------

def test_the_calendar_page_has_a_sync_button(logged_in, account):
    body = logged_in.get("/calendar").get_data(as_text=True)
    assert "/integrations/calendar/sync" in body
    assert "Sync now" in body


def test_the_calendar_sync_button_fetches_events(logged_in, csrf, account):
    from communications.models import CalendarEvent

    with fakes.fake_providers(events=[fakes.event(event_id="e-manual")]):
        response = logged_in.post("/integrations/calendar/sync", data={
            "csrf_token": csrf, "return_to": "/calendar",
        })

    assert response.headers["Location"].endswith("/calendar")
    assert CalendarEvent.query.filter_by(provider_event_id="e-manual").count() == 1


def test_the_calendar_sync_button_does_not_fetch_mail(logged_in, csrf, account):
    """Calendar only — someone waiting on an appointment shouldn't wait on a
    mailbox as well."""
    from communications.models import EmailThread

    with fakes.fake_providers(events=[], threads=[fakes.thread()]):
        logged_in.post("/integrations/calendar/sync", data={"csrf_token": csrf})

    assert EmailThread.query.count() == 0


def test_a_calendar_sync_failure_is_reported(logged_in, csrf, account):
    with fakes.fake_providers(calendar_error=fakes.provider_error("Calendar down")):
        response = logged_in.post("/integrations/calendar/sync", data={
            "csrf_token": csrf, "return_to": "/calendar",
        })

    assert response.status_code == 302
    assert "Calendar down" in logged_in.get("/calendar").get_data(as_text=True)


def test_the_calendar_sync_route_needs_a_csrf_token(logged_in, account):
    assert logged_in.post("/integrations/calendar/sync").status_code == 400


def test_the_calendar_sync_route_needs_a_login(app):
    assert app.test_client().post(
        "/integrations/calendar/sync").status_code in (302, 400)


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
