"""Mailbox lifecycle, and the tenant scoping that every route depends on."""

from datetime import timedelta

from models import db

from communications.models import (
    AUDIT_INTEGRATION_CONNECTED, AUDIT_INTEGRATION_DISCONNECTED, AuditLog,
    EmailAccount, EmailSyncSettings, EmailThread, utcnow,
)
from communications.services import account_service

from tests.conftest import ALL_SCOPES, MAIL_ONLY_SCOPES


def tokens(access="new-access", refresh="new-refresh", scopes=None):
    return {
        "access_token": access, "refresh_token": refresh,
        "expiry": utcnow() + timedelta(hours=1),
        "scopes": (scopes or ALL_SCOPES).split(),
    }


# --- tenant scoping -------------------------------------------------------

def test_get_account_refuses_another_tenants_id(account, other_company):
    """Routes pass ids straight from the URL — this filter is what stops one
    company reading another's mailbox."""
    assert account_service.get_account(other_company.id, account.id) is None
    assert account_service.get_account(account.company_id, account.id) is not None


def test_accounts_for_is_scoped(company, other_company, account):
    db.session.add(EmailAccount(
        company_id=other_company.id, provider="gmail", email_address="theirs@example.com",
    ))
    db.session.flush()
    assert [a.id for a in account_service.accounts_for(company.id)] == [account.id]


def test_sync_enabled_accounts_without_a_company_covers_every_tenant(
    company, other_company, account,
):
    """The one query that legitimately isn't tenant-scoped: the scheduled
    job's view. What it returns is fed into per-account work that carries
    its own company_id."""
    db.session.add(EmailAccount(
        company_id=other_company.id, provider="gmail", email_address="theirs@example.com",
    ))
    db.session.flush()
    assert len(account_service.sync_enabled_accounts()) == 2
    assert len(account_service.sync_enabled_accounts(company.id)) == 1


def test_sync_enabled_accounts_excludes_paused(company, account):
    account.sync_enabled = False
    db.session.flush()
    assert account_service.sync_enabled_accounts(company.id) == []


# --- default account ------------------------------------------------------

def test_default_account_prefers_the_flagged_one(company, account):
    second = EmailAccount(company_id=company.id, provider="gmail",
                          email_address="second@example.com")
    db.session.add(second)
    db.session.flush()
    assert account_service.default_account(company.id).id == account.id


def test_default_account_falls_back_when_nothing_is_flagged(company, account):
    """One connected mailbox should Just Work without a settings trip."""
    account.is_default = False
    db.session.flush()
    assert account_service.default_account(company.id).id == account.id


def test_default_account_ignores_send_disabled_accounts(company, account):
    account.send_enabled = False
    db.session.flush()
    assert account_service.default_account(company.id) is None


def test_default_account_with_none_connected(other_company):
    assert account_service.default_account(other_company.id) is None


# --- connecting -----------------------------------------------------------

def test_connect_creates_an_account(company):
    created = account_service.connect_google_account(
        company.id, tokens(), {"email": "Studio@Example.com", "name": "Studio"},
    )
    db.session.flush()

    assert created.email_address == "studio@example.com"  # normalised
    assert created.display_name == "Studio"
    assert created.access_token == "new-access"
    assert created.refresh_token == "new-refresh"
    assert created.granted_scopes == ALL_SCOPES
    assert created.is_default is True  # first mailbox


def test_connect_creates_sync_settings(company):
    account_service.connect_google_account(
        company.id, tokens(), {"email": "studio@example.com"},
    )
    db.session.flush()
    assert EmailSyncSettings.query.filter_by(company_id=company.id).count() == 1


def test_second_account_is_not_made_default(company, account):
    created = account_service.connect_google_account(
        company.id, tokens(), {"email": "second@example.com"},
    )
    db.session.flush()
    assert created.is_default is False


def test_reconnecting_updates_in_place_rather_than_duplicating(company, account):
    """The unique constraint would reject a second row anyway, and
    "reconnect to fix a revoked grant" is the common case."""
    account.last_sync_error = "invalid_grant"
    db.session.flush()

    updated = account_service.connect_google_account(
        company.id, tokens(access="fresh"), {"email": "studio@example.com"},
    )
    db.session.flush()

    assert updated.id == account.id
    assert EmailAccount.query.count() == 1
    assert updated.access_token == "fresh"
    assert updated.last_sync_error is None  # a working reconnect clears it
    assert updated.sync_enabled is True


def test_reconnect_without_a_refresh_token_keeps_the_stored_one(company, account):
    original = account.refresh_token_encrypted
    account_service.connect_google_account(
        company.id, tokens(refresh=None), {"email": "studio@example.com"},
    )
    db.session.flush()
    assert account.refresh_token_encrypted == original


def test_connect_records_an_audit_entry(company):
    account_service.connect_google_account(
        company.id, tokens(), {"email": "studio@example.com"},
    )
    db.session.commit()
    entry = AuditLog.query.filter_by(event=AUDIT_INTEGRATION_CONNECTED).one()
    assert "studio@example.com" in entry.detail


