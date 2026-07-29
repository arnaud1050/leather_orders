"""Calendar sync and the service the month view reads through."""

from datetime import datetime, timedelta

import pytest

from models import Client, db

from communications.models import CalendarEvent, utcnow
from communications.providers.base import ProviderError
from communications.services import calendar_service
from communications.sync import calendar_sync

from tests import fakes
from tests.conftest import MAIL_ONLY_SCOPES


# --- sync -----------------------------------------------------------------

def test_sync_stores_events(account):
    with fakes.fake_providers(events=[fakes.event()]):
        result = calendar_sync.sync_calendar(account)

    assert result.ok and result.events_created == 1
    stored = CalendarEvent.query.one()
    assert stored.title == "Fitting"
    assert stored.company_id == account.company_id


def test_resyncing_updates_rather_than_duplicating(account):
    with fakes.fake_providers(events=[fakes.event()]):
        calendar_sync.sync_calendar(account)
    with fakes.fake_providers(events=[fakes.event(title="Fitting (moved)")]):
        result = calendar_sync.sync_calendar(account)

    assert result.events_created == 0 and result.events_updated == 1
    assert CalendarEvent.query.count() == 1
    assert CalendarEvent.query.one().title == "Fitting (moved)"


def test_a_moved_event_moves_here_too(account):
    """Unlike a received message, an event isn't immutable — keeping the
    first version we saw would be wrong."""
    start = datetime(2026, 7, 28, 10, 0)
    with fakes.fake_providers(events=[fakes.event(start=start)]):
        calendar_sync.sync_calendar(account)
    moved = start + timedelta(days=1)
    with fakes.fake_providers(events=[fakes.event(start=moved)]):
        calendar_sync.sync_calendar(account)

    assert CalendarEvent.query.one().start_time == moved


def test_cancelled_events_are_kept_not_deleted(account):
    """A sync must not make a row vanish from under someone looking at it."""
    with fakes.fake_providers(events=[fakes.event()]):
        calendar_sync.sync_calendar(account)
    with fakes.fake_providers(events=[fakes.event(status="cancelled")]):
        calendar_sync.sync_calendar(account)

    stored = CalendarEvent.query.one()
    assert stored.is_cancelled is True


def test_attendees_are_matched_to_clients(account, client_record):
    with fakes.fake_providers(events=[fakes.event(attendees=["marie@example.com"])]):
        calendar_sync.sync_calendar(account)
    assert CalendarEvent.query.one().client_id == client_record.id


def test_attendee_matching_does_not_cross_tenants(account, other_company):
    db.session.add(Client(
        company_id=other_company.id, first_name="Not", last_name="Yours",
        email="theirs@example.com",
    ))
    db.session.flush()
    with fakes.fake_providers(events=[fakes.event(attendees=["theirs@example.com"])]):
        calendar_sync.sync_calendar(account)
    assert CalendarEvent.query.one().client_id is None


def test_a_hand_linked_client_survives_a_resync(account, client_record):
    with fakes.fake_providers(events=[fakes.event()]):
        calendar_sync.sync_calendar(account)
    stored = CalendarEvent.query.one()
    stored.client_id = client_record.id
    db.session.commit()

    with fakes.fake_providers(events=[fakes.event()]):
        calendar_sync.sync_calendar(account)
    assert CalendarEvent.query.one().client_id == client_record.id


def test_sync_is_skipped_without_the_calendar_scope(account):
    """A mailbox connected for email alone shouldn't report a calendar
    failure it can't do anything about."""
    account.granted_scopes = MAIL_ONLY_SCOPES
    db.session.flush()

    with fakes.fake_providers(events=[fakes.event()]):
        result = calendar_sync.sync_calendar(account)

    assert result.ok
    assert result.events_seen == 0
    assert CalendarEvent.query.count() == 0


def test_provider_failure_is_reported_not_raised(account):
    with fakes.fake_providers(calendar_error=ProviderError("calendar down")):
        result = calendar_sync.sync_calendar(account)
    assert not result.ok and "calendar down" in result.error


def test_sync_records_the_time(account):
    with fakes.fake_providers(events=[]):
        calendar_sync.sync_calendar(account)
    assert account.last_calendar_sync_at is not None


def test_default_window_spans_lookback_and_lookahead(account):
    with fakes.fake_providers(events=[]):
        calendar_sync.sync_calendar(account)
    _, start, end = fakes.CALENDAR_LOG[-1]
    assert abs((utcnow() - start).days - calendar_sync.LOOKBACK_DAYS) <= 1
    assert abs((end - utcnow()).days - calendar_sync.LOOKAHEAD_DAYS) <= 1


# --- service: reading -----------------------------------------------------

def test_get_events_excludes_cancelled(account):
    with fakes.fake_providers(events=[
        fakes.event(event_id="e-ok"),
        fakes.event(event_id="e-gone", status="cancelled"),
    ]):
        calendar_sync.sync_calendar(account)

    visible = calendar_service.get_events(
        account.company_id, utcnow() - timedelta(days=1), utcnow() + timedelta(days=1),
    )
    assert [e.provider_event_id for e in visible] == ["e-ok"]


def test_get_events_is_tenant_scoped(account, other_company):
    with fakes.fake_providers(events=[fakes.event()]):
        calendar_sync.sync_calendar(account)
    assert calendar_service.get_events(
        other_company.id, utcnow() - timedelta(days=1), utcnow() + timedelta(days=1),
    ) == []


