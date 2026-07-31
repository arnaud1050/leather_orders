"""
The HTTP layer.

Three things matter here and the rest is rendering: every route requires a
login, every unsafe request requires a CSRF token, and no id from a URL
ever reaches another tenant's data.
"""

import pytest

from models import Client, db

from communications.models import EmailAccount, EmailAttachment, EmailThread

from tests import fakes

UNSAFE_ROUTES = [
    "/integrations/google/connect",
    "/integrations/sync-settings",
    "/integrations/sync",
    "/integrations/accounts/1/disconnect",
    "/integrations/accounts/1/flags",
    "/mail/send",
    "/mail/threads/1/create-client",
]

READ_ROUTES = [
    "/settings/integrations",
    "/mail/leads",
    "/integrations/google/callback",
]


# --- authentication -------------------------------------------------------

@pytest.mark.parametrize("path", READ_ROUTES)
def test_read_routes_require_a_login(app, path):
    response = app.test_client().get(path)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


@pytest.mark.parametrize("path", UNSAFE_ROUTES)
def test_post_routes_require_a_login(app, path):
    response = app.test_client().post(path)
    assert response.status_code in (302, 400)
    if response.status_code == 302:
        assert "/login" in response.headers["Location"]


def test_client_email_tab_requires_a_login(app, client_record):
    response = app.test_client().get(f"/clients/{client_record.id}/emails")
    assert response.status_code == 302


# --- CSRF -----------------------------------------------------------------

@pytest.mark.parametrize("path", UNSAFE_ROUTES)
def test_unsafe_requests_without_a_token_are_rejected(logged_in, path):
    """Enforced by a blueprint-wide before_request, so a route added later
    is protected by default rather than if someone remembers."""
    assert logged_in.post(path).status_code == 400


def test_a_wrong_token_is_rejected(logged_in, csrf):
    response = logged_in.post(
        "/integrations/sync-settings", data={"csrf_token": csrf + "x"},
    )
    assert response.status_code == 400


def test_a_valid_token_is_accepted(logged_in, csrf):
    response = logged_in.post(
        "/integrations/sync-settings",
        data={"csrf_token": csrf, "sync_enabled": "on", "sync_frequency": "20"},
    )
    assert response.status_code == 302


def test_get_requests_do_not_need_a_token(logged_in):
    """Requiring one on safe methods would break ordinary links."""
    assert logged_in.get("/settings/integrations").status_code == 200


def test_the_token_is_rendered_into_forms(logged_in, csrf):
    body = logged_in.get("/settings/integrations").get_data(as_text=True)
    assert f'value="{csrf}"' in body


def test_the_session_cookie_is_samesite_lax(app):
    """Defence in depth behind the token: a forged cross-site POST never
    even carries the session."""
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True


# --- pages render ---------------------------------------------------------

def test_integrations_page_lists_a_connected_account(logged_in, account):
    body = logged_in.get("/settings/integrations").get_data(as_text=True)
    assert "studio@example.com" in body
    assert "Connect Gmail" in body


def test_integrations_page_explains_a_missing_configuration(logged_in, monkeypatch):
    from communications import config

    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "")
    body = logged_in.get("/settings/integrations").get_data(as_text=True)
    assert "GOOGLE_CLIENT_ID" in body


def test_integrations_page_warns_about_the_derived_encryption_key(logged_in, monkeypatch):
    monkeypatch.delenv("COMMS_ENCRYPTION_KEY", raising=False)
    body = logged_in.get("/settings/integrations").get_data(as_text=True)
    assert "COMMS_ENCRYPTION_KEY" in body


def test_leads_page_lists_unmatched_threads_only(logged_in, thread, lead_thread):
    body = logged_in.get("/mail/leads").get_data(as_text=True)
    assert "Messenger bag enquiry" in body
    assert "Briefcase timeline" not in body
    # No per-row "Unmatched" pill: every thread on this page is unmatched by
    # definition, which is what the page itself says.
    assert "Unmatched" not in body


