"""
The calendar API the rest of the app calls (§15).

    calendar_service.get_events(company_id, start, end)
    calendar_service.create_event(company_id, title=..., start=..., end=...)
    calendar_service.update_event(company_id, event_id, title=...)

`get_events` reads the **local mirror**, not Google. That's the point of
mirroring: the month view renders on every page load and cannot depend on
a network round trip to a third party that might be slow or down. Writes
go straight through to the provider and are mirrored back immediately, so
a created event shows up without waiting for the next sync.
"""

import logging
from datetime import date, datetime, time

from models import db

from communications.models import CalendarEvent, utcnow
from communications.providers import calendar_provider_for
from communications.services import account_service
from communications.sync import calendar_sync

logger = logging.getLogger(__name__)


class CalendarServiceError(Exception):
    """Something the caller should show the user."""


def get_events(company_id: int, start: datetime, end: datetime) -> list[CalendarEvent]:
    """Mirrored events overlapping [start, end], cancelled ones excluded.

    Overlap, not containment: a three-day event that starts before the
    window still belongs on the days of it that are inside.
    """
    return (
        CalendarEvent.query.filter(
            CalendarEvent.company_id == company_id,
            CalendarEvent.status != "cancelled",
            CalendarEvent.start_time <= end,
            CalendarEvent.end_time >= start,
        )
        .order_by(CalendarEvent.start_time)
        .all()
    )


def events_by_day(company_id: int, year: int, month: int) -> dict[int, list[CalendarEvent]]:
    """day-of-month -> events, for the calendar view.

    Mirrors orders_by_day() in app.py so calendar.html can loop the two the
    same way. A multi-day event appears on each of its days within the
    month, which is what someone reading a month grid expects.
    """
    month_start = datetime.combine(date(year, month, 1), time.min)
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    month_end = datetime.combine(next_month, time.min)

    grouped: dict[int, list[CalendarEvent]] = {}
    for event in get_events(company_id, month_start, month_end):
        if not event.start_time:
            continue
        cursor = max(event.start_time.date(), month_start.date())
        last = min((event.end_time or event.start_time).date(), next_month)
        while cursor <= last and cursor < next_month:
            if cursor.month == month and cursor.year == year:
                grouped.setdefault(cursor.day, []).append(event)
            cursor = date.fromordinal(cursor.toordinal() + 1)
    return grouped


def has_calendar(company_id: int) -> bool:
    """Whether any connected account granted calendar access.

    Used to decide whether the calendar view mentions events at all — a
    studio that hasn't connected anything shouldn't see empty scaffolding
    for a feature it isn't using.
    """
    from communications import config

    return any(
        any(account.has_scope(scope) for scope in config.CALENDAR_SCOPES)
        for account in account_service.accounts_for(company_id)
    )


def create_event(
    company_id: int, title: str, start: datetime, end: datetime,
    description=None, location=None, attendees=None, all_day=False,
    client_id=None, account_id=None,
) -> CalendarEvent:
    """Create an event in Google and mirror it locally.

    Commits, for the same reason send_email does: once Google has the
    event, the local row must not be lost by a caller rolling back.
    """
    account = _calendar_account(company_id, account_id)
    fetched = calendar_provider_for(account).create_event(
        title=title, start=start, end=end, description=description,
        location=location, attendees=attendees, all_day=all_day,
    )
    event = calendar_sync.store_event(
        account, fetched, {}, calendar_sync.CalendarSyncResult(account_id=account.id),
    )
    if client_id:
        event.client_id = client_id
    db.session.commit()
    return event


def update_event(company_id: int, event_id: int, **fields) -> CalendarEvent:
    """Patch a mirrored event through to Google, then re-mirror it."""
    event = CalendarEvent.query.filter_by(id=event_id, company_id=company_id).first()
    if event is None:
        raise CalendarServiceError("That event no longer exists.")

    account = account_service.get_account(company_id, event.email_account_id)
    if account is None:
        raise CalendarServiceError("The account this event came from is no longer connected.")

    fetched = calendar_provider_for(account).update_event(event.provider_event_id, **fields)
    calendar_sync.store_event(
        account, fetched, {}, calendar_sync.CalendarSyncResult(account_id=account.id),
    )
    event.updated_at = utcnow()
    db.session.commit()
    return event


def sync_now(company_id: int) -> list:
    """Refresh every calendar-capable account for one company."""
    return [
        calendar_sync.sync_calendar(account)
        for account in account_service.sync_enabled_accounts(company_id)
    ]


def _calendar_account(company_id: int, account_id=None):
    from communications import config

    account = (
        account_service.get_account(company_id, account_id) if account_id
        else account_service.default_account(company_id)
    )
    if account is None:
        raise CalendarServiceError(
            "No Google account is connected. Connect one under Settings → Integrations."
        )
    if not any(account.has_scope(scope) for scope in config.CALENDAR_SCOPES):
        raise CalendarServiceError(
            f"{account.email_address} was connected without calendar access. "
            "Reconnect it and accept the calendar permission."
        )
    return account
