"""
Symmetric encryption for secrets held at rest.

A secret this app stores on someone's behalf — a Gmail refresh token, an
OpenAI API key — is strictly worse to leak than a password hash, because
it's directly usable by whoever takes it. So none of them hit the database
in plaintext, and the key that decrypts them never hits the database at
all: it comes from the environment, which means a stolen copy of
`atelier.db` is not by itself enough to use any of them.

Fernet (AES-128-CBC + HMAC-SHA256, from `cryptography`) is used rather than
anything hand-rolled. It's authenticated, so a tampered ciphertext fails
loudly instead of decrypting to garbage.

**This is the one piece of shared infrastructure a module may import from
the host** (see hard rule 4 in the root CLAUDE.md). It deliberately knows
nothing about models, tenants or Flask — it's a pure function of `(env var,
salt, plaintext)`, so importing it doesn't drag the app in behind it. A
module still keeps its own one-file wrapper naming *its* env var and salt
(`communications/crypto.py`, `ai/crypto.py`), which is both what makes the
module liftable and what keeps two purposes from sharing a key.

## Why each purpose gets its own SecretBox

Two reasons, and the second is the load-bearing one:

1. Separate env vars mean a deployment can rotate one purpose's key without
   invalidating the other's.
2. **Separate salts mean the derived-key fallback produces a different key
   per purpose from the same SECRET_KEY.** Without that, ciphertext from
   one purpose would decrypt under another's box — not a break in itself,
   but it turns "this value is a mail token" into something the type system
   stops being able to say.

Key resolution, per box, in order:

1. The box's env var (e.g. `COMMS_ENCRYPTION_KEY`) — a real Fernet key.
   This is what production should set. Generate one with:
       python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
2. Derived from `SECRET_KEY` via PBKDF2. Convenience for local dev so the
   thing works out of the box, and it's genuinely a separate key rather
   than reusing the session key directly. The catch, and it's why (1)
   exists: **rotating SECRET_KEY makes every stored secret undecryptable**,
   and whatever was encrypted has to be re-entered.

The environment is read on every call rather than cached at import, so a
test (or a key rotation in a long-running process) that changes the env var
takes effect immediately.
"""

import base64
import hashlib
import os

_KDF_ITERATIONS = 480_000


class SecretDecryptionError(Exception):
    """A stored secret can't be decrypted — wrong key, or corrupted data.

    Callers should treat this as "this thing needs re-entering", not as a
    crash: it's the expected outcome of rotating the encryption key.
    """


def _derive_key(secret: str, salt: bytes) -> bytes:
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, _KDF_ITERATIONS)
    return base64.urlsafe_b64encode(digest)


class SecretBox:
    """Encrypt/decrypt for one purpose, keyed by one env var.

    `salt` is fixed and non-secret. PBKDF2's salt is there to stop one
    precomputed table covering every deployment; it doesn't need to be
    secret, and it can't be random here because the derivation has to be
    reproducible across restarts from SECRET_KEY alone.

    **Never change an existing box's `salt` or `env_var`** — either one
    silently makes every value already encrypted under it unreadable. The
    `-v1` suffix on the salts is there so a deliberate rotation can be
    spelled as `-v2` rather than mistaken for a rename.
    """

    def __init__(self, *, env_var: str, salt: bytes, decryption_hint: str):
        self.env_var = env_var
        self.salt = salt
        self.decryption_hint = decryption_hint

    def _fernet(self):
        from cryptography.fernet import Fernet

        key = os.environ.get(self.env_var, "").strip()
        if key:
            return Fernet(key.encode())
        # Dev fallback. Mirrors app.config["SECRET_KEY"]'s own fallback so a
        # developer who set neither still gets a working (and consistently
        # keyed) app.
        return Fernet(_derive_key(os.environ.get("SECRET_KEY", "dev-not-secure"), self.salt))

    def encrypt(self, value: str | None) -> str | None:
        """Ciphertext for storage. None and "" pass through as None — a
        missing secret is a legitimate state (Google only returns a refresh
        token on first grant; a company may have configured no API key)."""
        if value is None or value == "":
            return None
        return self._fernet().encrypt(value.encode()).decode()

    def decrypt(self, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        from cryptography.fernet import InvalidToken

        try:
            return self._fernet().decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise SecretDecryptionError(self.decryption_hint) from exc

    def using_derived_key(self) -> bool:
        """True when running on the SECRET_KEY-derived dev fallback, so the
        UI can say so rather than letting it pass for a real key."""
        return not os.environ.get(self.env_var, "").strip()
