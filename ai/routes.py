"""
The module's blueprint. Phase 1 is the Settings → AI page and the four
posts that maintain it.

No CSRF layer here, matching `documents/` and every mutating route in
`app.py`, which rely on the app-wide `SESSION_COOKIE_SAMESITE=Lax` rather
than a token. That's a real decision rather than an omission: writing an
API key is closer to communications' class of risk than to editing an order
line, but `SameSite=Lax` already stops a cross-site form post from carrying
the session cookie, and a second hand-rolled CSRF implementation is worse
than one. **If Flask-WTF is ever added app-wide, this module and
`communications/security.py` should both defer to `CSRFProtect`.**
"""

import io

from flask import (
    Blueprint, jsonify, redirect, render_template, request, send_file, session, url_for,
)
from flask_login import current_user, login_required

from ai import config, services
from ai.errors import AIError

bp = Blueprint("ai", __name__, template_folder="templates")

_resolve_thread_context = None
_load_document = None
_save_render = None


def register(app, *, resolve_thread_context=None, load_document=None, save_render=None) -> None:
    """Attach the blueprint.

    Three host-supplied hooks, all optional and all the same shape
    `documents.routes.register(resolve_order=...)` uses — passed in rather
    than imported, which is what keeps this module from ever importing
    `communications/` or `documents/`. A hook left unwired disables its
    feature with a message, rather than failing at startup.

    - `resolve_thread_context(company_id, thread_id) -> dict | None` — the
      conversation `ai.conversation.render` expects, or None when the thread
      isn't this company's.
    - `load_document(company_id, order_id, document_id) -> dict | None` —
      `{"filename", "content_type", "data"}` for the image to render from,
      already tenant-checked, or None.
    - `save_render(company_id, order_id, filename, content_type, data) ->
      str | None` — store this image as a real document; returns an error
      message, or None on success. **This module never writes into another
      module's storage** (`G-4`), so quota and validation still apply on the
      far side.
    """
    global _resolve_thread_context, _load_document, _save_render
    _resolve_thread_context = resolve_thread_context
    _load_document = load_document
    _save_render = save_render
    app.register_blueprint(bp)


@bp.app_context_processor
def _inject_availability():
    """`ai_reply_available()` for `communications/templates/_compose_form.html`.

    Injected as a *callable* rather than a value, so the query only runs on
    templates that actually ask — the same reason communications' badge
    counts are callables. Forgiving of a logged-out request and of a
    database that hasn't been migrated yet, for the same reason: this
    renders inside `base.html`'s descendants on pages that must not 500
    because of an optional feature.
    """
    def ai_reply_available() -> bool:
        if _resolve_thread_context is None or not current_user.is_authenticated:
            return False
        try:
            return services.reply_available(current_user.company_id)
        except Exception:  # noqa: BLE001 — an optional feature never breaks a page
            return False

    def ai_can_render(content_type) -> bool:
        """Whether *this* document gets a Render action — the key is saved
        **and** the file is something an image model can be given (`A-3`).
        Called once per document row, so it's a predicate rather than a
        flag the explorer would have to compute itself."""
        if _load_document is None or _save_render is None:
            return False
        if not current_user.is_authenticated:
            return False
        try:
            return services.can_render_document(current_user.company_id, content_type)
        except Exception:  # noqa: BLE001
            return False

    return {"ai_reply_available": ai_reply_available, "ai_can_render": ai_can_render}


def _flash(message: str) -> None:
    """One-shot message for the next page render. Same reasoning as
    documents' and communications' `_flash`: the app has no flash
    convention, so this stays scoped to a session key this module owns."""
    session["ai_notice"] = message


