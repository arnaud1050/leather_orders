"""
Configuration for the communications module.

Everything secret comes from the environment, never from the database and
never from a checked-in file: OAuth client credentials are per-*deployment*
(one Google Cloud project serves every tenant), while what's per-*tenant*
is the granted token, which lives encrypted in EmailAccount.

`DEPENDENCIES_OK` / `is_configured()` exist so the rest of the app can
degrade politely. A studio that never connects a mailbox — or a dev box
with no Google project — must still get a working app; the integrations
page just explains what's missing instead of raising ImportError at boot.
"""

import os

# --- Vendor scopes ---------------------------------------------------------
#
# Minimum viable set, per the module requirements. Deliberately NOT
# https://mail.google.com/ — that grants unrestricted mailbox access
# (including permanent delete) and drags the whole app into a much heavier
# Google verification tier for no functionality we use.
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",  # read + label/state changes
    "https://www.googleapis.com/auth/gmail.send",    # send + reply
]
CALENDAR_SCOPES = [
    "https://www.googleapis.com/auth/calendar",      # read/create/update events
]
# Identity, so we can record *which* mailbox was connected without asking
# the user to type their own address (and getting it wrong).
IDENTITY_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

GOOGLE_SCOPES = IDENTITY_SCOPES + GMAIL_SCOPES + CALENDAR_SCOPES

# What the scope strings mean, for the consent explanation on the
# integrations page — users should be able to see what they're granting
# before they get bounced to Google.
SCOPE_DESCRIPTIONS = {
    "https://www.googleapis.com/auth/gmail.modify":
        "Read messages and manage labels / read state. Cannot permanently delete mail.",
    "https://www.googleapis.com/auth/gmail.send":
        "Send messages and replies on your behalf.",
    "https://www.googleapis.com/auth/calendar":
        "Read, create and update calendar events.",
    "https://www.googleapis.com/auth/userinfo.email":
        "Read which Google account you connected.",
}


# --- Deployment credentials ------------------------------------------------

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
# Must match a redirect URI registered on the Google Cloud OAuth client
# exactly, scheme and trailing slash included. Google refuses anything else.
GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI", "http://localhost:5000/integrations/google/callback"
)

# Attachment bytes and any other module-owned files. Defaults under the
# same data/ directory the SQLite file lives in, which is the bind-mounted
# volume in both Docker deployments — so attachments survive a rebuild for
# the same reason the database does.
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATTACHMENT_DIR = os.environ.get("ATTACHMENT_DIR", os.path.join(BASE_DIR, "data", "attachments"))


# --- Sync defaults ---------------------------------------------------------
#
# Per-company overrides live in EmailSyncSettings; these are what a company
# gets before it touches anything.
DEFAULT_SYNC_FREQUENCY_MINUTES = 15
DEFAULT_INITIAL_SYNC_DAYS = 90
# Hard ceiling on messages pulled per account per run, so a first sync of a
# busy mailbox can't hold a request (or a job) open indefinitely. A run that
# hits the cap just picks up where it left off next time.
MAX_MESSAGES_PER_SYNC = 200


def dependencies_ok() -> tuple[bool, str | None]:
    """Whether the vendor libraries are importable.

    Checked lazily rather than at import: `pip install -r requirements.txt`
    being out of date should surface as a clear message on the integrations
    page, not as a 500 on every page in the app.
    """
    try:
        import google.oauth2.credentials  # noqa: F401
        import googleapiclient.discovery  # noqa: F401
        import google_auth_oauthlib.flow  # noqa: F401
        import cryptography.fernet  # noqa: F401
    except ImportError as exc:
        return False, str(exc)
    return True, None


def is_configured() -> bool:
    """True when this deployment could actually complete an OAuth flow."""
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET) and dependencies_ok()[0]


def configuration_problem() -> str | None:
    """One sentence on why the module can't be used, or None if it can."""
    ok, error = dependencies_ok()
    if not ok:
        return (
            f"Required libraries aren't installed ({error}). "
            "Run: pip install -r requirements.txt"
        )
    if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET):
        return (
            "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET aren't set in the "
            "environment. Create an OAuth client in the Google Cloud console "
            "and set them (see CLAUDE.md, \"Communications module\")."
        )
    return None