def test_client_emails_tab_lists_that_clients_threads(logged_in, client_record, thread):
    body = logged_in.get(f"/clients/{client_record.id}/emails").get_data(as_text=True)
    assert "Briefcase timeline" in body


def test_client_emails_tab_warns_when_no_address_is_on_file(logged_in, client_record):
    client_record.email = ""
    db.session.commit()
    body = logged_in.get(f"/clients/{client_record.id}/emails").get_data(as_text=True)
    assert "no email address on file" in body


def test_thread_page_renders_the_conversation(logged_in, thread):
    body = logged_in.get(f"/mail/threads/{thread.id}").get_data(as_text=True)
    assert "Any update on the briefcase?" in body
    assert "Marie Alarie" in body


def test_thread_page_header_names_the_counterparty_not_our_mailbox(logged_in, thread):
    """The header used to print the account the thread synced through, which
    reads as though it were the client's address."""
    body = logged_in.get(f"/mail/threads/{thread.id}").get_data(as_text=True)
    header = body.split("Messages")[0]
    assert "marie@example.com" in header
    assert "studio@example.com" not in header


def test_thread_page_labels_our_own_messages_you(logged_in, thread):
    message = thread.messages[0]
    message.direction = "outgoing"
    message.sender_name, message.sender = "Studio", "studio@example.com"
    message.recipients = "marie@example.com"
    db.session.commit()

    body = logged_in.get(f"/mail/threads/{thread.id}").get_data(as_text=True)
    assert "You" in body
    # Neither our own address nor the client's belongs on the message: both
    # ends of the conversation are named on the page already.
    assert "studio@example.com" not in body.split("Reply")[0]
    assert "To: " not in body


def test_thread_page_lists_only_third_party_recipients(logged_in, thread):
    thread.messages[0].cc = "notary@example.com"
    db.session.commit()

    body = logged_in.get(f"/mail/threads/{thread.id}").get_data(as_text=True)
    assert "Also sent to: notary@example.com" in body


def test_thread_page_hides_quoted_history(logged_in, thread):
    message = thread.messages[0]
    message.body_text = "Thanks!\n\n> Would Thursday suit you?"
    db.session.commit()

    body = logged_in.get(f"/mail/threads/{thread.id}").get_data(as_text=True)
    assert "Thanks!" in body
    assert "Would Thursday suit you?" not in body


def test_thread_page_offers_conversion_for_a_lead_only(logged_in, thread, lead_thread):
    lead_body = logged_in.get(f"/mail/threads/{lead_thread.id}").get_data(as_text=True)
    assert "Create client" in lead_body
    matched_body = logged_in.get(f"/mail/threads/{thread.id}").get_data(as_text=True)
    assert "Create client" not in matched_body


def test_message_html_is_never_rendered(logged_in, thread):
    """Sender-controlled HTML on our own origin is stored XSS."""
    message = thread.messages[0]
    message.body_html = "<script>alert('xss')</script>"
    message.body_text = "<script>alert('xss')</script>"
    db.session.commit()

    body = logged_in.get(f"/mail/threads/{thread.id}").get_data(as_text=True)
    assert "<script>alert" not in body
    assert "&lt;script&gt;" in body  # escaped, not executed


def test_a_thread_subject_is_escaped(logged_in, thread):
    thread.subject = "<img src=x onerror=alert(1)>"
    db.session.commit()
    body = logged_in.get(f"/mail/threads/{thread.id}").get_data(as_text=True)
    assert "<img src=x" not in body


# --- tenant isolation -----------------------------------------------------

def test_another_tenants_thread_is_a_404(logged_in, other_company, app):
    theirs_account = EmailAccount(
        company_id=other_company.id, provider="gmail", email_address="theirs@example.com",
    )
    db.session.add(theirs_account)
    db.session.flush()
    theirs = EmailThread(
        company_id=other_company.id, email_account_id=theirs_account.id,
        provider_thread_id="t-theirs", subject="Not yours",
    )
    db.session.add(theirs)
    db.session.commit()

    assert logged_in.get(f"/mail/threads/{theirs.id}").status_code == 404


