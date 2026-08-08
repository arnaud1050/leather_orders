"""
Encryption for OAuth tokens at rest.

The mechanics live in the host's `crypto.py` — this file is the module's
declaration of *which key* and *which salt* its tokens are encrypted under,
plus the names the rest of the module imports. Lifting this module into
another project means bringing `crypto.SecretBox` (a dependency-free file)
or swapping these three lines for whatever that project already has; it
does not mean re-deciding the security posture.

`crypto.py` is the one host import this module makes that isn't `db` — it
knows nothing about models or Flask, so importing it doesn't drag the app
in behind it. See the module docstring there for why each purpose gets its
own env var and salt.

Key resolution, in order:

1. `COMMS_ENCRYPTION_KEY` — a real Fernet key. This is what production
   should set. Generate one with:
       python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
2. Derived from `SECRET_KEY` via PBKDF2. Convenience for local dev. The
   catch, and it's why (1) exists: **rotating SECRET_KEY makes every stored
   token undecryptable**, and connected accounts have to be reconnected.
"""

from crypto import SecretBox, SecretDecryptionError

# The salt is part of the on-disk format: changing it makes every token
# already encrypted under the derived-key fallback unreadable.
_box = SecretBox(
    env_var="COMMS_ENCRYPTION_KEY",
    salt=b"atelier-communications-token-encryption-v1",
    decryption_hint=(
        "Stored OAuth token could not be decrypted. This normally means "
        "COMMS_ENCRYPTION_KEY (or SECRET_KEY, if you're relying on the "
        "derived key) changed since it was saved — the account needs to "
        "be disconnected and reconnected."
    ),
)

# Kept as this module's own name: callers catch "a token failed to decrypt",
# and the recovery for that (reconnect the account) is specific to what this
# module stores, even though the failure mode is generic.
TokenDecryptionError = SecretDecryptionError

encrypt = _box.encrypt
decrypt = _box.decrypt
using_derived_key = _box.using_derived_key
