"""Token encryption at rest. No app context needed — pure functions."""

import os

import pytest

from communications import crypto


def test_roundtrip():
    assert crypto.decrypt(crypto.encrypt("refresh-token-value")) == "refresh-token-value"


def test_ciphertext_is_not_the_plaintext():
    """The whole point: a stolen atelier.db must not contain usable tokens."""
    ciphertext = crypto.encrypt("ya29.super-secret")
    assert "ya29.super-secret" not in ciphertext


def test_encryption_is_non_deterministic():
    """Fernet includes a random IV, so the same token encrypts differently
    each time — an attacker can't tell two accounts share a token."""
    assert crypto.encrypt("same") != crypto.encrypt("same")


@pytest.mark.parametrize("empty", [None, ""])
def test_empty_values_pass_through(empty):
    """A missing refresh token is a legitimate state (Google only issues one
    on first consent), so it must not blow up on the way to the column."""
    assert crypto.encrypt(empty) is None
    assert crypto.decrypt(empty) is None


def test_decrypt_with_wrong_key_raises_typed_error(monkeypatch):
    """Rotating COMMS_ENCRYPTION_KEY must fail loudly and specifically, so
    callers can say "reconnect the account" rather than 500."""
    ciphertext = crypto.encrypt("token")
    monkeypatch.setenv("COMMS_ENCRYPTION_KEY", "cH8kV2nQ5xL9pR3tY7wA1sD4fG6hJ0kM8nB2vC5xZ1E=")
    with pytest.raises(crypto.TokenDecryptionError):
        crypto.decrypt(ciphertext)


def test_decrypt_rejects_tampered_ciphertext():
    """Fernet is authenticated: a modified token fails rather than
    decrypting to garbage that we'd then send to Google."""
    ciphertext = crypto.encrypt("token")
    tampered = ciphertext[:-4] + ("aaaa" if not ciphertext.endswith("aaaa") else "bbbb")
    with pytest.raises(crypto.TokenDecryptionError):
        crypto.decrypt(tampered)


def test_using_derived_key_reflects_environment(monkeypatch):
    monkeypatch.delenv("COMMS_ENCRYPTION_KEY", raising=False)
    assert crypto.using_derived_key() is True
    monkeypatch.setenv("COMMS_ENCRYPTION_KEY", os.environ.get("COMMS_ENCRYPTION_KEY", "x") or "x")
    monkeypatch.setenv("COMMS_ENCRYPTION_KEY", "cH8kV2nQ5xL9pR3tY7wA1sD4fG6hJ0kM8nB2vC5xZ1E=")
    assert crypto.using_derived_key() is False


def test_derived_key_is_stable_across_calls(monkeypatch):
    """The dev fallback has to survive a restart, or every token dies on
    each boot — so the derivation must be deterministic from SECRET_KEY."""
    monkeypatch.delenv("COMMS_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("SECRET_KEY", "stable-dev-secret")
    ciphertext = crypto.encrypt("token")
    assert crypto.decrypt(ciphertext) == "token"


def test_derived_key_changes_with_secret_key(monkeypatch):
    """The documented catch: rotating SECRET_KEY orphans stored tokens."""
    monkeypatch.delenv("COMMS_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("SECRET_KEY", "first-secret")
    ciphertext = crypto.encrypt("token")
    monkeypatch.setenv("SECRET_KEY", "second-secret")
    with pytest.raises(crypto.TokenDecryptionError):
        crypto.decrypt(ciphertext)
