"""
Encryption for vendor API keys at rest.

Mechanics live in the host's `crypto.py`; this file declares which key and
which salt *this* module's secrets use. See `communications/crypto.py` for
the same shape, and the host module's docstring for why the two don't share
a box.

An API key is worse to leak than an OAuth token in one respect: there's no
per-account grant to revoke, only "rotate the key and every other thing
using it breaks too". So it gets the same treatment — never plaintext in
the database, key from the environment.

Key resolution, in order:

1. `AI_ENCRYPTION_KEY` — a real Fernet key, what production should set.
       python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
2. Derived from `SECRET_KEY` via PBKDF2, for local dev. Rotating
   `SECRET_KEY` then makes stored keys unreadable and they have to be
   re-entered — the settings page says so when it's running this way.
"""

from crypto import SecretBox, SecretDecryptionError

# The salt is part of the on-disk format: changing it makes every key
# already encrypted under the derived-key fallback unreadable.
_box = SecretBox(
    env_var="AI_ENCRYPTION_KEY",
    salt=b"atelier-ai-credential-encryption-v1",
    decryption_hint=(
        "Stored AI API key could not be decrypted. This normally means "
        "AI_ENCRYPTION_KEY (or SECRET_KEY, if you're relying on the derived "
        "key) changed since it was saved — the key needs to be entered again "
        "under Settings → AI."
    ),
)

# This module's own name for the failure, like communications'
# TokenDecryptionError: the recovery is specific to what's stored here.
KeyDecryptionError = SecretDecryptionError

encrypt = _box.encrypt
decrypt = _box.decrypt
using_derived_key = _box.using_derived_key
