"""
Two-factor sign-in with an authenticator app (TOTP, RFC 6238). REQUIREMENTS
CO4s–CO4w.

A stolen password alone used to open an account, and a platform admin's
opens every studio (impersonation included). With this, signing in also
needs the 6-digit code an authenticator app (Google Authenticator, Aegis,
1Password, …) computes from a secret shared once, by QR code. Nothing is
sent by the server: no SMS, no email, no outside service.

**Who needs it.** Required for platform staff: app.py holds a staff account
without it on the setup page until it's done (CO4u). Optional for studio
users, from Settings → Account.

**What's stored**, on `users` (see `User`):

* `totp_secret`, encrypted under its own SecretBox (`TOTP_ENCRYPTION_KEY`),
  because anyone holding it can mint valid codes. Rotating that key (or
  SECRET_KEY, while running on the derived fallback) makes the secrets
  unreadable: affected users sign in with a recovery code and set up again.
* `totp_enabled_at`: set only once the user has proved the scan worked with
  a first code. A setup in progress lives in their session, so a scan that
  went wrong can't lock anybody out.
* `totp_last_step`: the time step of the last code accepted. Codes for that
  step or earlier are refused, so an overheard code can't be replayed.

**Recovery codes** (`RecoveryCode`): ten single-use codes shown once at
setup, for a lost phone. Stored as SHA-256 digests. They carry 50 bits of
randomness each, so a fast hash is enough: unlike a password, nobody chose
them, and the sign-in lockout (CO4k) caps guessing anyway.

**Codes are checked one step either side of now** (90 seconds in all), for
a phone clock that's a little off. That's the usual allowance.
"""

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timezone

from markupsafe import Markup

from crypto import SecretBox, SecretDecryptionError
from models import User, db

ISSUER = "Atelier"
STEP_SECONDS = 30
DIGITS = 6
VALID_WINDOW = 1          # steps either side of now
RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i

_box = SecretBox(
    env_var="TOTP_ENCRYPTION_KEY",
    salt=b"atelier-totp-v1",
    decryption_hint=(
        "Two-factor secrets can't be read with the current key "
        "(TOTP_ENCRYPTION_KEY, or SECRET_KEY if that's unset). "
        "Sign in with a recovery code and set two-factor up again."
    ),
)


def _utcnow() -> datetime:
    """Naive UTC, like every other timestamp here."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _now() -> float:
    """Unix time. A function so tests can move the clock."""
    return time.time()


class RecoveryCode(db.Model):
    """One single-use recovery code, as a digest. A new table, so
    db.create_all() covers it (hard rule 12)."""

    __tablename__ = "user_recovery_codes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    code_hash = db.Column(db.String(64), nullable=False)
    used_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False)


# --- the secret ------------------------------------------------------------------

def new_secret() -> str:
    import pyotp
    return pyotp.random_base32()


def encrypt_secret(secret: str) -> str:
    return _box.encrypt(secret)


def decrypt_secret(ciphertext: str) -> str | None:
    """None when the key can't read it (see the module docstring)."""
    try:
        return _box.decrypt(ciphertext)
    except SecretDecryptionError:
        return None


def provisioning_uri(user: User, secret: str) -> str:
    """The otpauth:// URI the QR code carries: names the account so the
    app's list reads "Atelier: someone@studio.ca"."""
    import pyotp
    return pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=ISSUER)


def qr_svg(uri: str) -> Markup:
    """An inline SVG QR code for the setup page. Our own output from our own
    URI, so safe to mark up; inline SVG needs nothing from the CSP."""
    import qrcode
    import qrcode.image.svg

    image = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    svg = image.to_string(encoding="unicode")
    svg = svg.replace('width="', 'data-width="').replace('height="', 'data-height="', 1)
    return Markup(svg.replace(
        "<svg ", '<svg class="two-factor-qr" role="img" aria-label="QR code to scan" ', 1))


def format_secret(secret: str) -> str:
    """The secret in groups of four, for typing in by hand."""
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


# --- codes -------------------------------------------------------------------------

def _clean_code(code: str) -> str:
    return "".join(ch for ch in (code or "") if ch.isdigit())


def _matching_step(secret: str, code: str) -> int | None:
    """The time step `code` is valid for, within the window, or None."""
    import pyotp

    code = _clean_code(code)
    if len(code) != DIGITS:
        return None
    totp = pyotp.TOTP(secret)
    now_step = int(_now() // STEP_SECONDS)
    for step in range(now_step - VALID_WINDOW, now_step + VALID_WINDOW + 1):
        if hmac.compare_digest(totp.at(step * STEP_SECONDS), code):
            return step
    return None


def setup_step(secret: str, code: str) -> int | None:
    """For setup: the time step of `code` if it proves the user scanned
    `secret`, else None. Handed to `enable`, so that same code can't then
    be replayed to sign in."""
    return _matching_step(secret, code)


def verify_code(user: User, code: str) -> bool:
    """Check a sign-in code against the user's stored secret, refusing a
    replay. Records the step on success (the caller commits)."""
    if not user.two_factor_enabled:
        return False
    secret = decrypt_secret(user.totp_secret)
    if secret is None:
        return False
    step = _matching_step(secret, code)
    if step is None or (user.totp_last_step is not None and step <= user.totp_last_step):
        return False
    user.totp_last_step = step
    return True


# --- recovery codes ---------------------------------------------------------------

def _normalise_recovery(code: str) -> str:
    return "".join(ch for ch in (code or "").lower() if ch in _RECOVERY_ALPHABET)


def _digest(code: str) -> str:
    return hashlib.sha256(_normalise_recovery(code).encode()).hexdigest()


def _new_recovery_code() -> str:
    chars = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
    return f"{chars[:5]}-{chars[5:]}"


def replace_recovery_codes(user: User) -> list[str]:
    """Issue a fresh set, invalidating any older one. Returns the codes in
    plain text: the only time they exist outside the user's hands."""
    RecoveryCode.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    codes = [_new_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    now = _utcnow()
    for code in codes:
        db.session.add(RecoveryCode(user_id=user.id, code_hash=_digest(code), created_at=now))
    return codes


def use_recovery_code(user: User, code: str) -> bool:
    """Spend one recovery code. False if it's unknown or already used."""
    if len(_normalise_recovery(code)) != 10:
        return False
    digest = _digest(code)
    for row in RecoveryCode.query.filter_by(user_id=user.id, used_at=None):
        if hmac.compare_digest(row.code_hash, digest):
            row.used_at = _utcnow()
            return True
    return False


def recovery_codes_left(user: User) -> int:
    return RecoveryCode.query.filter_by(user_id=user.id, used_at=None).count()


# --- turning it on and off --------------------------------------------------------

def enable(user: User, secret: str, proved_step: int) -> list[str]:
    """Switch two-factor on with a secret the user has proved with a code
    for `proved_step` (see `setup_step`). Returns the recovery codes to show
    once. The caller commits."""
    user.totp_secret = encrypt_secret(secret)
    user.totp_enabled_at = _utcnow()
    user.totp_last_step = proved_step
    return replace_recovery_codes(user)


def disable(user: User) -> None:
    """Switch it off and forget the secret and codes. The caller commits."""
    user.totp_secret = None
    user.totp_enabled_at = None
    user.totp_last_step = None
    RecoveryCode.query.filter_by(user_id=user.id).delete(synchronize_session=False)
