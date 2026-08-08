"""
Tables: `ai_settings`, `ai_render_drafts`.

One `AISettings` row per company, holding two vendor API keys and two
prompts. No foreign key to `companies` and no relationship on `Company` —
this module never sees a host model, so `company_id` is a plain integer
here, the same way `billing/` treats its subject ids. Every query filters
on it, which is what a real FK would have bought us anyway.

`RenderDraft` carries `order_id` and `source_document_id` the same way, and
that's a sharper choice than it looks: `documents/` and `inventory/` both
hold a *real* foreign key into `orders`, because neither has a circular
import to break. This module could too, and doesn't — a real FK would make
it importable only into a project that has an `orders` table, which is the
one thing a vendor key and a prompt should never require.

The cost is that a deleted order leaves drafts behind. That's acceptable
because a draft is scratch by definition and already expires on a timer
(`config.DRAFT_RETENTION_HOURS`) — the pruner is the thing that collects
them, not the database.

**Keys are stored as Fernet ciphertext and never as plaintext.** The
`_encrypted` columns are the storage; the `text_api_key` / `image_api_key`
properties are the interface, mirroring how `EmailAccount` handles OAuth
tokens.
"""

from datetime import datetime, timezone

from models import db

from ai import config, crypto


def _utcnow() -> datetime:
    """Naive UTC 'now' — timestamps are naive UTC throughout this app (see
    communications/models.py's utcnow(), which this mirrors)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hint(key: str | None) -> str | None:
    """The tail of a key, for showing which one is saved without showing
    the key. Short keys are reported as saved with nothing revealed rather
    than leaking most of themselves — a real vendor key is far longer than
    this, so that branch means something is wrong, not that it's brief."""
    if not key:
        return None
    return f"…{key[-4:]}" if len(key) >= 12 else "…"


class AISettings(db.Model):
    """A company's AI configuration, created empty on first read.

    Created rather than returned-as-None for the same reason billing's
    `profile_for()` does it: a caller shouldn't have to special-case "not
    set up yet", and an empty row simply means the buttons don't appear.

    There's no `enabled` flag for either feature. Whether a feature is
    available is derived from whether its key is present (hard rule 10) —
    a stored flag saying "on" next to an absent key is a copy that can
    disagree with reality, and "off" is already spelled by removing the key.
    """

    __tablename__ = "ai_settings"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=False, unique=True, index=True)

    # --- Inquiry replies ---
    # Fernet ciphertext, never plaintext — see crypto.py. Read and written
    # through the properties below, not directly.
    text_api_key_encrypted = db.Column(db.Text)
    text_model = db.Column(db.String(120), nullable=False, default=config.TEXT_MODEL)
    reply_prompt = db.Column(db.Text, nullable=False, default=config.DEFAULT_REPLY_PROMPT)

    # --- Renderings ---
    image_api_key_encrypted = db.Column(db.Text)
    image_model = db.Column(db.String(120), nullable=False, default=config.IMAGE_MODEL)
    render_prompt = db.Column(db.Text, nullable=False, default=config.DEFAULT_RENDER_PROMPT)

    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    # ------------------------------------------------------------------
    # Keys. The setter treats "" as "clear it" and None as "leave it
    # alone", so a settings form that renders a blank field (which it
    # always does — a saved key is never sent back to the browser) can't
    # wipe a saved key just by being submitted. Hard rule 9, at the model
    # level rather than only in the route.
    # ------------------------------------------------------------------

    @property
    def text_api_key(self) -> str | None:
        return crypto.decrypt(self.text_api_key_encrypted)

    @text_api_key.setter
    def text_api_key(self, value: str | None) -> None:
        if value is None:
            return
        self.text_api_key_encrypted = crypto.encrypt(value.strip())

    @property
    def image_api_key(self) -> str | None:
        return crypto.decrypt(self.image_api_key_encrypted)

    @image_api_key.setter
    def image_api_key(self, value: str | None) -> None:
        if value is None:
            return
        self.image_api_key_encrypted = crypto.encrypt(value.strip())

    # ------------------------------------------------------------------
    # What the UI asks. `has_*_key` reads the column, not the property, so
    # "a key is saved" stays answerable even when the encryption key has
    # rotated and the value can no longer be decrypted — that's the state
    # the settings page most needs to describe, and raising there would
    # blank the page instead.
    # ------------------------------------------------------------------

    @property
    def has_text_key(self) -> bool:
        return bool(self.text_api_key_encrypted)

    @property
    def has_image_key(self) -> bool:
        return bool(self.image_api_key_encrypted)

    @property
    def text_key_hint(self) -> str | None:
        try:
            return _hint(self.text_api_key)
        except crypto.KeyDecryptionError:
            return None

    @property
    def image_key_hint(self) -> str | None:
        try:
            return _hint(self.image_api_key)
        except crypto.KeyDecryptionError:
            return None


class RenderDraft(db.Model):
    """One generated image, before anyone decided to keep it.

    **A draft is not a document** (`G-1`). It doesn't appear in the order's
    Documents area, doesn't count against the company's 1GB quota, and
    disappears on its own after `config.DRAFT_RETENTION_HOURS`. Saving is
    what turns one into a real `Document`, through a host hook — this module
    never writes into another module's storage (`G-4`).

    Drafts accumulate per source document on purpose: "render, look, adjust,
    render again" is the actual workflow, and comparing the third attempt
    against the first is the whole reason to keep them around (`G-3`). They
    are ordered newest first everywhere they're shown.
    """

    __tablename__ = "ai_render_drafts"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=False, index=True)
    # Plain integers, not foreign keys — see the module docstring.
    order_id = db.Column(db.Integer, nullable=False, index=True)
    source_document_id = db.Column(db.Integer, nullable=False, index=True)

    # What was asked for, kept so a draft worth keeping can be traced back
    # to the wording that produced it — the thing you most want when the
    # fourth attempt is worse than the second. The company-wide prompt is
    # *not* stored: it's one row away, and duplicating it per draft would
    # make "what changed between these two?" harder to read, not easier.
    extra_prompt = db.Column(db.Text, nullable=False, default="")

    stored_filename = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(100), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False, default=0)

    # Set when this draft became a Document, so the modal can say so rather
    # than offering to save it twice. Nullable — most drafts never are.
    saved_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow, index=True)

    @property
    def is_saved(self) -> bool:
        return self.saved_at is not None
