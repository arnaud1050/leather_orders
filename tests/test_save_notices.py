"""
A save's message shows in the section whose button was pressed
(REQUIREMENTS.md MOD8, CLAUDE.md hard rule 17).

The wiring is three pieces in `templates/_save_notice.html`: a form names
its section (`notice_field`), the route stashes the message with that name,
and the page renders it in the matching slot (`notice_slot`), or at the top
when no slot on the page has that name (`page_notice`). What breaks quietly
is a name that doesn't match, so:

- every page is checked for forms naming a slot it doesn't render, and for
  slots that don't actually show a message sent to them;
- each kind of save is posted for real and its message looked for in the
  right slot, in the right colour — green for a save that went through, red
  for one that was refused.
"""

import html
import re
from datetime import timedelta

import pytest
from markupsafe import escape

from communications.models import (
    RULE_CONVERT, RULE_HIDE, CalendarEvent, EmailThread, utcnow,
)
from communications.services import email_service, sender_rules
from communications.sync import calendar_sync
from models import OrderType, SourceOption, User, db
from tests import fakes

SLOT = re.compile(r'<div class="notice-slot" data-notice-slot="([^"]+)">(.*?)</div>', re.S)
FIELD = re.compile(r'name="notice_section" value="([^"]+)"')
NOTICE = re.compile(
    r'<p data-save-notice class="save-notice save-notice--(\w+)" role="(\w+)">(.*?)</p>', re.S)


def located(body: str, message: str) -> tuple[str, str]:
    """(slot name, or "top"; "success" or "error") for the one notice
    carrying `message`."""
    text = str(escape(message))
    notices = [m for m in NOTICE.finditer(body) if text in m.group(3)]
    assert len(notices) == 1, f"expected one notice containing {message!r}, found {len(notices)}"
    style, role = notices[0].group(1), notices[0].group(2)
    assert role == ("alert" if style == "error" else "status")
    for name, inner in SLOT.findall(body):
        if text in inner:
            return name, style
    return "top", style


def only_message(body: str) -> str:
    """The text of the page's one save notice, for a message the test
    doesn't spell out (a service's own wording, a sync summary)."""
    notices = NOTICE.findall(body)
    assert len(notices) == 1, f"expected one notice, found {len(notices)}"
    return html.unescape(notices[0][2])


def seed(test_client, key: str, notice: dict) -> None:
    with test_client.session_transaction() as session:
        session[key] = notice


# --- fixtures ---------------------------------------------------------------

@pytest.fixture
def studio(company, account, client_record, thread, lead_thread, order):
    """Everything that makes each tenant page render all of its forms:
    rules with a mapping, saved AI keys, a lead, a dismissed lead, a
    calendar event in this week, an order type, a source option, an
    inventory unit and type, and an order to invoice."""
    from ai import services as ai_services
    from inventory import services as inventory_services

    db.session.add(OrderType(company_id=company.id, label="Custom Order", sort_order=0))
    db.session.add(SourceOption(company_id=company.id, label="Instagram", sort_order=0))
    inventory_services.add_unit(company.id, "pair")
    inventory_services.add_type(company.id, "Leather")

    sender_rules.add_rule(company.id, "@news.example", RULE_HIDE)
    convert = sender_rules.add_rule(company.id, "@forms.example", RULE_CONVERT)
    sender_rules.add_field(company.id, convert.id, "Name", "name")
    ai_services.save_reply_settings(company.id, api_key="sk-test-reply")
    ai_services.save_render_settings(company.id, api_key="AIza-test-render")

    dismissed = EmailThread(
        company_id=company.id, email_account_id=account.id,
        provider_thread_id="t-old", subject="Old enquiry", last_message_date=utcnow(),
    )
    db.session.add(dismissed)
    db.session.flush()
    email_service.dismiss_thread(company.id, dismissed.id)

    with fakes.fake_providers(events=[fakes.event()]):
        calendar_sync.sync_calendar(account)
    db.session.commit()
    return {
        "client": client_record, "thread": thread, "lead": lead_thread,
        "event": CalendarEvent.query.one(), "order": order,
    }


def tenant_pages(studio):
    client, lead = studio["client"], studio["lead"]
    return [
        ("/settings/integrations", "comms_notice"),
        ("/settings/ai", "ai_notice"),
        ("/mail/leads", "comms_notice"),
        ("/mail/leads?show=dismissed", "comms_notice"),
        (f"/mail/threads/{lead.id}", "comms_notice"),
        (f"/mail/threads/{studio['thread'].id}", "comms_notice"),
        (f"/clients/{client.id}/emails", "comms_notice"),
        (f"/clients/{client.id}", "comms_notice"),
        ("/calendar", "comms_notice"),
        ("/week", "comms_notice"),
        ("/settings/orders", "settings_notice"),
        ("/settings/clients", "settings_notice"),
        ("/settings/inventory", "settings_notice"),
        ("/settings/account", "settings_notice"),
        ("/settings/general", "settings_notice"),
        ("/settings/invoicing", "settings_notice"),
    ]


