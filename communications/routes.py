"""
Flask blueprint for the communications module.

A blueprint rather than more routes in app.py: this is the one part of the
app that's meant to be liftable into another project whole, and 40 routes
bolted onto app.py would make that a copy-paste job instead of an import.
CLAUDE.md's "new routes go in app.py" rule is about the app's own
features — this is a module with its own templates, models and services.

Registered in app.py via `communications.routes.register(app)`.

Every route is @login_required and derives its tenant from
`current_user.company_id` — never from the URL. The blueprint also
enforces CSRF on every unsafe request (see security.validate_csrf).
"""

from flask import (
    Blueprint, abort, current_app, redirect, render_template, request, session, url_for,
)
from flask_login import current_user, login_required

from models import db

from communications import config, jobs
from communications.models import (
    AUDIT_EVENT_LABELS, EmailMessage, EmailSyncSettings, EmailThread,
)
from communications.oauth import google_oauth
from communications.providers.base import ProviderError
from communications.providers.registry import PROVIDER_LABELS
from communications.security import csrf_token, validate_csrf
from communications.services import account_service, audit, email_service

bp = Blueprint(
    "communications", __name__,
    template_folder="templates",
)


@bp.before_request
def _protect():
    """CSRF on every unsafe request into this blueprint, by default.

    A route added below is covered without having to remember a decorator —
    see security.py.
    """
    validate_csrf()


def _flash(message: str, category: str = "info") -> None:
    """One-shot message for the next page render.

    The app has no flash-message convention yet (every existing form just
    redirects), so rather than introduce Flask's flash + a base.html
    partial across the whole app, notices are kept in the session under one
    key and rendered only by this module's templates.
    """
    session["comms_notice"] = {"message": message, "category": category}


def _take_notice():
    return session.pop("comms_notice", None)


# ---------------------------------------------------------------------------
# Integrations settings page. Sits under /settings as a third category
# alongside Invoicing and Order preferences, following the same
# .settings-nav pattern (see CLAUDE.md, "Settings").
# ---------------------------------------------------------------------------

@bp.route("/settings/integrations")
@login_required
def integrations():
    company_id = current_user.company_id
    accounts = account_service.accounts_for(company_id)
    return render_template(
        "integrations.html",
        section="integrations",
        accounts=accounts,
        sync_settings=EmailSyncSettings.for_company(company_id),
        scope_summary=account_service.scope_summary,
        available_providers=PROVIDER_LABELS,
        configuration_problem=config.configuration_problem(),
        using_derived_key=_using_derived_key(),
        scheduler_enabled=jobs.scheduler_enabled(),
        audit_entries=audit.recent(company_id, limit=15),
        audit_labels=AUDIT_EVENT_LABELS,
        notice=_take_notice(),
        active_view="settings",
    )


def _using_derived_key() -> bool:
    from communications import crypto

    return crypto.using_derived_key()


# ---------------------------------------------------------------------------
# OAuth. /connect starts the flow, /callback finishes it. The callback is
# the only route Google itself sends a browser to, and its security rests
# entirely on the session-stored state token — see google_oauth.finish_flow.
# ---------------------------------------------------------------------------

@bp.route("/integrations/google/connect", methods=["POST"])
@login_required
def google_connect():
    try:
        url = google_oauth.start_flow(
            session,
            company_id=current_user.company_id,
            return_to=url_for("communications.integrations"),
        )
    except ProviderError as exc:
        _flash(str(exc), "error")
        return redirect(url_for("communications.integrations"))
    return redirect(url)


