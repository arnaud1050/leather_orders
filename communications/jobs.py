"""
Background jobs (§18).

Three entry points, all plain callables that take a Flask app and push
their own context:

    sync_email_accounts(app)
    refresh_oauth_tokens(app)
    sync_calendar_events(app)

They're callables first and scheduled second on purpose — cron, Celery, a
management command or a test can all call them directly, and swapping
APScheduler for something else later means rewriting `start_scheduler()`
and nothing above it.

**Why the scheduler is behind a flag.** Both Docker deployments run
gunicorn with 2 workers and `--preload`. An unguarded BackgroundScheduler
would start in *every* worker, so two schedulers would hit Gmail on the
same cadence and race each other inserting the same rows — the same class
of bug `--preload` was added to fix for seeding (see CLAUDE.md,
"Deployment"). So it only starts when `RUN_SCHEDULER=1`, which should be
set on exactly one process.

Per-tenant frequency is honoured without a job per tenant: the scheduler
ticks on a fixed short interval and each tick skips accounts whose
company's `sync_frequency` hasn't elapsed yet.
"""

import logging
import os
from datetime import timedelta

from models import db

from communications.models import EmailSyncSettings, utcnow
from communications.services import account_service
from communications.sync import calendar_sync, email_sync

logger = logging.getLogger(__name__)

# How often the scheduler wakes up. Not the sync frequency — that's
# per-company and enforced inside the job. This only has to be finer than
# the smallest frequency a company can choose (5 minutes, clamped in
# routes.update_sync_settings).
TICK_MINUTES = 5

# Refresh tokens this far ahead of expiry. Long enough that a token is
# never expired when a sync run needs it, short enough not to churn.
TOKEN_REFRESH_MARGIN = timedelta(minutes=20)


def sync_email_accounts(app) -> list:
    """Sync every account that's due, across every tenant.

    One account failing must not stop the rest — a studio with a revoked
    grant shouldn't stall everyone else's mail — so each is wrapped
    individually.
    """
    results = []
    with app.app_context():
        for account in _accounts_due():
            try:
                results.append(email_sync.sync_account(account))
            except Exception:  # noqa: BLE001
                logger.exception("Scheduled email sync failed for account %s", account.id)
                db.session.rollback()
    return results


def sync_calendar_events(app) -> list:
    """Mirror calendars for every account that granted calendar access."""
    results = []
    with app.app_context():
        for account in account_service.sync_enabled_accounts():
            if not _company_sync_enabled(account.company_id):
                continue
            try:
                results.append(calendar_sync.sync_calendar(account))
            except Exception:  # noqa: BLE001
                logger.exception("Scheduled calendar sync failed for account %s", account.id)
                db.session.rollback()
    return results


def refresh_oauth_tokens(app) -> int:
    """Refresh access tokens that are about to expire.

    Strictly speaking redundant — every provider call refreshes on demand
    (see google_oauth.credentials_for). It's here because it turns a
    revoked grant into a *visible* error on the integrations page within
    the hour, rather than at whatever future moment someone next tries to
    send an email.
    """
    from communications.oauth import google_oauth
    from communications.providers.base import ReauthorizationRequired

    refreshed = 0
    with app.app_context():
        deadline = utcnow() + TOKEN_REFRESH_MARGIN
        accounts = [
            account for account in account_service.sync_enabled_accounts()
            if account.token_expiry is None or account.token_expiry <= deadline
        ]
        for account in accounts:
            try:
                google_oauth.credentials_for(account)
                account.last_sync_error = None
                refreshed += 1
            except ReauthorizationRequired as exc:
                account.last_sync_error = str(exc)[:1000]
            except Exception:  # noqa: BLE001
                logger.exception("Token refresh failed for account %s", account.id)
        db.session.commit()
    return refreshed


def _accounts_due() -> list:
    """Accounts whose company has sync on and whose interval has elapsed."""
    due = []
    settings_by_company = {
        settings.company_id: settings for settings in EmailSyncSettings.query.all()
    }
    now = utcnow()
    for account in account_service.sync_enabled_accounts():
        settings = settings_by_company.get(account.company_id)
        if settings is not None and not settings.sync_enabled:
            continue
        frequency = settings.sync_frequency if settings else 15
        if account.last_sync_at and now - account.last_sync_at < timedelta(minutes=frequency):
            continue
        due.append(account)
    return due


def _company_sync_enabled(company_id: int) -> bool:
    settings = EmailSyncSettings.query.filter_by(company_id=company_id).first()
    return settings is None or settings.sync_enabled


def scheduler_enabled() -> bool:
    return os.environ.get("RUN_SCHEDULER") == "1"


def start_scheduler(app):
    """Start the in-process scheduler, if this process is the one for it.

    Returns the scheduler, or None when it isn't enabled or APScheduler
    isn't installed — never raises, because a missing background job must
    not stop the web app from serving.
    """
    if not scheduler_enabled():
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning("RUN_SCHEDULER=1 but APScheduler isn't installed; no jobs will run.")
        return None

    scheduler = BackgroundScheduler(daemon=True)
    # coalesce + max_instances=1: if a run overshoots its slot (a big first
    # sync will), the next tick is skipped rather than queued behind it, so
    # two syncs of the same mailbox can never overlap.
    scheduler.add_job(
        sync_email_accounts, "interval", minutes=TICK_MINUTES, args=[app],
        id="sync_email_accounts", coalesce=True, max_instances=1,
    )
    scheduler.add_job(
        sync_calendar_events, "interval", minutes=30, args=[app],
        id="sync_calendar_events", coalesce=True, max_instances=1,
    )
    scheduler.add_job(
        refresh_oauth_tokens, "interval", minutes=30, args=[app],
        id="refresh_oauth_tokens", coalesce=True, max_instances=1,
    )
    scheduler.start()
    logger.info("Communications scheduler started (tick: %s minutes).", TICK_MINUTES)
    return scheduler