ADMIN_PAGES = [
    ("/admin/companies", "admin_notice"),
    ("/admin/companies/{company}", "admin_notice"),
    ("/admin/platform-admins", "admin_notice"),
    ("/admin/settings", "admin_notice"),
]


def _check_wiring(test_client, pages):
    """Every form names a slot its page renders; every slot shows the
    notice sent to it; a notice for a slot that isn't there goes to the
    top rather than vanishing."""
    named = 0
    for url, key in pages:
        body = test_client.get(url, follow_redirects=True).get_data(as_text=True)
        slots = [name for name, _ in SLOT.findall(body)]
        assert len(slots) == len(set(slots)), f"{url}: a slot name is used twice"
        for section in set(FIELD.findall(body)):
            named += 1
            assert section in slots, f"{url}: a form names {section!r}, which has no slot here"

        for section in slots:
            seed(test_client, key, {"message": f"probe {section}", "category": "success",
                                    "section": section})
            again = test_client.get(url, follow_redirects=True).get_data(as_text=True)
            assert located(again, f"probe {section}") == (section, "success"), url

        seed(test_client, key, {"message": "probe stray", "category": "error",
                                "section": "not-on-this-page"})
        again = test_client.get(url, follow_redirects=True).get_data(as_text=True)
        assert located(again, "probe stray") == ("top", "error"), url
    return named


def test_every_tenant_page_is_wired(logged_in, studio):
    invoice = _invoice_for(logged_in, studio["order"])
    pages = tenant_pages(studio) + [(f"/invoices/{invoice.id}", "billing_notice")]
    assert _check_wiring(logged_in, pages) >= 30


def _invoice_for(test_client, order):
    from billing.models import Invoice

    test_client.post(f"/subjects/{order.id}/invoice", data={})
    return Invoice.query.one()


def test_every_admin_page_is_wired(admin_client, company, user):
    pages = [(url.format(company=company.id), key) for url, key in ADMIN_PAGES]
    assert _check_wiring(admin_client, pages) >= 8


# Routes that leave a save notice, found by what their source calls. Two
# are left out on purpose: the Google connect button, whose result comes
# back through the OAuth callback (which names its section itself), and
# documents', whose messages render inside their own section with no slot.
LEAVES_A_NOTICE = re.compile(
    r"\b(_flash|_report|_flash_settings_notice|_thread_action|_event_refused)\(")
NAMES_ITS_OWN_SECTION = {"communications.google_connect"}
TEMPLATE_DIRS = ["templates", "communications", "ai", "inventory", "admin"]
FORM = re.compile(r"<form\b[^>]*?url_for\('([\w.]+)'.*?</form>", re.S)


def test_every_form_that_leaves_a_message_names_its_section(app):
    """Otherwise its message silently goes back to the top of the page."""
    import inspect
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    leaves_notice = {
        endpoint for endpoint, view in app.view_functions.items()
        if not endpoint.startswith("documents.")
        # unwrap: login_required and platform_admin_required are wrappers,
        # and it's the view's own source that says what it reports.
        and LEAVES_A_NOTICE.search(inspect.getsource(inspect.unwrap(view)))
    } - NAMES_ITS_OWN_SECTION
    assert len(leaves_notice) >= 25, "the scan found too few routes to mean anything"

    checked = 0
    for directory in TEMPLATE_DIRS:
        for path in sorted((root / directory).rglob("*.html")):
            for form in FORM.finditer(path.read_text(encoding="utf-8")):
                if form.group(1) in leaves_notice:
                    checked += 1
                    assert "notice_field(" in form.group(0), (
                        f"{path.relative_to(root)}: the form posting to {form.group(1)} "
                        "needs {{ notice_field('…') }} naming the section it's in")
    assert checked >= 30


def test_a_notice_from_before_this_change_still_shows():
    """A session written by the old code carries no `section`; it falls
    back to the top rather than erroring or vanishing."""
    from app import app as flask_app

    with flask_app.test_request_context():
        macros = flask_app.jinja_env.get_template("_save_notice.html").module
        html = str(macros.page_notice({"message": "Saved.", "category": "success"}, ["a"]))
    assert 'save-notice--success' in html and "Saved." in html


