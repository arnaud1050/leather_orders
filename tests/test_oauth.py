"""
OAuth state validation and token refresh.

The state checks are the security-critical half: the callback is a public
GET that Google redirects a browser to, so without a session-bound state
token an attacker could feed a victim's browser a crafted callback URL and
graft their own mailbox onto the victim's company.
"""

from base64 import urlsafe_b64encode
from hashlib import sha256

import pytest

from communications import config
from communications.oauth import google_oauth
from communications.providers.base import ProviderError, ReauthorizationRequired


def test_start_flow_records_state_and_company_in_the_session(app, company):
    session = {}
    url = google_oauth.start_flow(session, company.id, "/settings/integrations")

    assert session[google_oauth.STATE_SESSION_KEY]
    assert session[google_oauth.COMPANY_SESSION_KEY] == company.id
    assert session[google_oauth.RETURN_SESSION_KEY] == "/settings/integrations"
    assert url.startswith("https://accounts.google.com/o/oauth2/auth")


def test_consent_url_requests_offline_access_and_forces_the_prompt(app, company):
    """access_type=offline is what yields a refresh token at all; without
    prompt=consent Google skips issuing one on a reconnect, leaving an
    account that can't refresh."""
    url = google_oauth.start_flow({}, company.id, "/")
    assert "access_type=offline" in url
    assert "prompt=consent" in url


def test_consent_url_requests_the_minimum_scopes(app, company):
    url = google_oauth.start_flow({}, company.id, "/")
    assert "gmail.modify" in url
    assert "gmail.send" in url
    assert "auth%2Fcalendar" in url or "auth/calendar" in url
    # Never the unrestricted scope — it allows permanent deletion and drags
    # the app into a much heavier Google verification tier.
    assert "mail.google.com" not in url


