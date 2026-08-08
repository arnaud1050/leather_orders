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

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from ai import config, services
from ai.errors import AIError

bp = Blueprint("ai", __name__, template_folder="templates")

_resolve_thread_context = None


def register(app, *, resolve_thread_context=None) -> None:
    """Attach the blueprint.

    `resolve_thread_context(company_id, thread_id) -> dict | None` is
    supplied by the host (app.py) rather than imported, the same hook shape
    `documents.routes.register(resolve_order=...)` uses — and here it's what
    keeps this module from ever importing `communications`. It returns the
    plain dict `ai.conversation.render` expects, or None when the thread
    isn't this company's.

    Optional, so a host that doesn't wire it up gets a settings page and a
    button that says the feature isn't available, rather than an import
    error at startup.
    """
    global _resolve_thread_context
    _resolve_thread_context = resolve_thread_context
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

    return {"ai_reply_available": ai_reply_available}


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
        suggestion = services.suggest_reply(current_user.company_id, conversation)
    except AIError as exc:
        return jsonify(error=str(exc)), 502

    return jsonify(suggestion=suggestion)


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