# --- Settings → Email/Calendar ------------------------------------------------

def test_rule_messages_show_under_automatic_handling(logged_in, csrf, company):
    form = {"csrf_token": csrf, "pattern": "@news.example", "action": RULE_HIDE,
            "notice_section": "rules"}
    body = logged_in.post("/integrations/rules", data=form, follow_redirects=True).get_data(as_text=True)
    assert located(body, "Mail from @news.example") == ("rules", "success")

    body = logged_in.post("/integrations/rules", data=form, follow_redirects=True).get_data(as_text=True)
    message = only_message(body)
    assert located(body, message) == ("rules", "error")


@pytest.mark.parametrize("which", ["email", "calendar"])
def test_sync_settings_report_in_their_own_section(logged_in, csrf, company, which):
    body = logged_in.post("/integrations/sync-settings", data={
        "csrf_token": csrf, "section": which, "notice_section": f"{which}-sync",
    }, follow_redirects=True).get_data(as_text=True)

    assert located(body, "Sync settings saved.") == (f"{which}-sync", "success")


@pytest.mark.parametrize("url, section", [
    ("/integrations/sync", "email-sync"),
    ("/integrations/calendar/sync", "calendar-sync"),
    ("/integrations/sync-all", "accounts"),
])
def test_each_sync_button_reports_beside_itself(logged_in, csrf, account, url, section):
    with fakes.fake_providers():
        body = logged_in.post(url, data={
            "csrf_token": csrf, "notice_section": section,
            "return_to": "/settings/integrations",
        }, follow_redirects=True).get_data(as_text=True)

    message = only_message(body)
    assert located(body, message)[0] == section


def test_a_cancelled_google_sign_in_reports_under_connected_accounts(logged_in, company):
    """No form posts the callback, so the route names the section itself."""
    body = logged_in.get("/integrations/google/callback?error=access_denied",
                         follow_redirects=True).get_data(as_text=True)

    assert located(body, "Google sign-in was cancelled") == ("accounts", "error")


# --- Settings → AI ----------------------------------------------------------

@pytest.mark.parametrize("url, section, message", [
    ("/settings/ai/replies", "replies", "Inquiry reply settings saved."),
    ("/settings/ai/renders", "renders", "Rendering settings saved."),
    ("/settings/ai/replies/key/delete", "replies", "OpenAI API key removed."),
    ("/settings/ai/renders/key/delete", "renders", "Google AI API key removed."),
])
def test_ai_messages_show_in_their_own_section(logged_in, company, url, section, message):
    body = logged_in.post(url, data={"notice_section": section},
                          follow_redirects=True).get_data(as_text=True)

    assert located(body, message) == (section, "success")


# --- mail -------------------------------------------------------------------

def test_hiding_a_lead_reports_above_the_list(logged_in, csrf, lead_thread):
    body = logged_in.post(f"/mail/threads/{lead_thread.id}/dismiss", data={
        "csrf_token": csrf, "return_to": "/mail/leads", "notice_section": "threads",
    }, follow_redirects=True).get_data(as_text=True)

    assert located(body, "Conversation hidden.") == ("threads", "success")


def test_sync_now_on_leads_reports_under_its_button(logged_in, csrf, account):
    with fakes.fake_providers():
        body = logged_in.post("/integrations/sync", data={
            "csrf_token": csrf, "return_to": "/mail/leads", "notice_section": "sync",
        }, follow_redirects=True).get_data(as_text=True)

    message = only_message(body)
    assert located(body, message) == ("sync", "success")


@pytest.mark.parametrize("page", ["thread", "client"])
def test_a_refused_send_reports_in_the_compose_section(logged_in, csrf, thread, client_record, page):
    return_to = (f"/mail/threads/{thread.id}" if page == "thread"
                 else f"/clients/{client_record.id}/emails")
    body = logged_in.post("/mail/send", data={
        "csrf_token": csrf, "return_to": return_to, "notice_section": "compose",
        "to": "", "subject": "Hello", "body_text": "Hi",
    }, follow_redirects=True).get_data(as_text=True)

    assert located(body, "At least one valid recipient") == ("compose", "error")


def test_a_sent_message_reports_in_the_compose_section(logged_in, csrf, thread):
    with fakes.fake_providers():
        body = logged_in.post("/mail/send", data={
            "csrf_token": csrf, "return_to": f"/mail/threads/{thread.id}",
            "notice_section": "compose", "to": "marie@example.com",
            "subject": "Re: Briefcase", "body_text": "Next week.", "thread_id": thread.id,
        }, follow_redirects=True).get_data(as_text=True)

    assert located(body, "Message sent.") == ("compose", "success")