def test_another_tenants_client_email_tab_is_a_404(logged_in, other_company):
    theirs = Client(
        company_id=other_company.id, first_name="Not", last_name="Yours",
        email="theirs@example.com",
    )
    db.session.add(theirs)
    db.session.commit()

    assert logged_in.get(f"/clients/{theirs.id}/emails").status_code == 404


def test_disconnecting_another_tenants_account_is_a_404(logged_in, csrf, other_company):
    theirs = EmailAccount(
        company_id=other_company.id, provider="gmail", email_address="theirs@example.com",
    )
    db.session.add(theirs)
    db.session.commit()

    response = logged_in.post(
        f"/integrations/accounts/{theirs.id}/disconnect", data={"csrf_token": csrf},
    )
    assert response.status_code == 404
    assert EmailAccount.query.count() == 1


def test_another_tenants_attachment_is_a_404(logged_in, other_company, tmp_path, monkeypatch):
    from communications import config
    from communications.models import EmailMessage
    from communications.storage import attachment_storage

    monkeypatch.setattr(config, "ATTACHMENT_DIR", str(tmp_path))
    theirs_account = EmailAccount(
        company_id=other_company.id, provider="gmail", email_address="theirs@example.com",
    )
    db.session.add(theirs_account)
    db.session.flush()
    theirs_thread = EmailThread(
        company_id=other_company.id, email_account_id=theirs_account.id,
        provider_thread_id="t-theirs",
    )
    db.session.add(theirs_thread)
    db.session.flush()
    theirs_message = EmailMessage(
        thread_id=theirs_thread.id, provider_message_id="m-theirs",
    )
    db.session.add(theirs_message)
    db.session.flush()
    stored = attachment_storage.save(other_company.id, "secret.pdf", b"theirs")
    theirs_attachment = EmailAttachment(
        message_id=theirs_message.id, filename="secret.pdf", stored_filename=stored,
    )
    db.session.add(theirs_attachment)
    db.session.commit()

    response = logged_in.get(f"/mail/attachments/{theirs_attachment.id}")
    assert response.status_code == 404


def test_own_attachment_downloads_as_an_opaque_file(
    logged_in, company, thread, tmp_path, monkeypatch,
):
    """Never inline: an HTML attachment rendered on our origin is stored XSS."""
    from communications import config
    from communications.storage import attachment_storage

    monkeypatch.setattr(config, "ATTACHMENT_DIR", str(tmp_path))
    stored = attachment_storage.save(company.id, "mockup.pdf", b"pdf-bytes")
    row = EmailAttachment(
        message_id=thread.messages[0].id, filename="mockup.pdf", stored_filename=stored,
    )
    db.session.add(row)
    db.session.commit()

    response = logged_in.get(f"/mail/attachments/{row.id}")
    assert response.status_code == 200
    assert response.mimetype == "application/octet-stream"
    assert "attachment" in response.headers["Content-Disposition"]


def test_an_undownloaded_attachment_is_a_404(logged_in, thread):
    row = EmailAttachment(
        message_id=thread.messages[0].id, filename="mockup.pdf", stored_filename=None,
    )
    db.session.add(row)
    db.session.commit()
    assert logged_in.get(f"/mail/attachments/{row.id}").status_code == 404


# --- actions --------------------------------------------------------------

def test_sync_settings_are_clamped(logged_in, csrf, company):
    """Dials, not data — a 1-minute frequency gets you rate-limited and a
    3650-day initial sync hangs the first run."""
    from communications.models import EmailSyncSettings

    logged_in.post("/integrations/sync-settings", data={
        "csrf_token": csrf, "sync_frequency": "1", "initial_sync_days": "99999",
    })
    settings = EmailSyncSettings.query.filter_by(company_id=company.id).one()
    assert settings.sync_frequency == 5
    assert settings.initial_sync_days == 730


