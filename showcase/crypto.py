"""
Encryption for the website secret at rest (SC41).

Mechanics live in the host's `crypto.py`, the one host helper a module may
import (hard rule 4); this file names *this* module's key and salt, the
same shape as `ai/crypto.py` and `communications/crypto.py`. The secret
signs every call to the studio's website, so a stolen database alone must
not be enough to post cards there.

Key resolution, in order:

1. `SHOWCASE_ENCRYPTION_KEY` — a real Fernet key, what production should set.
       python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
2. Derived from `SECRET_KEY` via PBKDF2, for local dev. Rotating
   `SECRET_KEY` then makes the stored secret unreadable: the website has to
   be connected again (Settings says so).
"""

from crypto import SecretBox, SecretDecryptionError

# The salt is part of the on-disk format: changing it makes every secret
# already encrypted under the derived-key fallback unreadable.
_box = SecretBox(
    env_var="SHOWCASE_ENCRYPTION_KEY",
    salt=b"atelier-showcase-website-secret-v1",
    decryption_hint=(
        "The website secret can't be read with the current key. "
        "Delete the website connection and connect it again."
    ),
)

encrypt = _box.encrypt
decrypt = _box.decrypt

__all__ = ["encrypt", "decrypt", "SecretDecryptionError"]
