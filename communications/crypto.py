"""
Encryption for OAuth tokens at rest.

A refresh token is a long-lived, non-expiring key to someone's mailbox —
strictly worse to leak than a password hash, because it's directly usable.
So it never hits the database in plaintext, and the key that decrypts it
never hits the database at all: it comes from the environment, which means
a stolen copy of `atelier.db` is not by itself enough to read anyone's mail.

Fernet (AES-128-CBC + HMAC-SHA256, from `cryptography`) is used rather than
anything hand-rolled. It's authenticated, so a tampered ciphertext fails
loudly instead of decrypting to garbage.

Key resolution, in order:

1. `COMMS_ENCRYPTION_KEY` — a real Fernet key. This is what production
   should set. Generate one with:
       python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
2. Derived from `SECRET_KEY` via PBKDF2. Convenience for local dev so the
   module works out of the box, and it's genuinely a separate key rather
   than reusing the session key directly. The catch, and it's why (1)
   exists: **rotating SECRET_KEY makes every stored token undecryptable**,
   and connected accounts have to be reconnected.
"""

import base64
import hashlib
import os

# Fixed, non-secret salt. PBKDF2's salt is there to stop one precomputed
# table covering every deployment; it doesn't need to be secret, and it
# can't be random here because the derivation has to be reproducible
# across restarts from SECRET_KEY alone.
_KDF_SALT = b"atelier-communications-token-encryption-v1"
_KDF_ITERATIONS = 480_000


class TokenDecryptionError(Exception):
    """Stored token can't be decrypted — wrong key, or corrupted data.

    Callers should treat this as "this account needs reconnecting", not as
    a crash: it's the expected outcome of rotating the encryption key.
    """


def _derive_key(secret: str) -> bytes:
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), _KDF_SALT, _KDF_ITERATIONS)
    return base64.urlsafe_b64encode(digest)


def _fernet():
    from cryptography.fernet import Fernet

    key = os.environ.get("COMMS_ENCRYPTION_KEY", "").strip()
    if key:
        return Fernet(key.encode())
    # Dev fallback. Mirrors app.config["SECRET_KEY"]'s own fallback so a
    # developer who set neither still gets a working (and consistently
    # keyed) app.
    return Fernet(_derive_key(os.environ.get("SECRET_KEY", "dev-not-secure")))


def encrypt(value: str | None) -> str | None:
    """Ciphertext for storage. None passes through — a missing token is a
    legitimate state (Google only returns a refresh token on first grant)."""
    if value is None or value == "":
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise TokenDecryptionError(
            "Stored OAuth token could not be decrypted. This normally means "
            "COMMS_ENCRYPTION_KEY (or SECRET_KEY, if you're relying on the "
            "derived key) changed since it was saved — the account needs to "
            "be disconnected and reconnected."
        ) from exc


def using_derived_key() -> bool:
    """True when running on the SECRET_KEY-derived dev fallback, so the UI
    can say so rather than letting it pass for a real key."""
    return not os.environ.get("COMMS_ENCRYPTION_KEY", "").strip()