@bp.route("/ai/suggest-reply", methods=["POST"])
@login_required
def suggest_reply():
    """Draft a reply to one thread. JSON in, JSON out — the compose form
    fetches this and fills its own textarea, so there's no page navigation
    and nothing to redirect back to.

    Every failure answers `{"error": "<a sentence>"}` with a real status
    code. The browser side reads `error` regardless of status, so the two
    can't disagree about whether something went wrong.
    """
    if _resolve_thread_context is None:
        return jsonify(error="Reply suggestions aren't wired up on this "
                             "deployment."), 501

    payload = request.get_json(silent=True) or request.form
    raw_thread_id = str(payload.get("thread_id", ""))
    if not raw_thread_id.isdigit():
        return jsonify(error="No conversation to reply to."), 400

    conversation = _resolve_thread_context(current_user.company_id, int(raw_thread_id))
    if conversation is None:
        # Another company's thread, or one that's since been deleted. Same
        # answer either way — "not yours" and "not there" must not be
        # distinguishable from outside.
        return jsonify(error="No conversation to reply to."), 404

    try:
        suggestion = services.suggest_reply(
            current_user.company_id, conversation,
            # Read straight off the session's user — a signature belongs to
            # a person, and this route already knows which one. No host hook
            # needed, and the module still never sees a `User`.
            signature=getattr(current_user, "signature", "") or "",
        )
    except AIError as exc:
        return jsonify(error=str(exc)), 502

    return jsonify(suggestion=suggestion)


# ---------------------------------------------------------------------------
# Renderings. JSON in, JSON out, like suggest_reply — the modal drives all
# of it with fetch, so there's no page navigation and nothing to redirect to.
# ---------------------------------------------------------------------------

def _draft_json(draft) -> dict:
    return {
        "id": draft.id,
        "url": url_for("ai.render_image", draft_id=draft.id),
        "extra_prompt": draft.extra_prompt,
        "saved": draft.is_saved,
    }


@bp.route("/orders/<int:order_id>/documents/<int:document_id>/renders")
@login_required
def render_history(order_id: int, document_id: int):
    """Every attempt so far for this source image, newest first — what the
    modal shows when it opens, so a comparison survives closing it.

    Expired drafts are pruned here, tenant-scoped, rather than by a
    scheduled job. This module deliberately has no `jobs.py`: the app's only
    scheduler is opt-in (`RUN_SCHEDULER=1`, off by default and off in both
    Docker deployments), so anything hung on it would in practice never run.
    Opening this window is the moment a company's drafts are provably being
    looked at, which makes it the honest place to collect the ones that
    aged out — and it's self-limiting, since a company that stops rendering
    stops accumulating.
    """
    services.prune_expired_drafts(current_user.company_id)
    drafts = services.drafts_for_document(current_user.company_id, document_id)
    return jsonify(
        drafts=[_draft_json(d) for d in drafts if d.order_id == order_id],
        prompt=services.render_prompt_for(current_user.company_id),
    )


@bp.route("/orders/<int:order_id>/documents/<int:document_id>/render", methods=["POST"])
@login_required
def render_document(order_id: int, document_id: int):
    if _load_document is None:
        return jsonify(error="Rendering isn't wired up on this deployment."), 501

    payload = request.get_json(silent=True) or request.form
    source = _load_document(current_user.company_id, order_id, document_id)
    if source is None:
        # Another company's document, or one that's gone. Same answer for
        # both — "not yours" and "not there" must not be distinguishable.
        return jsonify(error="That document isn't available."), 404

    try:
        draft = services.render_from_document(
            current_user.company_id, order_id, document_id,
            source_image=source["data"],
            source_content_type=source["content_type"],
            extra_prompt=payload.get("extra_prompt", "") or "",
        )
    except AIError as exc:
        return jsonify(error=str(exc)), 502

    return jsonify(draft=_draft_json(draft))


