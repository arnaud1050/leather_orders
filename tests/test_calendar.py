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


def test_update_event_sets_the_client_link_locally(company, account, client_record):
    """client_id is ours, not Google's — applied here, never forwarded."""
    with fakes.fake_providers(events=[fakes.event()]):
        calendar_sync.sync_calendar(account)
    stored = CalendarEvent.query.one()

    with fakes.fake_providers():
        calendar_service.update_event(company.id, stored.id, client_id=client_record.id)

    assert CalendarEvent.query.one().client_id == client_record.id
    patch = next(call for call in fakes.CALENDAR_LOG if call[0] == "update_event")
    assert "client_id" not in patch[2]


def test_update_event_can_clear_the_client_link(company, account, client_record):
    with fakes.fake_providers(events=[fakes.event()]):
        calendar_sync.sync_calendar(account)
    stored = CalendarEvent.query.one()
    stored.client_id = client_record.id
    db.session.flush()

    with fakes.fake_providers():
        calendar_service.update_event(company.id, stored.id, client_id=None)

    assert CalendarEvent.query.one().client_id is None


def test_update_event_leaves_the_client_link_alone_when_not_given(
    company, account, client_record,
):
    """Not passing client_id must mean "don't touch", not "clear"."""
    with fakes.fake_providers(events=[fakes.event()]):
        calendar_sync.sync_calendar(account)
    stored = CalendarEvent.query.one()
    stored.client_id = client_record.id
    db.session.flush()

    with fakes.fake_providers():
        calendar_service.update_event(company.id, stored.id, title="Renamed")

    assert CalendarEvent.query.one().client_id == client_record.id


# --- the month view's event UI --------------------------------------------
#
# The forms live in app.py's calendar template but post into this module's
# blueprint, so these cover the seam between the two: the timezone conversion
# on the way in, tenant scoping, and CSRF.

def _event_form(csrf, **overrides):
    form = {
        "csrf_token": csrf,
        "title": "Second fitting",
        "start_date": "2026-08-05",
        "start_time": "14:00",
        "end_date": "2026-08-05",
        "end_time": "15:00",
        "return_to": "/calendar",
    }
    form.update(overrides)
    return form


def test_creating_an_event_from_the_month_view(logged_in, csrf, company, account):
    with fakes.fake_providers():
        response = logged_in.post(
            "/calendar/events/new", data=_event_form(csrf), follow_redirects=True,
        )

    assert response.status_code == 200
    stored = CalendarEvent.query.one()
    assert stored.title == "Second fitting"
    # 14:00 in Vancouver (PDT, UTC-7) is 21:00 UTC. Storing the wall clock
    # unconverted would book the appointment seven hours out and look fine.
    assert stored.start_time == datetime(2026, 8, 5, 21, 0)
    assert stored.end_time == datetime(2026, 8, 5, 22, 0)


def test_a_created_event_is_shown_back_in_the_company_zone(logged_in, csrf, account):
    with fakes.fake_providers():
        logged_in.post("/calendar/events/new", data=_event_form(csrf))

    body = logged_in.get("/month/2026/8").get_data(as_text=True)
    assert "2:00pm - 3:00pm" in body


def test_an_all_day_event_skips_the_times(logged_in, csrf, account):
    with fakes.fake_providers():
        logged_in.post("/calendar/events/new", data=_event_form(
            csrf, all_day="on", start_time="", end_time="",
        ))

    stored = CalendarEvent.query.one()
    assert stored.all_day is True


def test_an_end_before_the_start_is_refused(logged_in, csrf, account):
    with fakes.fake_providers():
        response = logged_in.post(
            "/calendar/events/new",
            data=_event_form(csrf, start_time="15:00", end_time="14:00"),
            follow_redirects=True,
        )

    assert CalendarEvent.query.count() == 0
    assert "ends before it starts" in response.get_data(as_text=True)


def test_a_timed_event_with_no_times_is_refused(logged_in, csrf, account):
    with fakes.fake_providers():
        response = logged_in.post(
            "/calendar/events/new", data=_event_form(csrf, start_time="", end_time=""),
            follow_redirects=True,
        )

    assert CalendarEvent.query.count() == 0
    assert "start and an end time" in response.get_data(as_text=True)


def test_a_missing_start_date_is_refused(logged_in, csrf, account):
    with fakes.fake_providers():
        response = logged_in.post(
            "/calendar/events/new", data=_event_form(csrf, start_date=""),
            follow_redirects=True,
        )

    assert CalendarEvent.query.count() == 0
    assert "needs a start date" in response.get_data(as_text=True)


def test_creating_an_event_without_a_calendar_reports_it(logged_in, csrf, company):
    """No connected account: the studio gets told, not a 500."""
    with fakes.fake_providers():
        response = logged_in.post(
            "/calendar/events/new", data=_event_form(csrf), follow_redirects=True,
        )

    assert CalendarEvent.query.count() == 0
    assert "No Google account is connected" in response.get_data(as_text=True)


def test_editing_an_event_from_the_month_view(logged_in, csrf, account):
    with fakes.fake_providers(events=[fakes.event()]):
        calendar_sync.sync_calendar(account)
    stored = CalendarEvent.query.one()

    with fakes.fake_providers():
        logged_in.post(
            f"/calendar/events/{stored.id}", data=_event_form(csrf, title="Renamed"),
        )

    assert CalendarEvent.query.one().title == "Renamed"


def test_editing_another_tenants_event_is_refused(logged_in, csrf, other_company, app):
    """The event id arrives in a URL anyone can edit."""
    from communications.models import EmailAccount

    theirs = EmailAccount(
        company_id=other_company.id, provider="gmail",
        email_address="theirs@example.com", granted_scopes="",
    )
    db.session.add(theirs)
    db.session.flush()
    event = CalendarEvent(
        company_id=other_company.id, email_account_id=theirs.id,
        provider_event_id="e-theirs", title="Theirs",
        start_time=datetime(2026, 8, 5, 17, 0), end_time=datetime(2026, 8, 5, 18, 0),
    )
    db.session.add(event)
    db.session.flush()

    with fakes.fake_providers():
        response = logged_in.post(
            f"/calendar/events/{event.id}", data=_event_form(csrf, title="Mine now"),
            follow_redirects=True,
        )

    assert db.session.get(CalendarEvent, event.id).title == "Theirs"
    assert "no longer exists" in response.get_data(as_text=True)


def test_an_event_cannot_be_linked_to_another_tenants_client(
    logged_in, csrf, account, other_company,
):
    theirs = Client(
        company_id=other_company.id, first_name="Not", last_name="Ours",
        email="not-ours@example.com",
    )
    db.session.add(theirs)
    db.session.flush()

    with fakes.fake_providers():
        logged_in.post("/calendar/events/new", data=_event_form(csrf, client_id=theirs.id))

    assert CalendarEvent.query.one().client_id is None


def test_event_routes_require_a_csrf_token(logged_in, account):
    with fakes.fake_providers():
        response = logged_in.post("/calendar/events/new", data=_event_form("wrong-token"))

    assert response.status_code == 400
    assert CalendarEvent.query.count() == 0


def test_the_month_view_hides_event_ui_without_a_calendar(logged_in, company):
    body = logged_in.get("/calendar").get_data(as_text=True)
    assert "+ New event" not in body


def test_the_month_view_offers_event_ui_with_a_calendar(logged_in, account):
    body = logged_in.get("/calendar").get_data(as_text=True)
    assert "+ New event" in body
