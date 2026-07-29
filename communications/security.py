"""
CSRF protection for the communications blueprint (§16).

The app has no CSRF layer today, and retrofitting every existing form is
out of scope for this module — but the forms *here* send mail from a
studio's real mailbox and disconnect mailboxes, which is exactly the class
of action a forged cross-site POST is worth mounting. So the blueprint
enforces its own, self-contained.

The scheme is the standard synchroniser-token pattern: a random token per
session, embedded in every form, compared on every unsafe request. An
attacker's page can make a browser POST to us with the victim's cookies,
but it cannot *read* our pages, so it cannot learn the token.

Enforcement is a blueprint-wide `before_request` hook rather than a
per-route decorator, deliberately: a new POST route added later is
protected by default instead of protected if someone remembers.

If Flask-WTF is ever added app-wide, delete this and use CSRFProtect —
this exists because there's nothing to defer to yet, not because it's
better.
"""

import secrets

from flask import abort, request, session

_SESSION_KEY = "comms_csrf_token"
_FORM_FIELD = "csrf_token"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def csrf_token() -> str:
    """This session's token, minted on first use.

    Exposed to templates as a Jinja global (see routes.register), so forms
    write `{{ csrf_token() }}` the same way Flask-WTF would spell it.
    """
    token = session.get(_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[_SESSION_KEY] = token
    return token


def validate_csrf() -> None:
    """Reject unsafe requests without a matching token.

    Wired as a before_request on the blueprint. Safe methods pass through:
    a GET must not have side effects anyway, and requiring a token on them
    would break ordinary links.
    """
    if request.method in _SAFE_METHODS:
        return
    submitted = request.form.get(_FORM_FIELD) or request.headers.get("X-CSRF-Token", "")
    expected = session.get(_SESSION_KEY, "")
    # compare_digest over `==` so a token can't be recovered a byte at a
    # time from response timing.
    if not expected or not submitted or not secrets.compare_digest(submitted, expected):
        abort(400, "Invalid or missing CSRF token. Reload the page and try again.")
