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


# --- guests and invitations -----------------------------------------------
#
# The rule underneath all of these: attaching somebody to an event and
# emailing them about it are two separate acts. Adding an attendee used to
# imply neither — Google's insert defaults to notifying nobody, so the old
# "Invite" field attached an address and sent no mail at all. Sending is now
# explicit, which means it has to be tested explicitly in both directions:
# that it happens when asked, and that it never happens when it wasn't.

def _create_calls():
    return [row for row in fakes.CALENDAR_LOG if row[0] == "create_event"]


def _last_patch():
    return [row for row in fakes.CALENDAR_LOG if row[0] == "update_event"][-1][2]


def test_the_linked_client_is_invited_without_anyone_typing_their_address(
    logged_in, csrf, account, client_record,
):
    """The whole point: the studio picks a name, not an email."""
    with fakes.fake_providers():
        logged_in.post(
            "/calendar/events/new",
            data=_event_form(csrf, client_id=client_record.id, send_invite="1"),
        )

    assert _create_calls()[0][4] == ["marie@example.com"]


def test_adding_an_event_without_the_invite_button_sends_no_mail(
    logged_in, csrf, account, client_record,
):
    """A reminder to yourself with the client attached must not mail them.

    This is the regression that matters most: the guest is on the event either
    way, so nothing about the payload distinguishes the two cases except the
    notify flag.
    """
    with fakes.fake_providers():
        logged_in.post(
            "/calendar/events/new", data=_event_form(csrf, client_id=client_record.id),
        )

    call = _create_calls()[0]
    assert call[4] == ["marie@example.com"]   # attached
    assert call[6] is False                   # but not told


def test_the_invite_button_is_what_sends(logged_in, csrf, account, client_record):
    with fakes.fake_providers():
        logged_in.post(
            "/calendar/events/new",
            data=_event_form(csrf, client_id=client_record.id, send_invite="1"),
        )

    assert _create_calls()[0][6] is True


def test_asking_to_invite_nobody_notifies_nobody(logged_in, csrf, account):
    """No client, no extra guests — "send invite" can't mean anything, and
    Google is told not to notify rather than asked to mail an empty list."""
    with fakes.fake_providers():
        logged_in.post("/calendar/events/new", data=_event_form(csrf, send_invite="1"))

    call = _create_calls()[0]
    assert call[4] == []
    assert call[6] is False


def test_extra_guests_join_the_client(logged_in, csrf, account, client_record):
    with fakes.fake_providers():
        logged_in.post("/calendar/events/new", data=_event_form(
            csrf, client_id=client_record.id,
            attendees="cutter@example.com, courier@example.com",
        ))

    assert _create_calls()[0][4] == [
        "marie@example.com", "cutter@example.com", "courier@example.com",
    ]


def test_the_client_is_not_invited_twice_when_also_typed(
    logged_in, csrf, account, client_record,
):
    """Google rejects a duplicate address, and the client's own is the
    obvious thing to type into the extra-guests box."""
    with fakes.fake_providers():
        logged_in.post("/calendar/events/new", data=_event_form(
            csrf, client_id=client_record.id, attendees="MARIE@example.com",
        ))

    assert _create_calls()[0][4] == ["marie@example.com"]


def test_a_client_with_no_email_contributes_no_guest(logged_in, csrf, account, company):
    """Linking them is still legitimate — it files the appointment against
    their record. There's just nobody to invite, and the form says so."""
    silent = Client(company_id=company.id, first_name="No", last_name="Address")
    db.session.add(silent)
    db.session.flush()

    with fakes.fake_providers():
        logged_in.post("/calendar/events/new", data=_event_form(
            csrf, client_id=silent.id, send_invite="1",
        ))

    call = _create_calls()[0]
    assert call[4] == []
    assert call[6] is False
    assert CalendarEvent.query.one().client_id == silent.id


def test_another_tenants_client_contributes_no_guest(
    logged_in, csrf, account, other_company,
):
    """The id arrives in a form field anyone can edit — it must not be a way
    to read an address off another studio's roster by having us mail it."""
    theirs = Client(
        company_id=other_company.id, first_name="Not", last_name="Ours",
        email="not-ours@example.com",
    )
    db.session.add(theirs)
    db.session.flush()

    with fakes.fake_providers():
        logged_in.post("/calendar/events/new", data=_event_form(
            csrf, client_id=theirs.id, send_invite="1",
        ))

    assert _create_calls()[0][4] == []


def test_the_confirmation_says_whether_mail_went_out(
    logged_in, csrf, account, client_record,
):
    """A confirmation that doesn't mention the mail is one you have to open
    Gmail to verify — which was the old message's whole problem."""
    with fakes.fake_providers():
        quiet = logged_in.post(
            "/calendar/events/new",
            data=_event_form(csrf, client_id=client_record.id),
            follow_redirects=True,
        ).get_data(as_text=True)
    assert "No invitations sent." in quiet

    with fakes.fake_providers():
        loud = logged_in.post(
            "/calendar/events/new",
            data=_event_form(csrf, client_id=client_record.id, send_invite="1"),
            follow_redirects=True,
        ).get_data(as_text=True)
    assert "Invitation sent to marie@example.com." in loud


# --- guests, mirrored and editable ----------------------------------------

def test_sync_mirrors_the_guest_list(account):
    """Storing attendees is what makes the guest list editable at all."""
    with fakes.fake_providers(events=[fakes.event(attendees=["marie@example.com"])]):
        calendar_sync.sync_calendar(account)

    assert CalendarEvent.query.one().attendee_list == ["marie@example.com"]


