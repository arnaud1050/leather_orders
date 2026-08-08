"""
Public API for the AI module. Every function takes `company_id` first — the
rest of the app should never reach into `ai.models` or `ai.crypto` directly.

Phase 1 is configuration only: the settings a company holds, and the two
predicates that say whether each feature can be offered. Generating a reply
and rendering an image land on top of this.
"""

from datetime import timedelta

from ai import (
    config, conversation as conversation_text, crypto, google_image_client,
    openai_client, storage,
)
from ai.errors import AIError
from ai.models import AISettings, RenderDraft, _utcnow
from models import db


def normalise_newlines(text: str) -> str:
    """CRLF (and bare CR) to LF.

    **A browser submits textarea content with CRLF line endings**, per the
    HTML spec, while every default shipped in `config.py` uses LF. Without
    this, a prompt saved through the form is byte-different from the same
    prompt in code — which makes "has this company edited its prompt?"
    unanswerable, and that question is what `migrations.py` relies on to
    move an untouched default forward. Found by diffing a real database
    against the recorded default, not in a test.
    """
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


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


def suggest_reply(company_id: int, conversation: dict, signature: str = "") -> str:
    """A draft reply to `conversation`, as plain text.

    **This never sends anything** (`R-1`). It returns text for a human to
    edit in the compose box, and the module has no path to a mail provider
    at all — sending stays entirely in `communications/`.

    `signature` is **appended in code, never asked of the model** (`R-11`).
    The prompt tells it to stop at its last sentence; the sign-off is then
    exact by construction, costs no tokens, and can't be paraphrased into
    someone else's name. It arrives as a plain string, like everything else
    crossing this boundary — the module never sees a `User`.

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

    draft = openai_client.generate_reply(
        api_key=api_key,
        model=settings.text_model,
        instructions=settings.reply_prompt,
        conversation=conversation_text.render(conversation),
        timeout=config.TEXT_TIMEOUT_SECONDS,
    )
    return draft + (f"\n\n{signature.strip()}" if signature.strip() else "")


# ---------------------------------------------------------------------------
# Renderings. A draft is scratch until someone saves it (`G-1`) — it isn't a
# document, doesn't touch the company's storage quota, and expires on its own.
# ---------------------------------------------------------------------------

def _image_key(company_id: int) -> tuple[AISettings, str]:
    settings = settings_for(company_id)
    try:
        api_key = settings.image_api_key if settings.has_image_key else None
    except crypto.KeyDecryptionError as exc:
        raise AIError(
            "The saved Google key can't be decrypted — AI_ENCRYPTION_KEY or "
            "SECRET_KEY changed since it was saved. Enter the key again "
            "under Settings → AI."
        ) from exc
    if not api_key:
        raise AIError("No Google AI Studio API key is saved. Add one under "
                      "Settings → AI.")
    return settings, api_key


def render_prompt_for(company_id: int, extra_prompt: str = "") -> str:
    """The company prompt with this project's details **added, not
    substituted** (`G-2`).

    Separate from `render_from_document` so the modal can show exactly what
    will be sent before anything is charged for, and so the composition is
    testable without a vendor.
    """
    settings = settings_for(company_id)
    extra = normalise_newlines(extra_prompt).strip()
    return f"{settings.render_prompt}\n\n{extra}" if extra else settings.render_prompt


def render_from_document(
    company_id: int, order_id: int, document_id: int,
    source_image: bytes, source_content_type: str, extra_prompt: str = "",
) -> RenderDraft:
    """Generate one image from one source, and keep it as a draft.

    The source arrives as bytes from a host hook — this module never reads
    another module's storage, the same way it never writes to it (`G-4`).

    Raises `AIError` on every failure path, including a source image that's
    too large or the wrong type. Those two are checked **before** the vendor
    call rather than after: they're the cases where a charge would be
    incurred for something we already know won't work.
    """
    if source_content_type not in config.SOURCE_IMAGE_CONTENT_TYPES:
        raise AIError("Only JPEG and PNG images can be rendered from.")
    if len(source_image) > config.MAX_SOURCE_IMAGE_BYTES:
        limit_mb = config.MAX_SOURCE_IMAGE_BYTES // (1024 * 1024)
        raise AIError(f"That image is too large to send — the limit is {limit_mb}MB.")

    settings, api_key = _image_key(company_id)
    data, content_type = google_image_client.generate_image(
        api_key=api_key,
        model=settings.image_model,
        instructions=render_prompt_for(company_id, extra_prompt),
        source_image=source_image,
        source_content_type=source_content_type,
        timeout=config.IMAGE_TIMEOUT_SECONDS,
    )

    draft = RenderDraft(
        company_id=company_id, order_id=order_id, source_document_id=document_id,
        extra_prompt=normalise_newlines(extra_prompt).strip(),
        stored_filename=storage.save(company_id, data, content_type),
        content_type=content_type, size_bytes=len(data),
    )
    db.session.add(draft)
    db.session.commit()
    return draft


def drafts_for_document(company_id: int, document_id: int) -> list[RenderDraft]:
    """Every attempt for one source document, newest first — which is what
    makes comparing the third against the first possible (`G-3`)."""
    return (
        RenderDraft.query.filter_by(company_id=company_id, source_document_id=document_id)
        .order_by(RenderDraft.created_at.desc(), RenderDraft.id.desc())
        .all()
    )


def get_draft(company_id: int, draft_id: int) -> RenderDraft | None:
    return RenderDraft.query.filter_by(id=draft_id, company_id=company_id).first()


def draft_bytes(draft: RenderDraft) -> bytes | None:
    return storage.read(draft.company_id, draft.stored_filename)


def discard_draft(company_id: int, draft_id: int) -> bool:
    """Throw one away. The file goes with the row — a draft nobody kept
    shouldn't leave bytes on the disk behind it."""
    draft = get_draft(company_id, draft_id)
    if draft is None:
        return False
    storage.delete(draft.company_id, draft.stored_filename)
    db.session.delete(draft)
    db.session.commit()
    return True


def mark_saved(draft: RenderDraft) -> None:
    """Record that this draft became a document.

    The file and row are deliberately **kept**: the bytes now live in
    `documents/` as well, and keeping the draft is what lets the modal say
    "saved" instead of offering to save the same image twice. The pruner
    collects it on the normal schedule.
    """
    draft.saved_at = _utcnow()
    db.session.commit()


def prune_expired_drafts(company_id: int | None = None) -> int:
    """Delete drafts past `config.DRAFT_RETENTION_HOURS`, files and all.

    `company_id=None` means every tenant — the housekeeping view, and the
    one place a query here legitimately isn't tenant-scoped, same exemption
    communications' scheduled sync has. Returns how many went.

    Saved drafts are pruned too: the image itself survives as a `Document`,
    and keeping the draft forever would mean the same bytes stored twice
    indefinitely.
    """
    cutoff = _utcnow() - timedelta(hours=config.DRAFT_RETENTION_HOURS)
    query = RenderDraft.query.filter(RenderDraft.created_at < cutoff)
    if company_id is not None:
        query = query.filter(RenderDraft.company_id == company_id)

    expired = query.all()
    for draft in expired:
        storage.delete(draft.company_id, draft.stored_filename)
        db.session.delete(draft)
    if expired:
        db.session.commit()
    return len(expired)


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
        settings.reply_prompt = normalise_newlines(prompt).strip() or config.DEFAULT_REPLY_PROMPT
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
        settings.render_prompt = normalise_newlines(prompt).strip() or config.DEFAULT_RENDER_PROMPT
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
