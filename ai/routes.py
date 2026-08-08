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

from flask import Blueprint, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from ai import config, services

bp = Blueprint("ai", __name__, template_folder="templates")


def register(app) -> None:
    app.register_blueprint(bp)


def _flash(message: str) -> None:
    """One-shot message for the next page render. Same reasoning as
    documents' and communications' `_flash`: the app has no flash
    convention, so this stays scoped to a session key this module owns."""
    session["ai_notice"] = message


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