@bp.route("/ai/renders/<int:draft_id>/image")
@login_required
def render_image(draft_id: int):
    """Serve a draft's bytes.

    Served from memory rather than by path: `send_file` with a path would
    work, but a draft is small, short-lived and read at most a handful of
    times, and going through `services.draft_bytes` keeps storage layout
    behind one function. Only ever an image content type this module wrote
    itself, so inline rendering is safe here in a way it isn't for uploads.
    """
    draft = services.get_draft(current_user.company_id, draft_id)
    if draft is None:
        return jsonify(error="That render isn't available."), 404
    data = services.draft_bytes(draft)
    if data is None:
        return jsonify(error="That render's file is missing."), 404
    return send_file(io.BytesIO(data), mimetype=draft.content_type)


@bp.route("/ai/renders/<int:draft_id>/save", methods=["POST"])
@login_required
def save_render(draft_id: int):
    """Turn a draft into a real document, through the host hook (`G-4`)."""
    if _save_render is None:
        return jsonify(error="Rendering isn't wired up on this deployment."), 501

    draft = services.get_draft(current_user.company_id, draft_id)
    if draft is None:
        return jsonify(error="That render isn't available."), 404

    data = services.draft_bytes(draft)
    if data is None:
        return jsonify(error="That render's file is missing."), 404

    extension = "jpg" if draft.content_type == "image/jpeg" else "png"
    error = _save_render(
        current_user.company_id, draft.order_id,
        f"rendering-{draft.id}.{extension}", draft.content_type, data,
    )
    if error:
        # Quota, validation — decided by documents/, reported verbatim,
        # because that module's messages are already written for a person.
        return jsonify(error=error), 400

    services.mark_saved(draft)
    return jsonify(saved=True, draft=_draft_json(draft))


@bp.route("/ai/renders/<int:draft_id>/discard", methods=["POST"])
@login_required
def discard_render(draft_id: int):
    if not services.discard_draft(current_user.company_id, draft_id):
        return jsonify(error="That render isn't available."), 404
    return jsonify(discarded=True)


@bp.route("/settings/ai")
@login_required
def settings():
    return render_template(
        "ai/settings.html",
        section="ai",
        active_view="settings",
        settings=services.settings_for(current_user.company_id),
        using_derived_key=services.using_derived_key(),
        default_text_model=config.TEXT_MODEL,
        default_image_model=config.IMAGE_MODEL,
        notice=session.pop("ai_notice", None),
    )


def _submitted(field: str) -> str | None:
    """A form value, or None when the form didn't render the field at all.

    Hard rule 9: absent means "leave it alone". Present-but-blank keeps its
    own meaning per field — for a prompt it restores the default, and for a
    key it's ignored, since the key input is always rendered blank (a saved
    key is never sent back to the browser) and submitting the section
    without retyping it must not wipe it.
    """
    return request.form[field] if field in request.form else None


def _submitted_key(field: str) -> str | None:
    value = _submitted(field)
    return value if value and value.strip() else None


@bp.route("/settings/ai/replies", methods=["POST"])
@login_required
def save_replies():
    services.save_reply_settings(
        current_user.company_id,
        api_key=_submitted_key("api_key"),
        model=_submitted("model"),
        prompt=_submitted("prompt"),
    )
    _flash("Inquiry reply settings saved.")
    return redirect(url_for("ai.settings"))


@bp.route("/settings/ai/renders", methods=["POST"])
@login_required
def save_renders():
    services.save_render_settings(
        current_user.company_id,
        api_key=_submitted_key("api_key"),
        model=_submitted("model"),
        prompt=_submitted("prompt"),
    )
    _flash("Rendering settings saved.")
    return redirect(url_for("ai.settings"))


@bp.route("/settings/ai/replies/key/delete", methods=["POST"])
@login_required
def delete_reply_key():
    services.clear_text_key(current_user.company_id)
    _flash("OpenAI API key removed. Reply suggestions are switched off.")
    return redirect(url_for("ai.settings"))


@bp.route("/settings/ai/renders/key/delete", methods=["POST"])
@login_required
def delete_render_key():
    services.clear_image_key(current_user.company_id)
    _flash("Google AI API key removed. Rendering is switched off.")
    return redirect(url_for("ai.settings"))