def test_creating_a_client_reports_on_the_client_page(logged_in, csrf, lead_thread):
    """The report's one message that landed on a different page entirely."""
    response = logged_in.post(f"/mail/threads/{lead_thread.id}/create-client", data={
        "csrf_token": csrf, "first_name": "Jean", "last_name": "Tremblay",
        "email": "stranger@example.com", "notice_section": "create-client",
        "return_to": "/mail/leads",
    }, follow_redirects=True)
    body = response.get_data(as_text=True)

    assert response.request.path.startswith("/clients/")
    assert located(body, "Created Jean Tremblay") == ("client", "success")
    # Consumed there, so it can't turn up later on Leads out of context.
    assert "Created Jean Tremblay" not in logged_in.get("/mail/leads").get_data(as_text=True)


def test_a_refusal_for_a_section_no_longer_shown_goes_to_the_top(logged_in, csrf, thread):
    """Creating a client from a conversation that already has one: the
    "Create a client" section isn't on the page any more, so its message
    falls back to the top rather than disappearing."""
    body = logged_in.post(f"/mail/threads/{thread.id}/create-client", data={
        "csrf_token": csrf, "email": "marie@example.com",
        "notice_section": "create-client", "return_to": f"/mail/threads/{thread.id}",
    }, follow_redirects=True).get_data(as_text=True)

    assert located(body, "already linked to a client") == ("top", "error")


# --- calendar ---------------------------------------------------------------

def _event_form(csrf, **overrides):
    today = (utcnow() - timedelta(hours=12)).date().isoformat()
    form = {
        "csrf_token": csrf, "title": "Second fitting", "notice_section": "event-new",
        "start_date": today, "start_time": "14:00", "end_date": today,
        "end_time": "15:00", "location": "Studio", "return_to": "/week",
    }
    form.update(overrides)
    return form


def test_a_refused_new_event_reopens_its_dialog_with_what_was_typed(logged_in, csrf, account):
    with fakes.fake_providers():
        body = logged_in.post("/calendar/events/new", data=_event_form(
            csrf, start_time="15:00", end_time="14:00", description="Bring the swatches",
        ), follow_redirects=True).get_data(as_text=True)

    assert CalendarEvent.query.count() == 0
    assert located(body, "ends before it starts") == ("event-new", "error")
    dialog = re.search(r'<dialog id="event-modal-new"[^>]*>(.*?)</dialog>', body, re.S)
    assert "data-open-on-load" in re.search(r'<dialog id="event-modal-new"[^>]*>', body).group(0)
    assert 'value="Second fitting"' in dialog.group(1)
    assert 'value="15:00"' in dialog.group(1)
    assert "Bring the swatches</textarea>" in dialog.group(1)


def test_a_refused_edit_reopens_that_events_dialog(logged_in, csrf, studio):
    event = studio["event"]
    with fakes.fake_providers():
        body = logged_in.post(f"/calendar/events/{event.id}", data=_event_form(
            csrf, title="Moved fitting", start_date="", notice_section=f"event-{event.id}",
        ), follow_redirects=True).get_data(as_text=True)

    assert located(body, "needs a start date") == (f"event-{event.id}", "error")
    tag = re.search(rf'<dialog id="event-modal-{event.id}"[^>]*>', body).group(0)
    assert "data-open-on-load" in tag
    assert 'value="Moved fitting"' in body
    assert CalendarEvent.query.one().title == "Fitting"


def test_a_saved_event_reports_under_the_calendar_buttons(logged_in, csrf, account):
    with fakes.fake_providers():
        body = logged_in.post("/calendar/events/new", data=_event_form(csrf),
                              follow_redirects=True).get_data(as_text=True)

    assert located(body, "Event added to your Google Calendar.") == ("calendar", "success")
    assert not re.search(r"<dialog[^>]*data-open-on-load", body)


def test_an_event_refused_without_a_calendar_still_shows(logged_in, csrf, company):
    """No calendar connected: no dialogs on the page to reopen, so the
    message goes to the top instead."""
    with fakes.fake_providers():
        body = logged_in.post("/calendar/events/new", data=_event_form(csrf),
                              follow_redirects=True).get_data(as_text=True)

    assert located(body, "No Google account is connected") == ("top", "error")


def test_calendar_sync_now_reports_under_its_button(logged_in, csrf, account):
    with fakes.fake_providers():
        body = logged_in.post("/integrations/calendar/sync", data={
            "csrf_token": csrf, "return_to": "/week", "notice_section": "calendar",
        }, follow_redirects=True).get_data(as_text=True)

    message = only_message(body)
    assert located(body, message)[0] == "calendar"


