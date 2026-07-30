"""
The company's display timezone.

Timestamps are stored naive UTC everywhere (see communications/models.py);
this covers the one place that's converted — rendering — plus the setting
that drives it. The point of the display tests is that a Vancouver studio
reading "22:31" gets the time it was there, not in UTC.
"""

from datetime import datetime

import pytest

from models import Company, DEFAULT_TIMEZONE, db

from communications.sync import calendar_sync

from tests import fakes

# 05:31 UTC on Jul 28 is 22:31 the previous evening in Vancouver (PDT, UTC-7)
# and 01:31 the same morning in Toronto (EDT, UTC-4) — a date rollover in one
# direction and not the other, which is exactly the case a naive strftime
# gets wrong.
UTC_MOMENT = datetime(2026, 7, 28, 5, 31)


def test_a_new_company_defaults_to_vancouver(company):
    assert company.timezone == DEFAULT_TIMEZONE == "America/Vancouver"


def test_message_time_renders_in_the_company_zone(logged_in, thread):
    thread.messages[0].received_date = UTC_MOMENT
    db.session.commit()

    body = logged_in.get(f"/mail/threads/{thread.id}").get_data(as_text=True)
    assert "Jul 27, 2026 at 22:31" in body
    # Not the stored UTC value, and not labelled with a zone — there's one
    # setting for the whole company, so naming it on every line says nothing.
    assert "05:31" not in body
    assert "UTC" not in body


def test_changing_the_setting_changes_what_is_shown(logged_in, company, thread):
    thread.messages[0].received_date = UTC_MOMENT
    company.timezone = "America/Toronto"
    db.session.commit()

    body = logged_in.get(f"/mail/threads/{thread.id}").get_data(as_text=True)
    assert "Jul 28, 2026 at 01:31" in body


def test_calendar_event_time_renders_in_the_company_zone(logged_in, account, company):
    """A synced calendar event's start/end (see calendar.html) go through the
    same naive-UTC-to-company-zone conversion as message times, formatted
    12-hour with lowercase am/pm — 19:00-20:00 UTC is noon-1pm in Vancouver."""
    with fakes.fake_providers(events=[
        fakes.event(title="Fitting — Marie", start=datetime(2026, 7, 15, 19, 0)),
    ]):
        calendar_sync.sync_calendar(account)

    body = logged_in.get("/month/2026/7").get_data(as_text=True)
    assert "12:00pm - 1:00pm" in body
    assert "19:00" not in body


def test_thread_list_dates_are_converted_too(logged_in, lead_thread):
    """The date alone, but still converted — 22:31 PDT is the day before."""
    lead_thread.last_message_date = UTC_MOMENT
    db.session.commit()

    body = logged_in.get("/mail/leads").get_data(as_text=True)
    assert "Jul 27, 2026" in body


def test_an_unknown_stored_zone_falls_back_instead_of_500ing(logged_in, company, thread):
    """A zone this Python has no data for must not take a page down."""
    thread.messages[0].received_date = UTC_MOMENT
    company.timezone = "Mars/Olympus_Mons"
    db.session.commit()

    response = logged_in.get(f"/mail/threads/{thread.id}")
    assert response.status_code == 200
    assert "Jul 28, 2026 at 05:31" in response.get_data(as_text=True)  # UTC


# --- the setting itself ---------------------------------------------------

def test_the_zone_is_offered_and_the_current_one_preselected(logged_in, company):
    company.timezone = "America/Toronto"
    db.session.commit()

    body = logged_in.get("/settings/preferences").get_data(as_text=True)
    assert 'value="America/Toronto" selected' in body


def test_saving_a_zone(logged_in, company):
    logged_in.post("/settings/preferences", data={"timezone": "America/Halifax"})
    assert db.session.get(Company, company.id).timezone == "America/Halifax"


@pytest.mark.parametrize("value", ["Mars/Olympus_Mons", "", "america/halifax"])
def test_a_zone_outside_the_offered_list_is_ignored(logged_in, company, value):
    """Storing an unresolvable name would silently push every time to UTC."""
    logged_in.post("/settings/preferences", data={"timezone": value})
    assert db.session.get(Company, company.id).timezone == DEFAULT_TIMEZONE


def test_the_zone_is_per_tenant(company, other_company):
    company.timezone = "America/Toronto"
    db.session.flush()
    assert other_company.timezone == DEFAULT_TIMEZONE