def test_a_guest_added_in_google_shows_up_here(account):
    """The provider is the authority on who is invited."""
    with fakes.fake_providers(events=[fakes.event(attendees=["marie@example.com"])]):
        calendar_sync.sync_calendar(account)
    with fakes.fake_providers(events=[fakes.event(
        attendees=["marie@example.com", "cutter@example.com"],
    )]):
        calendar_sync.sync_calendar(account)

    assert CalendarEvent.query.one().attendee_list == [
        "marie@example.com", "cutter@example.com",
    ]


def test_the_studios_own_mailbox_is_not_shown_as_a_guest(account):
    """Google lists the organiser among the attendees; showing it in a guest
    field reads as though somebody had added it by hand."""
    with fakes.fake_providers(events=[fakes.event(
        attendees=["studio@example.com", "marie@example.com"],
    )]):
        calendar_sync.sync_calendar(account)

    assert CalendarEvent.query.one().guest_list == ["marie@example.com"]


def test_the_edit_form_does_not_repeat_the_client_in_the_guest_box(
    account, client_record,
):
    """They're invited by *being* the linked client; listing the address too
    would say it twice, and keep them invited after an unlink."""
    with fakes.fake_providers(events=[fakes.event(
        attendees=["studio@example.com", "marie@example.com", "cutter@example.com"],
    )]):
        calendar_sync.sync_calendar(account)

    assert CalendarEvent.query.one().extra_guests == ["cutter@example.com"]


def test_editing_a_title_leaves_the_guest_list_alone(logged_in, csrf, account, company):
    """Patching attendees *replaces* them, so a call that doesn't mean to
    change guests must not send the key at all."""
    with fakes.fake_providers(events=[fakes.event(attendees=["marie@example.com"])]):
        calendar_sync.sync_calendar(account)
    stored = CalendarEvent.query.one()

    with fakes.fake_providers():
        calendar_service.update_event(company.id, stored.id, title="Renamed")

    assert "attendees" not in _last_patch()


def test_saving_the_edit_form_does_not_uninvite_the_existing_guests(
    logged_in, csrf, account, client_record,
):
    """The regression the old "no guest field" rule existed to prevent — now
    prevented by the form knowing the list instead of by refusing to show it.
    """
    with fakes.fake_providers(events=[fakes.event(
        attendees=["studio@example.com", "marie@example.com", "cutter@example.com"],
    )]):
        calendar_sync.sync_calendar(account)
    stored = CalendarEvent.query.one()
    stored.client_id = client_record.id
    db.session.commit()

    with fakes.fake_providers():
        logged_in.post(f"/calendar/events/{stored.id}", data=_event_form(
            csrf, title="Renamed", client_id=client_record.id,
            attendees="cutter@example.com",
        ))

    patched = _last_patch()["attendees"]
    assert sorted(patched) == [
        "cutter@example.com", "marie@example.com", "studio@example.com",
    ]


def test_a_guest_can_actually_be_removed(logged_in, csrf, account, client_record):
    """The other half: if the form can only ever add, it isn't an editor."""
    with fakes.fake_providers(events=[fakes.event(
        attendees=["studio@example.com", "cutter@example.com"],
    )]):
        calendar_sync.sync_calendar(account)
    stored = CalendarEvent.query.one()

    with fakes.fake_providers():
        logged_in.post(f"/calendar/events/{stored.id}", data=_event_form(
            csrf, title="Fitting", attendees="",
        ))

    assert _last_patch()["attendees"] == ["studio@example.com"]


def test_the_organiser_is_not_restored_onto_an_event_that_never_had_them(
    logged_in, csrf, account,
):
    """Keeping the organiser is about not dropping them, not about adding
    ourselves to our own reminder."""
    with fakes.fake_providers(events=[fakes.event(attendees=[])]):
        calendar_sync.sync_calendar(account)
    stored = CalendarEvent.query.one()

    with fakes.fake_providers():
        logged_in.post(f"/calendar/events/{stored.id}", data=_event_form(
            csrf, title="Fitting", attendees="cutter@example.com",
        ))

    assert _last_patch()["attendees"] == ["cutter@example.com"]


def test_repointing_an_event_at_another_client_moves_the_invitation(
    logged_in, csrf, account, company, client_record,
):
    """The guest list is resolved against the client the event is about to
    have, not the one it had a moment ago."""
    other = Client(
        company_id=company.id, first_name="Luc", last_name="Bertrand",
        email="luc@example.com",
    )
    db.session.add(other)
    db.session.flush()

    with fakes.fake_providers(events=[fakes.event(attendees=["marie@example.com"])]):
        calendar_sync.sync_calendar(account)
    stored = CalendarEvent.query.one()
    stored.client_id = client_record.id
    db.session.commit()

    with fakes.fake_providers():
        logged_in.post(f"/calendar/events/{stored.id}", data=_event_form(
            csrf, title="Fitting", client_id=other.id, attendees="",
        ))

    assert _last_patch()["attendees"] == ["luc@example.com"]


def test_editing_notifies_only_when_asked(logged_in, csrf, account, client_record):
    with fakes.fake_providers(events=[fakes.event(attendees=["marie@example.com"])]):
        calendar_sync.sync_calendar(account)
    stored = CalendarEvent.query.one()

    with fakes.fake_providers():
        logged_in.post(f"/calendar/events/{stored.id}", data=_event_form(
            csrf, title="Moved", client_id=client_record.id,
        ))
    assert _last_patch().get("notify") is False

    with fakes.fake_providers():
        logged_in.post(f"/calendar/events/{stored.id}", data=_event_form(
            csrf, title="Moved again", client_id=client_record.id, send_invite="1",
        ))
    assert _last_patch().get("notify") is True