# --- core settings ----------------------------------------------------------

@pytest.mark.parametrize("url, page, section, label", [
    ("/settings/order-types", "/settings/orders", "order-types", "Custom Order"),
    ("/settings/sources", "/settings/clients", "sources", "Instagram"),
    ("/settings/inventory-types", "/settings/inventory", "inventory-types", "Leather"),
])
def test_a_duplicate_is_refused_in_its_own_section(logged_in, company, url, page, section, label):
    logged_in.post(url, data={"label": label})
    body = logged_in.post(url, data={"label": label, "notice_section": section},
                          follow_redirects=True).get_data(as_text=True)

    assert located(body, "already exists") == (section, "error")


def test_a_form_naming_no_section_still_reports_at_the_top(logged_in, company):
    logged_in.post("/settings/order-types", data={"label": "Custom Order"})
    body = logged_in.post("/settings/order-types", data={"label": "Custom Order"},
                          follow_redirects=True).get_data(as_text=True)

    assert located(body, "already exists") == ("top", "error")


def test_a_malformed_section_is_treated_as_none(logged_in, company):
    logged_in.post("/settings/order-types", data={"label": "Custom Order"})
    body = logged_in.post("/settings/order-types", data={
        "label": "Custom Order", "notice_section": '"><script>x</script>',
    }, follow_redirects=True).get_data(as_text=True)

    assert located(body, "already exists") == ("top", "error")
    assert "<script>x" not in body


def test_a_malformed_section_is_treated_as_none_by_ai_too(logged_in, company):
    """ai/ checks the name by hand (its imports are held to a short list),
    so it gets its own test rather than sharing the settings one."""
    body = logged_in.post("/settings/ai/replies", data={"notice_section": "Replies!"},
                          follow_redirects=True).get_data(as_text=True)

    assert located(body, "Inquiry reply settings saved.") == ("top", "success")


def test_password_messages_show_under_password(logged_in, company):
    body = logged_in.post("/settings/account/password", data={
        "current_password": "wrong", "new_password": "longenough",
        "confirm_password": "longenough", "notice_section": "password",
    }, follow_redirects=True).get_data(as_text=True)
    assert located(body, "That isn't your current password.") == ("password", "error")

    body = logged_in.post("/settings/account/password", data={
        "current_password": "changeme", "new_password": "longenough",
        "confirm_password": "longenough", "notice_section": "password",
    }, follow_redirects=True).get_data(as_text=True)
    assert located(body, "Password changed.") == ("password", "success")


def test_signature_saved_shows_under_signature(logged_in, company):
    body = logged_in.post("/settings/account/signature", data={
        "signature": "Joe", "notice_section": "signature",
    }, follow_redirects=True).get_data(as_text=True)

    assert located(body, "Signature saved.") == ("signature", "success")


def test_a_required_password_change_stays_an_amber_warning(logged_in, user):
    """A standing condition of the account, not the result of a save: amber,
    and not marked as the message to scroll to."""
    db.session.get(User, user.id).must_change_password = True
    db.session.commit()

    body = logged_in.get("/settings/account").get_data(as_text=True)
    assert ('<p class="warning-note">You need to set a new password before continuing.</p>'
            in body)
    assert "data-save-notice" not in body


def test_a_duplicate_document_type_is_red_in_its_section(logged_in, company):
    logged_in.post("/settings/document-types", data={"label": "Mockups"})
    body = logged_in.post("/settings/document-types", data={"label": "Mockups"},
                          follow_redirects=True).get_data(as_text=True)

    section = body.split("<h2>Document types</h2>")[1].split("</section>")[0]
    assert located(section, "already exists")[1] == "error"


def test_a_refused_invoice_colour_is_red_in_its_section(logged_in, company):
    body = logged_in.post("/settings/invoicing/appearance", data={"primary_color": "nope"},
                          follow_redirects=True).get_data(as_text=True)

    section = body.split("<h2>Invoice appearance</h2>")[1]
    assert located(section, "wasn't recognised")[1] == "error"


# --- platform admin ---------------------------------------------------------

def test_company_messages_show_in_their_own_section(admin_client, company, user):
    body = admin_client.post(f"/admin/companies/{company.id}", data={
        "name": "By Monsieur", "timezone": "America/Vancouver", "notice_section": "company",
    }, follow_redirects=True).get_data(as_text=True)
    assert located(body, "Company saved.") == ("company", "success")

    body = admin_client.post(f"/admin/companies/{company.id}/active", data={
        "active": "0", "notice_section": "access",
    }, follow_redirects=True).get_data(as_text=True)
    assert located(body, "deactivated") == ("access", "success")