def test_get_events_includes_an_event_overlapping_the_window_edge(account):
    """Overlap, not containment: a three-day event that starts before the
    window still belongs on the days of it that are inside."""
    start = utcnow() - timedelta(days=2)
    with fakes.fake_providers(events=[
        fakes.event(start=start, end=start + timedelta(days=4)),
    ]):
        calendar_sync.sync_calendar(account)

    found = calendar_service.get_events(
        account.company_id, utcnow(), utcnow() + timedelta(hours=1),
    )
    assert len(found) == 1


def test_events_by_day_places_a_timed_event_on_its_day(account):
    start = datetime(2026, 7, 15, 10, 0)
    with fakes.fake_providers(events=[fakes.event(start=start)]):
        calendar_sync.sync_calendar(account)

    by_day = calendar_service.events_by_day(account.company_id, 2026, 7)
    assert 15 in by_day and by_day[15][0].title == "Fitting"


def test_events_by_day_spans_a_multi_day_event(account):
    """What someone reading a month grid expects: it appears on each of its
    days, not only the first."""
    start = datetime(2026, 7, 15, 0, 0)
    with fakes.fake_providers(events=[
        fakes.event(start=start, end=start + timedelta(days=2), all_day=True),
    ]):
        calendar_sync.sync_calendar(account)

    by_day = calendar_service.events_by_day(account.company_id, 2026, 7)
    assert {15, 16, 17} <= set(by_day)


def test_events_by_day_clips_an_event_that_starts_in_the_previous_month(account):
    start = datetime(2026, 6, 29, 9, 0)
    with fakes.fake_providers(events=[
        fakes.event(start=start, end=start + timedelta(days=4)),
    ]):
        calendar_sync.sync_calendar(account)

    by_day = calendar_service.events_by_day(account.company_id, 2026, 7)
    assert set(by_day) <= {1, 2, 3}
    assert 1 in by_day


def test_events_by_day_handles_a_december_window(account):
    """The next-month calculation rolls the year over."""
    start = datetime(2026, 12, 30, 9, 0)
    with fakes.fake_providers(events=[
        fakes.event(start=start, end=start + timedelta(days=4)),
    ]):
        calendar_sync.sync_calendar(account)

    by_day = calendar_service.events_by_day(account.company_id, 2026, 12)
    assert set(by_day) == {30, 31}


def test_events_by_day_of_an_empty_month(account):
    assert calendar_service.events_by_day(account.company_id, 2020, 1) == {}


def test_has_calendar(account, company):
    assert calendar_service.has_calendar(company.id) is True
    account.granted_scopes = MAIL_ONLY_SCOPES
    db.session.flush()
    assert calendar_service.has_calendar(company.id) is False


def test_has_calendar_without_any_account(other_company):
    assert calendar_service.has_calendar(other_company.id) is False


# --- service: writing -----------------------------------------------------

def test_create_event_calls_the_provider_and_mirrors_it(company, account, client_record):
    start = datetime(2026, 8, 1, 10, 0)
    with fakes.fake_providers():
        event = calendar_service.create_event(
            company.id, title="Second fitting", start=start,
            end=start + timedelta(hours=1), client_id=client_record.id,
        )

    assert event.title == "Second fitting"
    assert event.client_id == client_record.id
    assert CalendarEvent.query.count() == 1
    assert any(call[0] == "create_event" for call in fakes.CALENDAR_LOG)


def test_create_event_without_a_connected_account(other_company):
    with pytest.raises(calendar_service.CalendarServiceError, match="No Google account"):
        calendar_service.create_event(
            other_company.id, "x", datetime(2026, 8, 1), datetime(2026, 8, 1),
        )


def test_create_event_without_the_calendar_scope(company, account):
    account.granted_scopes = MAIL_ONLY_SCOPES
    db.session.flush()
    with pytest.raises(calendar_service.CalendarServiceError, match="calendar access"):
        calendar_service.create_event(
            company.id, "x", datetime(2026, 8, 1), datetime(2026, 8, 1),
        )


def test_update_event_patches_and_re_mirrors(company, account):
    with fakes.fake_providers(events=[fakes.event()]):
        calendar_sync.sync_calendar(account)
    stored = CalendarEvent.query.one()

    with fakes.fake_providers():
        calendar_service.update_event(company.id, stored.id, title="Renamed")

    assert CalendarEvent.query.one().title == "Renamed"
    patch = next(call for call in fakes.CALENDAR_LOG if call[0] == "update_event")
    assert patch[2] == {"title": "Renamed"}  # only the changed field is sent


def test_update_event_refuses_another_tenants_event(other_company, account):
    with fakes.fake_providers(events=[fakes.event()]):
        calendar_sync.sync_calendar(account)
    stored = CalendarEvent.query.one()

    with pytest.raises(calendar_service.CalendarServiceError, match="no longer exists"):
        calendar_service.update_event(other_company.id, stored.id, title="Nope")


def test_calendar_sync_now_covers_every_enabled_account(company, account):
    with fakes.fake_providers(events=[fakes.event()]):
        results = calendar_service.sync_now(company.id)
    assert len(results) == 1 and results[0].ok