@bp.route("/integrations/google/callback")
@login_required
def google_callback():
    """Where Google returns the user after consent.

    @login_required as well as state-validated: the account has to attach
    to *a* company, and the only trustworthy source for which one is the
    logged-in session that started the flow.
    """
    if request.args.get("error"):
        # User hit "Cancel" on the consent screen, or Google refused.
        _flash(f"Google sign-in was cancelled ({request.args['error']}).", "error")
        return redirect(url_for("communications.integrations"))

    try:
        tokens, company_id, return_to = google_oauth.finish_flow(
            session, request.url, request.args.get("state"),
        )
    except ProviderError as exc:
        _flash(str(exc), "error")
        return redirect(url_for("communications.integrations"))

    # The flow was started by this user, for this company — but check
    # anyway. A session that changed identity mid-flow (logged out and back
    # in as someone else) must not graft a mailbox onto the wrong tenant.
    if company_id != current_user.company_id:
        _flash("That sign-in was started by a different account. Please try again.", "error")
        return redirect(url_for("communications.integrations"))

    try:
        userinfo = google_oauth.fetch_userinfo(tokens["access_token"])
        account = account_service.connect_google_account(company_id, tokens, userinfo)
    except Exception as exc:  # noqa: BLE001 — surface, don't 500 the callback
        current_app.logger.exception("Google account connection failed")
        db.session.rollback()
        _flash(f"Could not finish connecting the account: {exc}", "error")
        return redirect(url_for("communications.integrations"))

    db.session.commit()
    _flash(f"Connected {account.email_address}.", "success")
    return redirect(return_to)


@bp.route("/integrations/accounts/<int:account_id>/disconnect", methods=["POST"])
@login_required
def disconnect_account(account_id: int):
    if account_service.disconnect(current_user.company_id, account_id):
        db.session.commit()
        _flash("Account disconnected and its synced mail removed.", "success")
    else:
        abort(404)
    return redirect(url_for("communications.integrations"))


@bp.route("/integrations/accounts/<int:account_id>/flags", methods=["POST"])
@login_required
def update_account_flags(account_id: int):
    """Per-account switches. Each button posts exactly the flag it owns, so
    a form that doesn't render a field leaves it alone — the same rule the
    client modal follows for address fields (see edit_client in app.py)."""
    flags = {}
    for flag in ("sync_enabled", "send_enabled", "is_default"):
        if flag in request.form:
            flags[flag] = request.form.get(flag) == "1"
    if account_service.set_flags(current_user.company_id, account_id, **flags) is None:
        abort(404)
    db.session.commit()
    return redirect(url_for("communications.integrations"))


@bp.route("/integrations/sync-settings", methods=["POST"])
@login_required
def update_sync_settings():
    settings = EmailSyncSettings.for_company(current_user.company_id)
    settings.sync_enabled = request.form.get("sync_enabled") == "on"
    settings.sync_sent_mail = request.form.get("sync_sent_mail") == "on"
    settings.sync_attachments = request.form.get("sync_attachments") == "on"
    settings.keep_unmatched = request.form.get("keep_unmatched") == "on"

    # Clamped rather than validated-and-rejected: these are dials, not
    # data. A 1-minute frequency is a good way to get rate-limited by
    # Google, and a 3650-day initial sync is a good way to hang the first
    # run — so the bounds are enforced here rather than trusted from a
    # number input that anyone can edit.
    settings.sync_frequency = _clamp(request.form.get("sync_frequency"), 5, 1440, 15)
    settings.initial_sync_days = _clamp(request.form.get("initial_sync_days"), 1, 730, 90)

    db.session.commit()
    _flash("Sync settings saved.", "success")
    return redirect(url_for("communications.integrations"))


def _clamp(raw: str | None, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(raw)))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Syncing. The same email_service.sync_now() the scheduled job calls, so a
# manual run and an automatic one can't behave differently.
# ---------------------------------------------------------------------------

@bp.route("/integrations/sync", methods=["POST"])
@login_required
def sync_now():
    results = email_service.sync_now(current_user.company_id)
    if not results:
        _flash("No mailbox is connected and enabled for syncing.", "error")
    elif all(result.ok for result in results):
        _flash(" ".join(result.summary() for result in results), "success")
    else:
        _flash(
            " ".join(result.summary() for result in results if not result.ok), "error",
        )
    return redirect(request.form.get("return_to") or url_for("communications.integrations"))


# ---------------------------------------------------------------------------
# Mail. A client's conversations live as a third tab on the client page
# (same .settings-nav sub-nav pattern as Information / Orders); unmatched
# mail collects in the lead inbox at /mail/leads.
# ---------------------------------------------------------------------------

@bp.route("/clients/<int:client_id>/emails")
@login_required
def client_emails(client_id: int):
    from app import back_label, get_client_or_404  # avoids a circular import at module load

    client = get_client_or_404(client_id)
    return_to = request.args.get("return_to") or url_for("timeline_view")
    return render_template(
        "client_emails.html",
        section="emails",
        client=client,
        threads=email_service.threads_for_client(current_user.company_id, client_id),
        send_account=account_service.default_account(current_user.company_id),
        return_to=return_to,
        back_label=back_label(return_to),
        notice=_take_notice(),
        active_view=None,
    )