def test_non_numeric_sync_settings_fall_back_to_defaults(logged_in, csrf, company):
    from communications.models import EmailSyncSettings

    logged_in.post("/integrations/sync-settings", data={
        "csrf_token": csrf, "sync_frequency": "soon", "initial_sync_days": "",
    })
    settings = EmailSyncSettings.query.filter_by(company_id=company.id).one()
    assert settings.sync_frequency == 15
    assert settings.initial_sync_days == 90


def test_unchecked_boxes_turn_settings_off(logged_in, csrf, company):
    from communications.models import EmailSyncSettings

    logged_in.post("/integrations/sync-settings", data={"csrf_token": csrf})
    settings = EmailSyncSettings.query.filter_by(company_id=company.id).one()
    assert settings.sync_enabled is False
    assert settings.keep_unmatched is False


def test_sync_now_reports_what_it_did(logged_in, csrf, account, client_record):
    with fakes.fake_providers(threads=[fakes.thread()]):
        logged_in.post("/integrations/sync", data={"csrf_token": csrf})
    body = logged_in.get("/settings/integrations").get_data(as_text=True)
    assert "1 new message" in body


def test_sync_now_with_no_account_says_so(logged_in, csrf):
    logged_in.post("/integrations/sync", data={"csrf_token": csrf})
    body = logged_in.get("/settings/integrations").get_data(as_text=True)
    assert "No mailbox is connected" in body


def test_converting_a_lead_creates_a_client_and_redirects_to_them(
    logged_in, csrf, lead_thread, company,
):
    response = logged_in.post(
        f"/mail/threads/{lead_thread.id}/create-client", data={"csrf_token": csrf},
    )
    assert response.status_code == 302
    created = Client.query.filter_by(email="stranger@example.com").one()
    assert f"/clients/{created.id}" in response.headers["Location"]


def test_converting_an_already_linked_thread_shows_an_error(logged_in, csrf, thread):
    logged_in.post(f"/mail/threads/{thread.id}/create-client", data={"csrf_token": csrf})
    body = logged_in.get("/mail/leads").get_data(as_text=True)
    assert "already linked" in body


def test_send_reports_a_validation_error_rather_than_crashing(logged_in, csrf, account):
    response = logged_in.post("/mail/send", data={
        "csrf_token": csrf, "to": "", "subject": "Hi", "body_text": "Hello",
        "return_to": "/mail/leads",
    })
    assert response.status_code == 302
    assert "recipient" in logged_in.get("/mail/leads").get_data(as_text=True)


def test_send_from_the_client_tab(logged_in, csrf, account, client_record):
    with fakes.fake_providers():
        response = logged_in.post("/mail/send", data={
            "csrf_token": csrf, "to": "marie@example.com", "subject": "Ready",
            "body_text": "Your briefcase is ready.",
            "client_id": str(client_record.id),
            "return_to": f"/clients/{client_record.id}/emails",
        })
    assert response.status_code == 302
    assert fakes.SENT_LOG[-1]["to"] == ["marie@example.com"]
    assert EmailThread.query.filter_by(client_id=client_record.id).count() == 1


def test_account_flags_toggle_from_the_page(logged_in, csrf, account):
    logged_in.post(f"/integrations/accounts/{account.id}/flags", data={
        "csrf_token": csrf, "sync_enabled": "0",
    })
    db.session.refresh(account)
    assert account.sync_enabled is False


# --- OAuth callback -------------------------------------------------------

def test_callback_reports_a_cancelled_consent(logged_in):
    response = logged_in.get("/integrations/google/callback?error=access_denied")
    assert response.status_code == 302
    body = logged_in.get("/settings/integrations").get_data(as_text=True)
    assert "cancelled" in body


def test_callback_with_a_bad_state_is_refused(logged_in):
    response = logged_in.get("/integrations/google/callback?code=x&state=forged")
    assert response.status_code == 302
    body = logged_in.get("/settings/integrations").get_data(as_text=True)
    assert "no longer valid" in body