def test_connect_stores_only_the_scopes_actually_granted(company):
    """Users can untick permissions on the consent screen; the app must
    reflect the real grant, not what it hoped for."""
    created = account_service.connect_google_account(
        company.id, tokens(scopes=MAIL_ONLY_SCOPES), {"email": "studio@example.com"},
    )
    db.session.flush()
    assert created.has_scope("https://www.googleapis.com/auth/calendar") is False


def test_connect_without_an_email_from_google_is_rejected(company):
    import pytest

    with pytest.raises(ValueError, match="email address"):
        account_service.connect_google_account(company.id, tokens(), {})


# --- disconnecting --------------------------------------------------------

def test_disconnect_removes_the_account_and_its_mail(company, account, thread, monkeypatch):
    from communications.oauth import google_oauth

    revoked = []
    monkeypatch.setattr(google_oauth, "revoke", lambda acc: revoked.append(acc.id))

    assert account_service.disconnect(company.id, account.id) is True
    db.session.flush()

    assert revoked == [account.id]
    assert EmailAccount.query.count() == 0
    assert EmailThread.query.count() == 0  # "disconnect" that keeps mail isn't one


def test_disconnect_records_an_audit_entry(company, account, monkeypatch):
    from communications.oauth import google_oauth

    monkeypatch.setattr(google_oauth, "revoke", lambda acc: None)
    account_service.disconnect(company.id, account.id)
    db.session.commit()
    entry = AuditLog.query.filter_by(event=AUDIT_INTEGRATION_DISCONNECTED).one()
    assert "studio@example.com" in entry.detail


def test_disconnect_refuses_another_tenants_account(other_company, account, monkeypatch):
    from communications.oauth import google_oauth

    monkeypatch.setattr(google_oauth, "revoke", lambda acc: None)
    assert account_service.disconnect(other_company.id, account.id) is False
    assert EmailAccount.query.count() == 1


def test_disconnect_proceeds_when_revocation_fails(company, account, monkeypatch):
    """A revoke that fails (network down, token already dead) must not leave
    a disconnected account still listed in the app — so revoke() swallows
    its own errors and disconnect() carries on."""
    import requests

    def explode(*args, **kwargs):
        raise requests.RequestException("network down")

    monkeypatch.setattr(requests, "post", explode)

    assert account_service.disconnect(company.id, account.id) is True
    db.session.flush()
    assert EmailAccount.query.count() == 0


def test_revoke_is_a_noop_without_a_token(company, monkeypatch):
    import requests

    called = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: called.append(1))

    from communications.oauth import google_oauth

    tokenless = EmailAccount(company_id=company.id, provider="gmail",
                             email_address="empty@example.com")
    db.session.add(tokenless)
    db.session.flush()

    google_oauth.revoke(tokenless)
    assert called == []


def test_revoke_sends_the_refresh_token(company, account, monkeypatch):
    """Revoking the refresh token kills the whole grant; revoking only the
    access token would leave it renewable."""
    import requests

    calls = []
    monkeypatch.setattr(requests, "post", lambda url, **kw: calls.append((url, kw)))

    from communications.oauth import google_oauth

    google_oauth.revoke(account)
    assert calls and "revoke" in calls[0][0]
    assert calls[0][1]["params"]["token"] == "refresh-token"


# --- flags ----------------------------------------------------------------

def test_set_flags_toggles_sync(company, account):
    account_service.set_flags(company.id, account.id, sync_enabled=False)
    assert account.sync_enabled is False


def test_making_one_account_default_clears_the_others(company, account):
    second = EmailAccount(company_id=company.id, provider="gmail",
                          email_address="second@example.com")
    db.session.add(second)
    db.session.flush()

    account_service.set_flags(company.id, second.id, is_default=True)
    db.session.flush()

    assert second.is_default is True
    assert account.is_default is False


def test_set_flags_refuses_another_tenants_account(other_company, account):
    assert account_service.set_flags(other_company.id, account.id, sync_enabled=False) is None
    assert account.sync_enabled is True


def test_set_flags_only_touches_what_was_passed(company, account):
    """A form that doesn't render a field means "leave it alone", not
    "clear it" — same rule as the client modal's address fields."""
    account_service.set_flags(company.id, account.id, sync_enabled=False)
    assert account.send_enabled is True
    assert account.is_default is True


# --- scope summary --------------------------------------------------------

def test_scope_summary_describes_what_was_granted(account):
    summary = dict(account_service.scope_summary(account))
    assert "https://www.googleapis.com/auth/calendar" in summary
    assert "delete" in summary["https://www.googleapis.com/auth/gmail.modify"]


def test_scope_summary_omits_ungranted_scopes(account):
    account.granted_scopes = MAIL_ONLY_SCOPES
    assert all("calendar" not in scope for scope, _ in account_service.scope_summary(account))


def test_scope_summary_ignores_scopes_it_has_no_wording_for(account):
    account.granted_scopes = "https://example.com/unknown-scope"
    assert account_service.scope_summary(account) == []