def test_user_messages_show_beside_the_form_that_left_them(admin_client, company, user):
    body = admin_client.post(f"/admin/companies/{company.id}/users", data={
        "email": "second@example.com", "password": "short", "notice_section": "add-user",
    }, follow_redirects=True).get_data(as_text=True)
    message = only_message(body)
    assert located(body, message) == ("add-user", "error")

    body = admin_client.post(f"/admin/companies/{company.id}/users", data={
        "email": "second@example.com", "password": "longenough", "notice_section": "add-user",
    }, follow_redirects=True).get_data(as_text=True)
    assert located(body, "User added.") == ("add-user", "success")

    body = admin_client.post(f"/admin/users/{user.id}/password", data={
        "password": "anotherlong", "notice_section": "users",
    }, follow_redirects=True).get_data(as_text=True)
    assert located(body, "Password reset for") == ("users", "success")


def test_creating_a_company_reports_where_it_lands(admin_client):
    body = admin_client.post("/admin/companies", data={
        "name": "", "notice_section": "add-company",
    }, follow_redirects=True).get_data(as_text=True)
    message = only_message(body)
    assert located(body, message) == ("add-company", "error")

    response = admin_client.post("/admin/companies", data={
        "name": "New Studio", "timezone": "America/Vancouver",
        "admin_email": "owner@new.example", "admin_password": "longenough",
        "notice_section": "add-company",
    }, follow_redirects=True)
    assert located(response.get_data(as_text=True), "New Studio created.") == ("company", "success")


def test_platform_admin_messages_show_beside_their_forms(admin_client, platform_admin):
    body = admin_client.post("/admin/platform-admins", data={
        "email": "staff2@example.com", "password": "longenough", "notice_section": "add-admin",
    }, follow_redirects=True).get_data(as_text=True)
    assert located(body, "Platform admin added.") == ("add-admin", "success")

    body = admin_client.post(f"/admin/users/{platform_admin.id}/password", data={
        "password": "short", "notice_section": "admins",
    }, follow_redirects=True).get_data(as_text=True)
    message = only_message(body)
    assert located(body, message) == ("admins", "error")


def test_announcement_saved_shows_in_its_section(admin_client):
    body = admin_client.post("/admin/settings/announcement", data={
        "message": "Maintenance tonight", "notice_section": "announcement",
    }, follow_redirects=True).get_data(as_text=True)

    assert located(body, "Announcement saved.") == ("announcement", "success")


# --- saves that used to show nothing ------------------------------------------
#
# Each of these saved silently before, so a person had to look for the change
# to know it happened. Now each reports in its own section; a refused delete
# says why instead of quietly doing nothing.

def _post(test_client, url, data):
    return test_client.post(url, data=data, follow_redirects=True).get_data(as_text=True)


def test_saving_the_time_zone_says_which_zone(logged_in, company):
    body = _post(logged_in, "/settings/general", {
        "timezone": "America/Toronto", "notice_section": "timezone"})
    assert located(body, "Time zone saved. Times now show in") == ("timezone", "success")

    body = _post(logged_in, "/settings/general", {
        "timezone": "Mars/Olympus", "notice_section": "timezone"})
    assert located(body, "isn't one the app offers") == ("timezone", "error")


def test_order_type_saves_report_under_order_types(logged_in, studio):
    body = _post(logged_in, "/settings/order-types", {
        "label": "White Label", "notice_section": "order-types"})
    assert located(body, 'Order type "White Label" added.') == ("order-types", "success")

    white = OrderType.query.filter_by(label="White Label").one()
    body = _post(logged_in, f"/settings/order-types/{white.id}/toggle",
                 {"notice_section": "order-types"})
    assert located(body, '"White Label" hidden.') == ("order-types", "success")
    body = _post(logged_in, f"/settings/order-types/{white.id}/toggle",
                 {"notice_section": "order-types"})
    assert located(body, '"White Label" is offered again') == ("order-types", "success")

    body = _post(logged_in, f"/settings/order-types/{white.id}/delete",
                 {"notice_section": "order-types"})
    assert located(body, '"White Label" deleted.') == ("order-types", "success")


