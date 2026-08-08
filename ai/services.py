"""
Public API for the AI module. Every function takes `company_id` first — the
rest of the app should never reach into `ai.models` or `ai.crypto` directly.

Phase 1 is configuration only: the settings a company holds, and the two
predicates that say whether each feature can be offered. Generating a reply
and rendering an image land on top of this.
"""

from ai import config, conversation as conversation_text, crypto, openai_client
from ai.errors import AIError
from ai.models import AISettings
from models import db


def settings_for(company_id: int) -> AISettings:
    """This tenant's AI settings, created empty on first use.

    Created rather than returned-as-None for the same reason billing's
    `profile_for()` does it: nothing above has to special-case "not set up
    yet". An empty row means no key, which means no buttons — a state the
    rest of the app reads through `reply_available()` / `render_available()`
    rather than by inspecting columns.
    """
    settings = AISettings.query.filter_by(company_id=company_id).first()
    if settings is None:
        settings = AISettings(company_id=company_id)
        db.session.add(settings)
        db.session.commit()
    return settings


def reply_available(company_id: int) -> bool:
    """Whether the compose form should offer "Suggest response".

    Derived from the key being present, not from a stored flag (hard rule
    10). Cheap enough to call from a template: one indexed lookup, and the
    row is already in the session on any page that has touched settings.
    """
    return settings_for(company_id).has_text_key


def render_available(company_id: int) -> bool:
    """Whether a document's action row should offer the render button."""
    return settings_for(company_id).has_image_key


def can_render_document(company_id: int, content_type: str | None) -> bool:
    """Both halves of the question the documents explorer actually asks:
    is the feature configured, and is *this* file something an image model
    can be given. Kept here rather than in the template so the allowed
    content types stay one list in `config.py`."""
    return (
        content_type in config.SOURCE_IMAGE_CONTENT_TYPES
        and render_available(company_id)
    )


def suggest_reply(company_id: int, conversation: dict) -> str:
    """A draft reply to `conversation`, as plain text.

    **This never sends anything** (`R-1`). It returns text for a human to
    edit in the compose box, and the module has no path to a mail provider
    at all — sending stays entirely in `communications/`.

    Raises `AIError` with a message written for the user on every failure
    path: no key saved, the vendor rejecting the key, a rate limit, a
    timeout, an empty completion. The caller renders it beside the button
    and leaves whatever's already typed alone (`R-5`).
    """
    settings = settings_for(company_id)
    try:
        api_key = settings.text_api_key if settings.has_text_key else None
    except crypto.KeyDecryptionError as exc:
        # The encryption key rotated since the API key was saved. The
        # settings page renders this state fine (`S-5`); here it has to
        # become a sentence rather than a 500, and the recovery is to
        # re-enter the key.
        raise AIError(
            "The saved OpenAI key can't be decrypted — AI_ENCRYPTION_KEY or "
            "SECRET_KEY changed since it was saved. Enter the key again "
            "under Settings → AI."
        ) from exc
    if not api_key:
        # Reachable by racing the button against someone deleting the key
        # in another tab — the button doesn't render without one (`R-6`).
        raise AIError(
            "No OpenAI API key is saved. Add one under Settings → AI."
        )

    return openai_client.generate_reply(
        api_key=api_key,
        model=settings.text_model,
        instructions=settings.reply_prompt,
        conversation=conversation_text.render(conversation),
        timeout=config.TEXT_TIMEOUT_SECONDS,
    )


def using_derived_key() -> bool:
    """True when API keys are being encrypted with the SECRET_KEY-derived
    dev fallback. The settings page says so — same warning, and the same
    reasoning, as communications' integrations page (its `S-3`)."""
    return crypto.using_derived_key()


# ---------------------------------------------------------------------------
# Saving. Both savers take every field as `None`-by-default, meaning "the
# form didn't render this, leave it alone" (hard rule 9) — so the two
# sections of the settings page can post independently without either
# blanking the other's fields.
# ---------------------------------------------------------------------------

def save_reply_settings(
    company_id: int,
    *,
    api_key: str | None = None,
    model: str | None = None,
    prompt: str | None = None,
) -> AISettings:
    settings = settings_for(company_id)
    settings.text_api_key = api_key
    if model is not None:
        settings.text_model = model.strip() or config.TEXT_MODEL
    if prompt is not None:
        settings.reply_prompt = prompt.strip() or config.DEFAULT_REPLY_PROMPT
    db.session.commit()
    return settings


def save_render_settings(
    company_id: int,
    *,
    api_key: str | None = None,
    model: str | None = None,
    prompt: str | None = None,
) -> AISettings:
    settings = settings_for(company_id)
    settings.image_api_key = api_key
    if model is not None:
        settings.image_model = model.strip() or config.IMAGE_MODEL
    if prompt is not None:
        settings.render_prompt = prompt.strip() or config.DEFAULT_RENDER_PROMPT
    db.session.commit()
    return settings


def clear_text_key(company_id: int) -> None:
    """Remove the stored key. This is how a company turns the feature off —
    there's no separate switch, deliberately (see AISettings' docstring).
    The model and prompt are kept, so putting the key back restores the
    setup rather than starting over."""
    settings = settings_for(company_id)
    settings.text_api_key_encrypted = None
    db.session.commit()


def clear_image_key(company_id: int) -> None:
    settings = settings_for(company_id)
    settings.image_api_key_encrypted = None
    db.session.commit()
