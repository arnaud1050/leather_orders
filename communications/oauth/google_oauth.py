"""
Google OAuth 2.0 — the authorization-code flow, for Gmail + Calendar.

Three things happen here and nowhere else:

1. Building the consent URL, with a signed `state` value.
2. Exchanging the returned code for tokens, *after* validating that state.
3. Turning a stored EmailAccount back into live credentials, refreshing
   the access token when it's expired and saving the new one.

**On `state`** (§16, "OAuth state validation"): the callback is a public
GET endpoint that Google redirects a browser to. Without state, anyone
could feed a victim's browser a crafted callback URL and attach *their*
mailbox to the victim's company. So state is a random token stored in the
user's session and compared on return — an attacker can't write to the
victim's session, so they can't produce a matching value. The session
also carries the company_id the flow started for, which is what the new
account is attached to: never a company_id from the query string.
"""

import os
import secrets
from datetime import datetime, timezone

from communications import config
from communications.providers.base import ProviderError, ReauthorizationRequired

# Session keys for the in-flight flow. Namespaced so they can't collide
# with anything Flask-Login or a future flow puts in the session.
STATE_SESSION_KEY = "comms_oauth_state"
COMPANY_SESSION_KEY = "comms_oauth_company_id"
RETURN_SESSION_KEY = "comms_oauth_return_to"
# PKCE: google-auth-oauthlib generates a code_verifier inside
# authorization_url() and sends its challenge to Google, but the callback
# builds a *different* Flow object, which would have no verifier to send —
# Google then rejects the exchange with "Missing code verifier". So it
# travels with the rest of the in-flight flow. Same lifetime as `state`
# (popped before the exchange), so a replayed callback can't reuse it.
VERIFIER_SESSION_KEY = "comms_oauth_code_verifier"

# Google reorders the granted scopes and folds `openid` into the response,
# so oauthlib's strict equality check on the returned scope list fires on
# a perfectly valid grant. Relaxing it compares the *set* instead. What we
# were actually granted is read off the credentials afterwards and stored
# on the account (EmailAccount.granted_scopes), so nothing is taken on
# trust here — the check being relaxed is a formatting check, not a
# security one.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


def _allow_insecure_transport() -> None:
    """Permit http:// callbacks, but only on loopback.

    oauthlib refuses non-TLS redirect URIs, which is right — an authorization
    code in cleartext is a mailbox in cleartext. Local development has no
    TLS, so the exception is scoped to localhost specifically rather than
    set globally, so it can't silently cover a real deployment that was
    misconfigured to plain http.
    """
    uri = config.GOOGLE_REDIRECT_URI
    if uri.startswith("http://localhost") or uri.startswith("http://127.0.0.1"):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


def _client_config() -> dict:
    """The client_secrets.json shape, built from the environment.

    Assembled here rather than read from a file so the secret lives in one
    place (the env) and can't be committed by accident.
    """
    return {
        "web": {
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [config.GOOGLE_REDIRECT_URI],
        }
    }


def _flow(state: str | None = None):
    from google_auth_oauthlib.flow import Flow

    _allow_insecure_transport()
    flow = Flow.from_client_config(
        _client_config(), scopes=config.GOOGLE_SCOPES, state=state,
    )
    flow.redirect_uri = config.GOOGLE_REDIRECT_URI
    return flow


def start_flow(session, company_id: int, return_to: str) -> str:
    """Consent URL to redirect the user to; records the flow in `session`."""
    if not config.is_configured():
        raise ProviderError(config.configuration_problem())

    state = secrets.token_urlsafe(32)
    session[STATE_SESSION_KEY] = state
    session[COMPANY_SESSION_KEY] = company_id
    session[RETURN_SESSION_KEY] = return_to

    flow = _flow(state=state)
    authorization_url, _ = flow.authorization_url(
        # offline is what gets us a refresh token at all; without it the
        # grant dies in an hour and background sync is impossible.
        access_type="offline",
        # Google only issues a refresh token on first consent. A user who
        # reconnects after a disconnect would otherwise come back with no
        # refresh token and an account that can't sync — forcing the prompt
        # guarantees one every time.
        prompt="consent",
        include_granted_scopes="true",
    )
    # Only set on versions that do PKCE; None on older ones, and the
    # callback then simply has nothing to restore.
    session[VERIFIER_SESSION_KEY] = getattr(flow, "code_verifier", None)
    return authorization_url


