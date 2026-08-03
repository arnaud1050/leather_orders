"""
Calendar synchronisation.

Same shape as email_sync: upsert on the provider's own event id, so
running it twice is a no-op and a crashed run just repeats.

Two differences from mail, both because events aren't immutable the way a
received message is:

- An event's fields are **overwritten** on every sync. A meeting that moved
  should move here too; keeping the first version we saw would be wrong.
- A cancelled event is kept with `status = "cancelled"` rather than
  deleted, so a sync can't make a row vanish out from under someone
  looking at it. The UI filters cancelled events out.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from models import Client, db

from communications.models import AUDIT_SYNC_FAILED, CalendarEvent, utcnow
from communications.providers import calendar_provider_for
from communications.providers.base import ProviderError
from communications.services import audit

logger = logging.getLogger(__name__)

# How much calendar to mirror. Enough history to look back at a month
# that's just ended, and enough ahead to cover the timeline's 8-week
# window several times over.
LOOKBACK_DAYS = 60
LOOKAHEAD_DAYS = 180


@dataclass
class CalendarSyncResult:
    account_id: int
    email_address: str = ""
    events_seen: int = 0
    events_created: int = 0
    events_updated: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def summary(self) -> str:
        if self.error:
            return f"{self.email_address}: {self.error}"
        return (
            f"{self.email_address}: {self.events_created} new, "
            f"{self.events_updated} updated calendar event(s)"
        )


def sync_calendar(account, start: datetime | None = None, end: datetime | None = None):
    """Mirror one account's primary calendar into the database.

    Silently skipped (as a success) when the account never granted the
    calendar scope — a mailbox connected for email alone shouldn't report
    a calendar failure it can't do anything about.
    """
    from communications import config

    result = CalendarSyncResult(account_id=account.id, email_address=account.email_address)

    if not any(account.has_scope(scope) for scope in config.CALENDAR_SCOPES):
        return result

    now = utcnow()
    start = start or now - timedelta(days=LOOKBACK_DAYS)
    end = end or now + timedelta(days=LOOKAHEAD_DAYS)

    try:
        events = calendar_provider_for(account).list_events(start, end)
    except ProviderError as exc:
        result.error = str(exc)
        audit.record(
            account.company_id, AUDIT_SYNC_FAILED,
            f"Calendar sync for {account.email_address}: {exc}",
        )
        db.session.commit()
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error syncing calendar for account %s", account.id)
        result.error = f"Unexpected error: {exc}"
        return result

    clients_by_email = {
        client.email.strip().lower(): client
        for client in Client.query.filter_by(company_id=account.company_id).all()
        if client.email and client.email.strip()
    }

    try:
        for fetched in events:
            result.events_seen += 1
            store_event(account, fetched, clients_by_email, result)
        account.last_calendar_sync_at = utcnow()
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error storing calendar events for account %s", account.id)
        db.session.rollback()
        result.error = f"Could not store events: {exc}"

    return result


def store_event(account, fetched, clients_by_email, result) -> CalendarEvent:
    event = CalendarEvent.query.filter_by(
        email_account_id=account.id, provider_event_id=fetched.provider_event_id,
    ).first()
    if event is None:
        event = CalendarEvent(
            company_id=account.company_id,
            email_account_id=account.id,
            provider_event_id=fetched.provider_event_id,
        )
        db.session.add(event)
        result.events_created += 1
    else:
        result.events_updated += 1

    event.title = fetched.title
    event.description = fetched.description
    event.location = fetched.location
    event.start_time = fetched.start_time
    event.end_time = fetched.end_time
    event.all_day = fetched.all_day
    event.status = fetched.status
    # Overwritten like every other field: the provider is the authority on who
    # is invited, and a guest added in Google Calendar should show up here.
    # Storing them is what lets the edit form rebuild the list rather than
    # blanking it — see the column's own note.
    event.attendees = ", ".join(fetched.attendees)
    event.updated_at = utcnow()

    # Same exact-address matching as threads, and the same "only ever add"
    # rule: a client linked by hand isn't unlinked by a later sync.
    if event.client_id is None:
        for attendee in fetched.attendees:
            client = clients_by_email.get(attendee)
            if client is not None:
                event.client_id = client.id
                break

    return event