def test_deleting_an_order_type_in_use_says_why_it_was_kept(logged_in, studio):
    custom = OrderType.query.filter_by(label="Custom Order").one()
    studio["order"].order_type_id = custom.id
    db.session.commit()

    body = _post(logged_in, f"/settings/order-types/{custom.id}/delete",
                 {"notice_section": "order-types"})

    assert located(body, "orders are tagged with it. Hide it instead.") == ("order-types", "error")
    assert db.session.get(OrderType, custom.id) is not None


def test_hiding_an_orders_list_column_reports_under_its_section(logged_in, company):
    body = _post(logged_in, "/settings/order-columns/due/toggle",
                 {"notice_section": "order-columns"})
    assert located(body, "The Due column is now hidden on the Orders list.") == (
        "order-columns", "success")

    body = _post(logged_in, "/settings/order-columns/due/toggle",
                 {"notice_section": "order-columns"})
    assert located(body, "The Due column is now shown") == ("order-columns", "success")


def test_source_option_saves_report_under_their_section(logged_in, studio):
    body = _post(logged_in, "/settings/sources", {
        "label": "Word of Mouth", "notice_section": "sources"})
    assert located(body, 'Option "Word of Mouth" added.') == ("sources", "success")

    word = SourceOption.query.filter_by(label="Word of Mouth").one()
    instagram = SourceOption.query.filter_by(label="Instagram").one()
    body = _post(logged_in, f"/settings/sources/{word.id}/toggle", {"notice_section": "sources"})
    assert located(body, '"Word of Mouth" hidden from client pages.') == ("sources", "success")

    # The text box can only be on one option: moving it says where from.
    _post(logged_in, f"/settings/sources/{instagram.id}/set-other", {"notice_section": "sources"})
    body = _post(logged_in, f"/settings/sources/{word.id}/set-other", {"notice_section": "sources"})
    assert located(body, '"Word of Mouth" now has a text box on the client page. '
                         '"Instagram" no longer does.') == ("sources", "success")
    body = _post(logged_in, f"/settings/sources/{word.id}/set-other", {"notice_section": "sources"})
    assert located(body, '"Word of Mouth" no longer has a text box.') == ("sources", "success")

    body = _post(logged_in, f"/settings/sources/{word.id}/delete", {"notice_section": "sources"})
    assert located(body, '"Word of Mouth" deleted.') == ("sources", "success")


def test_deleting_a_source_option_in_use_says_why_it_was_kept(logged_in, studio):
    instagram = SourceOption.query.filter_by(label="Instagram").one()
    studio["client"].sources.append(instagram)
    db.session.commit()

    body = _post(logged_in, f"/settings/sources/{instagram.id}/delete",
                 {"notice_section": "sources"})

    assert located(body, "clients have picked it. Hide it instead.") == ("sources", "error")


def test_invoicing_saves_report_in_their_own_sections(logged_in, company):
    body = _post(logged_in, "/settings/company", {
        "name": "By Monsieur", "province": "BC", "notice_section": "company-details"})
    assert located(body, "Company details saved.") == ("company-details", "success")

    body = _post(logged_in, "/settings/company", {"name": "", "notice_section": "company-details"})
    assert located(body, "The company name can't be blank, so it was kept.") == (
        "company-details", "success")

    body = _post(logged_in, "/settings/invoicing", {
        "invoice_prefix": "BM", "payment_instructions": "", "notice_section": "invoicing"})
    assert located(body, "Invoicing settings saved.") == ("invoicing", "success")


def test_invoice_appearance_reports_in_its_section(logged_in, company):
    """Its routes name the slot themselves, so no notice_section is posted."""
    body = _post(logged_in, "/settings/invoicing/appearance", {"primary_color": "#2f6fa6"})
    assert located(body, "Invoice appearance saved.") == ("appearance", "success")

    body = _post(logged_in, "/settings/invoicing/appearance", {"primary_color": "nope"})
    assert located(body, "wasn't recognised") == ("appearance", "error")

    body = _post(logged_in, "/settings/invoicing/logo/delete", {})
    assert located(body, "Logo removed.") == ("appearance", "success")


def test_unit_saves_report_under_units(logged_in, studio):
    from inventory.models import InventoryUnit

    body = _post(logged_in, "/settings/inventory-units", {"key": "sheet", "notice_section": "units"})
    assert located(body, '"Sheet" added.') == ("units", "success")
    body = _post(logged_in, "/settings/inventory-units", {"key": "sheet", "notice_section": "units"})
    assert located(body, "That unit is already in the list.") == ("units", "error")

    sheet = InventoryUnit.query.filter_by(key="sheet").one()
    body = _post(logged_in, f"/settings/inventory-units/{sheet.id}/toggle",
                 {"notice_section": "units"})
    assert located(body, '"Sheet" hidden.') == ("units", "success")
    body = _post(logged_in, f"/settings/inventory-units/{sheet.id}/delete",
                 {"notice_section": "units"})
    assert located(body, '"Sheet" deleted.') == ("units", "success")