def finish_flow(session, request_url: str, state: str | None) -> tuple[dict, int, str]:
    """Validate the callback and exchange the code.

    Returns (token payload, company_id, return_to). Raises ProviderError on
    any state mismatch — deliberately with a vague message, since the only
    way to reach it is a stale tab or an attack, and neither is helped by
    detail.

    The session keys are cleared before the exchange, so a replayed
    callback URL can't be used twice.
    """
    expected_state = session.pop(STATE_SESSION_KEY, None)
    company_id = session.pop(COMPANY_SESSION_KEY, None)
    return_to = session.pop(RETURN_SESSION_KEY, None) or "/settings/integrations"
    code_verifier = session.pop(VERIFIER_SESSION_KEY, None)

    if not expected_state or not state or not secrets.compare_digest(state, expected_state):
        raise ProviderError(
            "This sign-in link is no longer valid. Start the connection again "
            "from the integrations page."
        )
    if not company_id:
        raise ProviderError("Sign-in session expired. Please try again.")

    flow = _flow(state=expected_state)
    if code_verifier:
        flow.code_verifier = code_verifier
    try:
        flow.fetch_token(authorization_response=request_url)
    except Exception as exc:  # oauthlib raises a wide family of these
        raise ProviderError(f"Google rejected the sign-in: {exc}") from exc

    credentials = flow.credentials
    return (
        {
            "access_token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "expiry": _naive_utc(credentials.expiry),
            "scopes": list(credentials.scopes or []),
        },
        company_id,
        return_to,
    )


def fetch_userinfo(access_token: str) -> dict:
    """Which Google account was just connected.

    Asked rather than typed: a mailbox address entered by hand is a
    mailbox address that can be wrong, and the whole client-matching path
    depends on knowing which addresses are "us".
    """
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    service = build(
        "oauth2", "v2", credentials=Credentials(token=access_token),
        cache_discovery=False,
    )
    return service.userinfo().get().execute()


def credentials_for(account):
    """Live Google credentials for an account, refreshed if needed.

    Every provider call goes through here, which is what makes token
    refresh automatic (§9) rather than something each call site remembers.
    A refreshed token is written back to the account immediately — the
    caller still has to commit, but the session carries it either way.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google.auth.exceptions import RefreshError

    from models import db

    refresh_token = account.refresh_token
    if not refresh_token:
        raise ReauthorizationRequired(
            f"{account.email_address} has no refresh token stored. "
            "Disconnect and reconnect the account."
        )

    credentials = Credentials(
        token=account.access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
        scopes=account.scopes or config.GOOGLE_SCOPES,
        expiry=account.token_expiry,
    )

    if not credentials.valid:
        try:
            credentials.refresh(Request())
        except RefreshError as exc:
            # The user revoked access in their Google account, or the grant
            # was expired by Google. No amount of retrying helps.
            raise ReauthorizationRequired(
                f"Google refused to refresh access for {account.email_address} "
                f"({exc}). The account needs to be reconnected."
            ) from exc
        account.access_token = credentials.token
        account.token_expiry = _naive_utc(credentials.expiry)
        db.session.add(account)

    return credentials


def revoke(account) -> None:
    """Ask Google to invalidate the grant.

    Best effort by design: the local record is deleted by the caller
    regardless. A revoke that fails (network down, token already dead)
    must not leave a disconnected account still listed in the app.
    """
    import requests

    token = account.refresh_token or account.access_token
    if not token:
        return
    try:
        requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": token},
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
    except Exception:  # noqa: BLE001 — see docstring; failure is acceptable
        pass


def _naive_utc(value: datetime | None) -> datetime | None:
    """Normalise to the naive-UTC convention the models use.

    google-auth already returns naive UTC, but it has not always, and a
    tz-aware datetime in a SQLite DateTime column compares wrong against
    the naive ones around it rather than failing — so it's pinned here.
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value
