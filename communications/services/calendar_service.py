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

from models import Client, db

from communications.models import CalendarEvent, utcnow
from communications.providers import calendar_provider_for
from communications.services import account_service
from communications.sync import calendar_sync

logger = logging.getLogger(__name__)


class CalendarServiceError(Exception):
    """Something the caller should show the user."""


#: Distinguishes "don't touch this field" from "set it to nothing", which
#: matters for a nullable link like client_id where None is a real value.
_UNSET = object()


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

    This is the only thing the month grid renders — orders live on the
    timeline. A multi-day event appears on each of its days within the month,
    which is what someone reading a month grid expects.
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
    client_id=None, account_id=None, notify=False,
) -> CalendarEvent:
    """Create an event in Google and mirror it locally.

    Commits, for the same reason send_email does: once Google has the
    event, the local row must not be lost by a caller rolling back.

    The linked client is invited automatically — see `guests_for`. `notify`
    decides whether any of them are emailed, and is never inferred from the
    guest list: an appointment noted against a client is not the same act as
    telling them about it.
    """
    account = _calendar_account(company_id, account_id)
    guests = guests_for(company_id, client_id, attendees)
    fetched = calendar_provider_for(account).create_event(
        title=title, start=start, end=end, description=description,
        location=location, attendees=guests, all_day=all_day,
        notify=bool(notify) and bool(guests),
    )
    event = calendar_sync.store_event(
        account, fetched, {}, calendar_sync.CalendarSyncResult(account_id=account.id),
    )
    if client_id:
        event.client_id = client_id
    db.session.commit()
    return event


def guests_for(company_id: int, client_id, extra=None) -> list[str]:
    """The full guest list for an event: the linked client, plus anyone else.

    The client's address comes from their record rather than being retyped,
    which is the whole point — the studio picks a name, not an email. A client
    with no address on file simply contributes nobody; that has to be visible
    in the form rather than discovered when no invitation arrives.

    Deduplicated **case-insensitively while keeping what was typed**: Google
    would reject the same address twice, and the client is usually also the
    obvious thing to type into the extra-guests box. Order is the client
    first, since they're the reason the appointment exists.
    """
    guests: list[str] = []
    seen: set[str] = set()

    def add(address):
        cleaned = (address or "").strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            guests.append(cleaned)

    if client_id:
        client = Client.query.filter_by(id=client_id, company_id=company_id).first()
        if client is not None:
            add(client.email)
    for address in extra or []:
        add(address)
    return guests


def update_event(company_id: int, event_id: int, **fields) -> CalendarEvent:
    """Patch a mirrored event through to Google, then re-mirror it.

    `client_id` is applied locally and not forwarded: which client an
    appointment belongs to is ours, not something Google stores. Popped rather
    than left in `fields` so the provider isn't handed a key it would silently
    ignore.

    **Guests**: pass `attendees` to change them, or leave the key out to leave
    them alone. Given, it's resolved through `guests_for` exactly as it is at
    creation — so the client link still supplies the client's address, and
    re-pointing an appointment at a different client moves the invitation with
    it. Left out, nothing about the guest list is sent, which is what keeps an
    ordinary title edit from touching who is invited.
    """
    event = CalendarEvent.query.filter_by(id=event_id, company_id=company_id).first()
    if event is None:
        raise CalendarServiceError("That event no longer exists.")

    account = account_service.get_account(company_id, event.email_account_id)
    if account is None:
        raise CalendarServiceError("The account this event came from is no longer connected.")

    client_id = fields.pop("client_id", _UNSET)

    if "attendees" in fields:
        # The client whose address should be on the list is the one the event
        # is about to have, not the one it had a moment ago.
        linked = event.client_id if client_id is _UNSET else client_id
        fields["attendees"] = guests_for(company_id, linked, fields["attendees"])
        # Never quietly drop the organiser. Google puts the studio's own
        # mailbox on the attendee list of anything it hosts, and the form is
        # built from `guest_list`, which hides it — so without this, saving any
        # edit would remove the organiser from their own appointment. Restored
        # only if it was actually there: adding it to an event that never
        # carried it would be inviting ourselves to our own reminder.
        own = (account.email_address or "").strip()
        was_listed = any(
            address.lower() == own.lower() for address in event.attendee_list
        )
        already = any(
            address.lower() == own.lower() for address in fields["attendees"]
        )
        if own and was_listed and not already:
            fields["attendees"].append(own)

    fetched = calendar_provider_for(account).update_event(event.provider_event_id, **fields)
    calendar_sync.store_event(
        account, fetched, {}, calendar_sync.CalendarSyncResult(account_id=account.id),
    )
    if client_id is not _UNSET:
        event.client_id = client_id
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