def test_deleting_a_unit_or_type_in_use_says_why(logged_in, studio, company):
    from inventory import services as inventory_services
    from inventory.models import InventoryType, InventoryUnit

    leather = InventoryType.query.filter_by(label="Leather").one()
    inventory_services.add_item(company.id, "Brass buckle", "pair", leather.id, 4, 2.5)
    pair = InventoryUnit.query.filter_by(key="pair").one()

    body = _post(logged_in, f"/settings/inventory-units/{pair.id}/delete",
                 {"notice_section": "units"})
    assert located(body, "items are measured in it. Hide it instead.") == ("units", "error")

    body = _post(logged_in, f"/settings/inventory-types/{leather.id}/delete",
                 {"notice_section": "inventory-types"})
    assert located(body, "items are tagged with it. Hide it instead.") == (
        "inventory-types", "error")


def test_inventory_type_and_column_saves_report_in_their_sections(logged_in, studio):
    from inventory.models import InventoryType

    body = _post(logged_in, "/settings/inventory-types", {
        "label": "Lining", "notice_section": "inventory-types"})
    assert located(body, 'Inventory type "Lining" added.') == ("inventory-types", "success")

    lining = InventoryType.query.filter_by(label="Lining").one()
    body = _post(logged_in, f"/settings/inventory-types/{lining.id}/toggle",
                 {"notice_section": "inventory-types"})
    assert located(body, '"Lining" hidden.') == ("inventory-types", "success")
    body = _post(logged_in, f"/settings/inventory-types/{lining.id}/delete",
                 {"notice_section": "inventory-types"})
    assert located(body, '"Lining" deleted.') == ("inventory-types", "success")

    body = _post(logged_in, "/settings/inventory-columns/reference/toggle",
                 {"notice_section": "inventory-columns"})
    assert located(body, "The Ref column is now hidden on the Inventory list.") == (
        "inventory-columns", "success")


def test_document_type_saves_report_in_their_section(logged_in, company):
    from documents.models import DocumentType

    body = _post(logged_in, "/settings/document-types", {"label": "Mockups"})
    section = body.split("<h2>Document types</h2>")[1].split("</section>")[0]
    assert located(section, 'Document type "Mockups" added.')[1] == "success"

    mockups = DocumentType.query.filter_by(label="Mockups").one()
    body = _post(logged_in, f"/settings/document-types/{mockups.id}/toggle", {})
    section = body.split("<h2>Document types</h2>")[1].split("</section>")[0]
    assert located(section, '"Mockups" hidden.')[1] == "success"

    body = _post(logged_in, f"/settings/document-types/{mockups.id}/delete", {})
    section = body.split("<h2>Document types</h2>")[1].split("</section>")[0]
    assert located(section, '"Mockups" deleted.')[1] == "success"


def test_account_switches_report_under_connected_accounts(logged_in, csrf, account):
    body = _post(logged_in, f"/integrations/accounts/{account.id}/flags", {
        "csrf_token": csrf, "sync_enabled": "0", "notice_section": "accounts"})
    assert located(body, "Sync paused for studio@example.com.") == ("accounts", "success")

    body = _post(logged_in, f"/integrations/accounts/{account.id}/flags", {
        "csrf_token": csrf, "sync_enabled": "1", "notice_section": "accounts"})
    assert located(body, "Sync resumed for studio@example.com.") == ("accounts", "success")

    body = _post(logged_in, f"/integrations/accounts/{account.id}/flags", {
        "csrf_token": csrf, "is_default": "1", "notice_section": "accounts"})
    assert located(body, "studio@example.com is now the default sending address.") == (
        "accounts", "success")


def test_invoice_settings_report_what_changed(logged_in, order):
    invoice = _invoice_for(logged_in, order)
    url = f"/invoices/{invoice.id}/status"

    body = _post(logged_in, url, {"status": "draft", "notes": "Thanks!",
                                  "notice_section": "invoice-settings"})
    assert located(body, "Invoice saved.") == ("invoice-settings", "success")

    body = _post(logged_in, url, {"status": "sent", "notice_section": "invoice-settings"})
    assert located(body, "Invoice marked sent. What it says is now frozen.") == (
        "invoice-settings", "success")

    body = _post(logged_in, url, {"status": "void", "notice_section": "invoice-settings"})
    message = only_message(body)
    assert message == "Invoice marked void."