def test_consent_url_carries_its_pkce_verifier_in_the_session(app, company):
    """The callback builds a *different* Flow object, so a code_verifier
    generated during authorization_url() is lost unless it travels with the
    rest of the in-flight flow — Google then rejects the exchange with
    "Missing code verifier". This is what that regression looks like."""
    session = {}
    url = google_oauth.start_flow(session, company.id, "/")

    if "code_challenge" not in url:  # older lib, no PKCE to carry
        return
    verifier = session[google_oauth.VERIFIER_SESSION_KEY]
    assert verifier

    expected = urlsafe_b64encode(sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert f"code_challenge={expected}" in url
    assert "code_challenge_method=S256" in url


def test_start_flow_refuses_when_unconfigured(app, company, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "")
    with pytest.raises(ProviderError):
        google_oauth.start_flow({}, company.id, "/")


# --- state validation -----------------------------------------------------

def test_finish_flow_rejects_a_missing_state(app):
    session = {}
    with pytest.raises(ProviderError, match="no longer valid"):
        google_oauth.finish_flow(session, "http://localhost/cb?code=x", "anything")


def test_finish_flow_rejects_a_mismatched_state(app, company):
    """The core CSRF defence on the callback: an attacker can't write to the
    victim's session, so they can't produce a matching value."""
    session = {
        google_oauth.STATE_SESSION_KEY: "the-real-state",
        google_oauth.COMPANY_SESSION_KEY: company.id,
    }
    with pytest.raises(ProviderError, match="no longer valid"):
        google_oauth.finish_flow(session, "http://localhost/cb?code=x", "attacker-state")


def test_finish_flow_rejects_a_state_the_caller_did_not_send(app, company):
    session = {
        google_oauth.STATE_SESSION_KEY: "the-real-state",
        google_oauth.COMPANY_SESSION_KEY: company.id,
    }
    with pytest.raises(ProviderError):
        google_oauth.finish_flow(session, "http://localhost/cb?code=x", None)


def test_finish_flow_requires_a_company_in_the_session(app):
    """The company an account attaches to comes from the session, never the
    query string."""
    session = {google_oauth.STATE_SESSION_KEY: "s"}
    with pytest.raises(ProviderError, match="expired"):
        google_oauth.finish_flow(session, "http://localhost/cb?code=x", "s")


def test_finish_flow_clears_the_session_so_a_callback_cannot_be_replayed(app, company):
    session = {
        google_oauth.STATE_SESSION_KEY: "s",
        google_oauth.COMPANY_SESSION_KEY: company.id,
        google_oauth.RETURN_SESSION_KEY: "/x",
    }
    with pytest.raises(ProviderError):
        # Fails at the token exchange (no real Google), but the session keys
        # must already be gone by then.
        google_oauth.finish_flow(session, "http://localhost/cb?code=x", "s")
    assert google_oauth.STATE_SESSION_KEY not in session
    assert google_oauth.COMPANY_SESSION_KEY not in session


def test_state_tokens_are_unpredictable(app, company):
    seen = set()
    for _ in range(20):
        session = {}
        google_oauth.start_flow(session, company.id, "/")
        seen.add(session[google_oauth.STATE_SESSION_KEY])
    assert len(seen) == 20
    assert all(len(state) >= 32 for state in seen)


# --- insecure transport ---------------------------------------------------

def test_http_is_allowed_only_on_loopback(monkeypatch):
    """An authorization code in cleartext is a mailbox in cleartext, so the
    exception is scoped to localhost rather than set globally."""
    monkeypatch.delenv("OAUTHLIB_INSECURE_TRANSPORT", raising=False)
    monkeypatch.setattr(config, "GOOGLE_REDIRECT_URI", "http://example.com/cb")
    google_oauth._allow_insecure_transport()
    import os

    assert "OAUTHLIB_INSECURE_TRANSPORT" not in os.environ

    monkeypatch.setattr(config, "GOOGLE_REDIRECT_URI", "http://localhost:5000/cb")
    google_oauth._allow_insecure_transport()
    assert os.environ.get("OAUTHLIB_INSECURE_TRANSPORT") == "1"
    monkeypatch.delenv("OAUTHLIB_INSECURE_TRANSPORT", raising=False)


# --- credentials / refresh ------------------------------------------------

def test_credentials_require_a_refresh_token(app, account):
    account.refresh_token_encrypted = None
    with pytest.raises(ReauthorizationRequired, match="no refresh token"):
        google_oauth.credentials_for(account)


def test_valid_credentials_are_not_refreshed(app, account, monkeypatch):
    from datetime import timedelta

    from communications.models import utcnow

    account.token_expiry = utcnow() + timedelta(hours=1)

    refreshed = []

    class FakeCredentials:
        valid = True
        token = "access-token"
        expiry = account.token_expiry

        def __init__(self, **kwargs):
            pass

        def refresh(self, request):
            refreshed.append(1)

    monkeypatch.setattr("google.oauth2.credentials.Credentials", FakeCredentials)
    google_oauth.credentials_for(account)
    assert refreshed == []


def test_expired_credentials_are_refreshed_and_persisted(app, account, monkeypatch):
    """A refresh that isn't saved means every request pays for another one."""
    from datetime import datetime

    class FakeCredentials:
        def __init__(self, **kwargs):
            self.valid = False
            self.token = "old"
            self.expiry = None

        def refresh(self, request):
            self.valid = True
            self.token = "refreshed-token"
            self.expiry = datetime(2030, 1, 1, 12, 0)

    monkeypatch.setattr("google.oauth2.credentials.Credentials", FakeCredentials)
    monkeypatch.setattr("google.auth.transport.requests.Request", lambda: object())

    google_oauth.credentials_for(account)

    assert account.access_token == "refreshed-token"
    assert account.token_expiry == datetime(2030, 1, 1, 12, 0)


def test_a_revoked_grant_raises_reauthorization_required(app, account, monkeypatch):
    """Distinct from a transient error: no amount of retrying fixes it, so
    the caller must tell the user to reconnect rather than retry."""
    from google.auth.exceptions import RefreshError

    class FakeCredentials:
        def __init__(self, **kwargs):
            self.valid = False
            self.token = None
            self.expiry = None

        def refresh(self, request):
            raise RefreshError("invalid_grant")

    monkeypatch.setattr("google.oauth2.credentials.Credentials", FakeCredentials)
    monkeypatch.setattr("google.auth.transport.requests.Request", lambda: object())

    with pytest.raises(ReauthorizationRequired, match="reconnected"):
        google_oauth.credentials_for(account)


def test_naive_utc_normalisation():
    """A tz-aware datetime in a SQLite DateTime column compares wrong
    against the naive ones around it rather than failing."""
    from datetime import datetime, timezone

    aware = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)
    assert google_oauth._naive_utc(aware) == datetime(2026, 7, 28, 14, 0)
    assert google_oauth._naive_utc(aware).tzinfo is None
    assert google_oauth._naive_utc(None) is None
    naive = datetime(2026, 7, 28, 14, 0)
    assert google_oauth._naive_utc(naive) == naive


# --- configuration reporting ---------------------------------------------

def test_configuration_problem_is_none_when_set_up(app):
    assert config.configuration_problem() is None


def test_configuration_problem_names_the_missing_credentials(app, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "")
    problem = config.configuration_problem()
    assert "GOOGLE_CLIENT_ID" in problem
    assert config.is_configured() is False