def test_callback_attaches_the_account_to_the_session_company(logged_in, company, monkeypatch):
    """The company comes from the session the flow started in, never the
    query string."""
    from communications.oauth import google_oauth

    monkeypatch.setattr(google_oauth, "finish_flow", lambda s, u, st: (
        {"access_token": "a", "refresh_token": "r", "expiry": None,
         "scopes": ["https://www.googleapis.com/auth/gmail.send"]},
        company.id, "/settings/integrations",
    ))
    monkeypatch.setattr(google_oauth, "fetch_userinfo",
                        lambda token: {"email": "new@example.com", "name": "New"})

    response = logged_in.get("/integrations/google/callback?code=x&state=s")
    assert response.status_code == 302
    created = EmailAccount.query.filter_by(email_address="new@example.com").one()
    assert created.company_id == company.id


def test_callback_refuses_a_flow_started_by_another_company(
    logged_in, other_company, monkeypatch,
):
    """A session that changed identity mid-flow must not graft a mailbox
    onto the wrong tenant."""
    from communications.oauth import google_oauth

    monkeypatch.setattr(google_oauth, "finish_flow", lambda s, u, st: (
        {"access_token": "a", "refresh_token": "r", "expiry": None, "scopes": []},
        other_company.id, "/settings/integrations",
    ))

    logged_in.get("/integrations/google/callback?code=x&state=s")
    assert EmailAccount.query.count() == 0
    body = logged_in.get("/settings/integrations").get_data(as_text=True)
    assert "different account" in body


def test_callback_surfaces_a_failure_instead_of_500ing(logged_in, company, monkeypatch):
    from communications.oauth import google_oauth

    monkeypatch.setattr(google_oauth, "finish_flow", lambda s, u, st: (
        {"access_token": "a", "refresh_token": "r", "expiry": None, "scopes": []},
        company.id, "/settings/integrations",
    ))

    def explode(token):
        raise RuntimeError("userinfo unavailable")

    monkeypatch.setattr(google_oauth, "fetch_userinfo", explode)

    response = logged_in.get("/integrations/google/callback?code=x&state=s")
    assert response.status_code == 302
    assert EmailAccount.query.count() == 0


# --- navigation -----------------------------------------------------------

def test_settings_nav_includes_integrations_on_every_settings_page(logged_in):
    for path in ("/settings/general", "/settings/invoicing", "/settings/orders", "/settings/clients", "/settings/integrations"):
        assert "Integrations" in logged_in.get(path).get_data(as_text=True)


def test_client_nav_includes_the_emails_tab_on_every_client_tab(logged_in, client_record):
    for path in (f"/clients/{client_record.id}",
                 f"/clients/{client_record.id}/orders",
                 f"/clients/{client_record.id}/emails"):
        assert "Emails" in logged_in.get(path).get_data(as_text=True)


def test_clients_list_links_to_leads(logged_in):
    assert "/mail/leads" in logged_in.get("/clients").get_data(as_text=True)


def test_calendar_view_shows_a_synced_event(logged_in, account):
    from datetime import datetime

    from communications.sync import calendar_sync

    with fakes.fake_providers(events=[
        fakes.event(title="Fitting — Marie", start=datetime(2026, 7, 15, 10, 0)),
    ]):
        calendar_sync.sync_calendar(account)

    body = logged_in.get("/month/2026/7").get_data(as_text=True)
    assert "Fitting — Marie" in body
    assert "chip--event" in body


def test_calendar_view_never_shows_orders(logged_in, order):
    """The calendar renders only synced calendar events, never orders —
    those live on the timeline. True whether or not a mailbox is connected,
    and even when an order is due in the month being viewed."""
    body = logged_in.get("/month/2026/7").get_data(as_text=True)
    assert "chip--event" not in body
    assert "chip chip--" not in body
    assert order.item not in body