@bp.route("/mail/threads/<int:thread_id>")
@login_required
def thread_page(thread_id: int):
    thread = email_service.get_thread(current_user.company_id, thread_id)
    if thread is None:
        abort(404)
    return_to = request.args.get("return_to") or url_for("communications.leads")
    return render_template(
        "thread_page.html",
        thread=thread,
        send_account=account_service.default_account(current_user.company_id),
        return_to=return_to,
        notice=_take_notice(),
        active_view=None,
    )


@bp.route("/mail/leads")
@login_required
def leads():
    """Conversations the app couldn't match to anyone on file.

    Kept out of the client list on purpose — these are unidentified
    senders, and a "client" record per inbound email would make the roster
    meaningless. Converting one is a deliberate click (see convert_lead).
    """
    return render_template(
        "leads.html",
        threads=email_service.lead_threads(current_user.company_id),
        notice=_take_notice(),
        active_view="clients",
    )


@bp.route("/mail/threads/<int:thread_id>/create-client", methods=["POST"])
@login_required
def convert_lead(thread_id: int):
    try:
        client = email_service.create_client_from_thread(
            current_user.company_id, thread_id,
            first_name=request.form.get("first_name"),
            last_name=request.form.get("last_name"),
            email=request.form.get("email"),
        )
    except email_service.EmailServiceError as exc:
        _flash(str(exc), "error")
        return redirect(request.form.get("return_to") or url_for("communications.leads"))

    _flash(f"Created {client.name} and linked this conversation.", "success")
    return redirect(url_for("client_page", client_id=client.id))


@bp.route("/mail/send", methods=["POST"])
@login_required
def send_message():
    """One endpoint for both composing and replying.

    A reply is just a send that carries `thread_id`; splitting them would
    duplicate the whole error path for one differing argument.
    """
    return_to = request.form.get("return_to") or url_for("communications.leads")
    thread_id = request.form.get("thread_id")
    client_id = request.form.get("client_id")

    try:
        email_service.send_email(
            current_user.company_id,
            to=request.form.get("to", ""),
            subject=request.form.get("subject", ""),
            body_text=request.form.get("body_text", ""),
            cc=request.form.get("cc"),
            thread_id=int(thread_id) if thread_id and thread_id.isdigit() else None,
            client_id=int(client_id) if client_id and client_id.isdigit() else None,
        )
    except (email_service.EmailServiceError, ProviderError) as exc:
        _flash(str(exc), "error")
        return redirect(return_to)

    _flash("Message sent.", "success")
    return redirect(return_to)


@bp.route("/mail/attachments/<int:attachment_id>")
@login_required
def download_attachment(attachment_id: int):
    """Serve a downloaded attachment.

    Tenant check walks the whole chain (attachment → message → thread →
    company) rather than trusting the id, and the file is served from the
    company's own directory — see storage/attachment_storage.py.
    """
    from flask import send_file

    from communications.models import EmailAttachment
    from communications.storage import attachment_storage

    attachment = (
        EmailAttachment.query.join(EmailMessage).join(EmailThread)
        .filter(
            EmailAttachment.id == attachment_id,
            EmailThread.company_id == current_user.company_id,
        )
        .first()
    )
    if attachment is None or not attachment.stored_filename:
        abort(404)
    path = attachment_storage.path_for(current_user.company_id, attachment.stored_filename)
    if path is None:
        abort(404)
    return send_file(
        path,
        as_attachment=True,
        download_name=attachment.filename or "attachment",
        # Never let the browser render a synced attachment inline: it's
        # untrusted content from an arbitrary sender, and an HTML
        # attachment rendered on our origin is stored XSS.
        mimetype="application/octet-stream",
    )


def register(app) -> None:
    """Attach the module to a Flask app.

    Also exposes csrf_token() to Jinja — module templates call it in every
    form, and a template global beats threading it through each
    render_template call.
    """
    app.jinja_env.globals.setdefault("csrf_token", csrf_token)
    app.register_blueprint(bp)
