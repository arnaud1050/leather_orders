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

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from flask import (
    Blueprint, abort, current_app, redirect, render_template, request, session, url_for,
)
from flask_login import current_user, login_required

from models import Client, DEFAULT_TIMEZONE, db

from communications import config, jobs
from communications.models import (
    AUDIT_EVENT_LABELS, FIELD_TARGET_LABELS, RULE_CONVERT, RULE_HIDE,
    RULE_LABELS, EmailMessage, EmailSyncSettings, EmailThread,
)
from communications.oauth import google_oauth
from communications.providers.base import ProviderError
from communications.providers.registry import PROVIDER_LABELS
from communications.security import csrf_token, validate_csrf
from communications.services import (
    account_service, audit, calendar_service, email_service, sender_rules,
)

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


def take_notice():
    """Public form of `_take_notice`, for a host-app page that renders a
    result from this module — app.py's calendar view, whose event forms post
    into this blueprint. Part of the module's API surface, like the services:
    the alternative was app.py reaching for the session key by name.
    """
    return _take_notice()


# ---------------------------------------------------------------------------
# Integrations settings page. Sits under /settings as one of several
# categories alongside General, Invoicing, Orders and Clients, following
# the same .settings-nav pattern (see CLAUDE.md, "Settings").
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
        hide_rules=sender_rules.rules_by_action(company_id, RULE_HIDE),
        convert_rules=sender_rules.rules_by_action(company_id, RULE_CONVERT),
        rule_labels=RULE_LABELS,
        field_targets=FIELD_TARGET_LABELS,
        has_calendar=calendar_service.has_calendar(company_id),
        scope_summary=account_service.scope_summary,
        available_providers=PROVIDER_LABELS,
        configuration_problem=config.configuration_problem(),
        using_derived_key=_using_derived_key(),
        scheduler_enabled=jobs.scheduler_enabled(),
        audit_entries=audit.recent(company_id, limit=10),
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
    """Save one section of the sync settings.

    Mail and calendar are two forms on the page, so each says which section
    it is. That's not decoration: an unticked checkbox sends **nothing**, so
    a calendar form processed wholesale would read every mail checkbox as
    "off" and quietly turn them all off. Same rule as the client modal's
    address field — a form that doesn't render a field means "not shown",
    never "cleared" — just enforced by an explicit marker rather than by
    `in request.form`, which checkboxes can't support.

    No marker means "everything", so an older form (or a test) that posts
    the whole lot still works.
    """
    section = request.form.get("section") or "all"
    settings = EmailSyncSettings.for_company(current_user.company_id)

    # Clamped rather than validated-and-rejected: these are dials, not
    # data. A 1-minute frequency is a good way to get rate-limited by
    # Google, and a 3650-day initial sync is a good way to hang the first
    # run — so the bounds are enforced here rather than trusted from a
    # number input that anyone can edit.
    if section in ("all", "email"):
        settings.sync_enabled = request.form.get("sync_enabled") == "on"
        settings.sync_sent_mail = request.form.get("sync_sent_mail") == "on"
        settings.sync_attachments = request.form.get("sync_attachments") == "on"
        settings.keep_unmatched = request.form.get("keep_unmatched") == "on"
        settings.sync_frequency = _clamp(request.form.get("sync_frequency"), 5, 1440, 15)
        settings.initial_sync_days = _clamp(
            request.form.get("initial_sync_days"), 1, 730, 90,
        )

    if section in ("all", "calendar"):
        settings.calendar_frequency = _clamp(
            request.form.get("calendar_frequency"), 5, 1440, 30,
        )

    db.session.commit()
    _flash("Sync settings saved.", "success")
    return redirect(url_for("communications.integrations"))


@bp.route("/integrations/calendar/sync", methods=["POST"])
@login_required
def sync_calendar_now():
    """Refresh calendars only — the button on the calendar page.

    Separate from the combined "Sync now" on the integrations page because
    of where it's pressed: someone on the month grid waiting for an
    appointment to appear doesn't want to wait on a mailbox as well, and a
    button labelled for the calendar should do what it says.
    """
    results = calendar_service.sync_now(current_user.company_id)
    if not results:
        _flash("No calendar is connected.", "error")
    elif all(result.ok for result in results):
        _flash(" ".join(result.summary() for result in results), "success")
    else:
        _flash(" ".join(r.summary() for r in results if not r.ok), "error")
    return redirect(request.form.get("return_to") or url_for("calendar_view"))


@bp.route("/integrations/rules", methods=["POST"])
@login_required
def add_sender_rule():
    """Add an "always hide" / "always convert" rule for a sender."""
    try:
        rule = sender_rules.add_rule(
            current_user.company_id,
            request.form.get("pattern", ""),
            request.form.get("action", ""),
            request.form.get("note", ""),
        )
    except sender_rules.SenderRuleError as exc:
        _flash(str(exc), "error")
    else:
        _flash(f"Mail from {rule.pattern} will now {rule.action_label.lower()}.", "success")
    return redirect(url_for("communications.integrations"))


@bp.route("/integrations/rules/<int:rule_id>/delete", methods=["POST"])
@login_required
def delete_sender_rule(rule_id: int):
    """Remove a rule. Conversations it already handled are left alone."""
    try:
        rule = sender_rules.delete_rule(current_user.company_id, rule_id)
    except sender_rules.SenderRuleError as exc:
        _flash(str(exc), "error")
    else:
        _flash(f"Rule for {rule.pattern} removed.", "success")
    return redirect(url_for("communications.integrations"))


@bp.route("/integrations/rules/<int:rule_id>/fields", methods=["POST"])
@login_required
def add_rule_field(rule_id: int):
    """Map a label in the form email to a field on the client."""
    try:
        field = sender_rules.add_field(
            current_user.company_id, rule_id,
            request.form.get("label", ""), request.form.get("target", ""),
        )
    except sender_rules.SenderRuleError as exc:
        _flash(str(exc), "error")
    else:
        _flash(f'"{field.label}" now fills {field.target_label.lower()}.', "success")
    return redirect(url_for("communications.integrations"))


@bp.route("/integrations/rules/fields/<int:field_id>/delete", methods=["POST"])
@login_required
def delete_rule_field(field_id: int):
    try:
        field = sender_rules.delete_field(current_user.company_id, field_id)
    except sender_rules.SenderRuleError as exc:
        _flash(str(exc), "error")
    else:
        _flash(f'"{field.label}" is no longer mapped.', "success")
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
    """Fetch mail for this company, now.

    Mail only. Every button that posts here sits under a mail heading — the
    Email sync section, and the lead inbox — and a button that quietly did
    more than its label says is worse than one that does less. The combined
    one is `sync_all_now` below.
    """
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


@bp.route("/integrations/sync-all", methods=["POST"])
@login_required
def sync_all_now():
    """Fetch mail *and* calendars — the button beside the connected accounts.

    It sits at the top of the page, above the split into Email sync and
    Calendar sync, because that's where "refresh everything I've connected"
    belongs. The per-section buttons below stay narrow so each does exactly
    what its heading says.

    Worth knowing: with `RUN_SCHEDULER` unset (the default) these buttons
    are the *only* thing that ever syncs anything.
    """
    results = email_service.sync_now(current_user.company_id)
    calendar_results = calendar_service.sync_now(current_user.company_id)

    parts = [r.summary() for r in results + calendar_results if not r.ok]
    if parts:
        _flash(" ".join(parts), "error")
    elif results or calendar_results:
        _flash(
            " ".join(r.summary() for r in results + calendar_results), "success",
        )
    else:
        _flash("Nothing is connected and enabled for syncing.", "error")
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
    # Reading it is what stops it being new. Only ever the first open, and
    # only this conversation — see mark_thread_opened.
    email_service.mark_thread_opened(current_user.company_id, thread_id)
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

    **Opening this page changes nothing.** The badge counts leads still
    awaiting triage, and reading the list isn't triage — it clears when a
    lead is converted, hidden or trashed. Each row keeps its own "New" pill
    until that conversation itself is opened.
    """
    company_id = current_user.company_id
    showing_dismissed = request.args.get("show") == "dismissed"

    dismissed = email_service.dismissed_lead_threads(company_id)
    threads = dismissed if showing_dismissed else email_service.lead_threads(company_id)

    return render_template(
        "leads.html",
        threads=threads,
        showing_dismissed=showing_dismissed,
        dismissed_count=len(dismissed),
        scheduler_enabled=jobs.scheduler_enabled(),
        has_accounts=bool(account_service.accounts_for(company_id)),
        notice=_take_notice(),
        active_view="clients",
    )


@bp.route("/mail/threads/<int:thread_id>/dismiss", methods=["POST"])
@login_required
def dismiss_thread(thread_id: int):
    """Hide a lead. Reversible, and never touches the mailbox."""
    return _thread_action(thread_id, email_service.dismiss_thread, "Conversation hidden.")


@bp.route("/mail/threads/<int:thread_id>/restore", methods=["POST"])
@login_required
def restore_thread(thread_id: int):
    return _thread_action(thread_id, email_service.restore_thread, "Conversation restored.")


@bp.route("/mail/threads/<int:thread_id>/trash", methods=["POST"])
@login_required
def trash_thread(thread_id: int):
    """Move a conversation to Gmail's Trash — recoverable there for 30 days.

    Confirmed in the template before it gets here, since unlike Dismiss this
    one reaches out and changes the studio's real mailbox.
    """
    return _thread_action(
        thread_id, email_service.trash_thread,
        "Conversation moved to Gmail's Trash — recoverable there for 30 days.",
    )


def _thread_action(thread_id: int, action, success_message: str):
    """Shared plumbing for dismiss/restore/trash.

    All three are: run a service call, report what happened, go back where
    you were. Writing that out three times would just be three chances to
    forget the error path.
    """
    return_to = request.form.get("return_to") or url_for("communications.leads")
    try:
        action(current_user.company_id, thread_id)
    except (email_service.EmailServiceError, ProviderError) as exc:
        _flash(str(exc), "error")
        return redirect(return_to)
    _flash(success_message, "success")
    return redirect(return_to)


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


# ---------------------------------------------------------------------------
# Calendar events. The month view itself is app.py's (orders used to share
# that grid), but writing an event is this module's job — it goes out to a
# provider, so it belongs behind the service and inside this blueprint's CSRF.
#
# Both routes take a wall-clock date/time from the form and convert it with
# _to_utc: the studio types 2pm meaning 2pm where it is, and everything below
# this layer speaks naive UTC.
# ---------------------------------------------------------------------------

@bp.route("/calendar/events/new", methods=["POST"])
@login_required
def create_calendar_event():
    return_to = request.form.get("return_to") or url_for("calendar_view")
    try:
        start, end, all_day = _event_window(request.form)
    except ValueError as exc:
        _flash(str(exc), "error")
        return redirect(return_to)

    try:
        calendar_service.create_event(
            current_user.company_id,
            title=request.form.get("title", "").strip() or "(untitled event)",
            start=start, end=end, all_day=all_day,
            description=request.form.get("description", "").strip() or None,
            location=request.form.get("location", "").strip() or None,
            attendees=_attendees(request.form.get("attendees")),
            client_id=_client_id(request.form.get("client_id")),
        )
    except (calendar_service.CalendarServiceError, ProviderError) as exc:
        _flash(str(exc), "error")
        return redirect(return_to)
    _flash("Event added to your Google Calendar.", "success")
    return redirect(return_to)


@bp.route("/calendar/events/<int:event_id>", methods=["POST"])
@login_required
def update_calendar_event(event_id: int):
    """Edit a mirrored event.

    Attendees are deliberately not editable here: Google's PATCH replaces the
    whole attendee list, so sending one built from a form that never loaded the
    existing guests would silently uninvite them.
    """
    return_to = request.form.get("return_to") or url_for("calendar_view")
    try:
        start, end, all_day = _event_window(request.form)
    except ValueError as exc:
        _flash(str(exc), "error")
        return redirect(return_to)

    try:
        calendar_service.update_event(
            current_user.company_id, event_id,
            title=request.form.get("title", "").strip() or "(untitled event)",
            start=start, end=end, all_day=all_day,
            description=request.form.get("description", "").strip() or None,
            location=request.form.get("location", "").strip() or None,
            client_id=_client_id(request.form.get("client_id")),
        )
    except (calendar_service.CalendarServiceError, ProviderError) as exc:
        _flash(str(exc), "error")
        return redirect(return_to)
    _flash("Event updated.", "success")
    return redirect(return_to)


def _event_window(form) -> tuple[datetime, datetime, bool]:
    """(start, end, all_day) in naive UTC, from the four date/time fields.

    Raises ValueError with something worth showing the user. Validating here
    rather than trusting the browser's date/time inputs, which are trivially
    bypassed and blank on older ones.
    """
    all_day = bool(form.get("all_day"))
    start_date = _parse_date(form.get("start_date"))
    if start_date is None:
        raise ValueError("An event needs a start date.")
    end_date = _parse_date(form.get("end_date")) or start_date

    if all_day:
        # Midnight local, and the end is the last day the event covers — the
        # provider converts to Google's exclusive end date.
        start = datetime.combine(start_date, time.min)
        end = datetime.combine(end_date, time.min)
    else:
        start_time = _parse_time(form.get("start_time"))
        end_time = _parse_time(form.get("end_time"))
        if start_time is None or end_time is None:
            raise ValueError("A timed event needs both a start and an end time.")
        start = datetime.combine(start_date, start_time)
        end = datetime.combine(end_date, end_time)

    if end < start:
        raise ValueError("The event ends before it starts.")
    return _to_utc(start), _to_utc(end), all_day


def _parse_date(value):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_time(value):
    raw = (value or "").strip()
    for fmt in ("%H:%M", "%H:%M:%S"):  # some browsers include seconds
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


def _attendees(raw):
    """Comma-separated invitees, or None. Whatever is typed goes to Google as
    entered — it validates addresses far better than a regex here would."""
    addresses = [part.strip() for part in (raw or "").split(",") if part.strip()]
    return addresses or None


def _client_id(raw):
    """The optional client link, checked against the current tenant.

    Blank means "no client", which is a real value — clearing the link has to
    be possible. An id belonging to another company is treated the same as
    blank rather than trusted: it arrives in a form field anyone can edit.
    """
    try:
        client_id = int(raw) if (raw or "").strip() else None
    except ValueError:
        return None
    if client_id is None:
        return None
    exists = Client.query.filter_by(
        id=client_id, company_id=current_user.company_id,
    ).first()
    return client_id if exists else None


@bp.app_context_processor
def _inject_nav_badges():
    """Make the two nav badges available to every template.

    `pending_lead_count` — enquiries still waiting to be dealt with.
    `new_client_count` — clients the app created by itself, unseen so far.
    `client_mail_count` — unread mail from clients already on file.
    `integration_alert_count` — integrations whose last sync failed.

    An `app_context_processor` (not a plain blueprint one) because both badges
    live in base.html's top nav, which every page in the app extends —
    including pages this blueprint doesn't own.

    Each returns a **callable**, not a number, so the query only runs on
    templates that actually render that badge. base.html renders both, so in
    practice it's two COUNTs per page load; making them lazy means adding a
    template that shows neither costs nothing.

    Deliberately forgiving: this runs on the login page (no user) and could
    run before the module's tables exist on a partially-migrated database.
    Neither is worth a 500 over a decoration, so both yield zero.
    """
    def _count(what: str, query):
        try:
            if not current_user.is_authenticated:
                return 0
            return query()
        except Exception:  # noqa: BLE001 — see docstring
            current_app.logger.debug("Could not compute the %s badge", what, exc_info=True)
            return 0

    def pending_lead_count() -> int:
        """How many leads nobody has acted on yet.

        Not per user, and not "since you last looked": one studio triages
        one inbox, and an enquiry stays outstanding until someone converts,
        hides or trashes it — regardless of who has glanced at the list.
        """
        return _count("pending-lead", lambda: email_service.pending_lead_count(
            current_user.company_id,
        ))

    def client_mail_count(client_id: int | None = None) -> int:
        """Unread mail from people already on file — all of them, or one.

        The optional argument is what lets the client page's Emails tab
        show the same badge narrowed to that client, without a second
        counter that could disagree with the nav.

        Not per user, like every other badge here: one studio, one inbox.
        It clears the only way it honestly can — by someone opening the
        conversation and reading it.
        """
        return _count("client-mail", lambda: email_service.unread_client_mail_count(
            current_user.company_id, client_id,
        ))

    def new_client_count() -> int:
        """Clients a sender rule created that nobody has seen yet.

        Unlike the lead badge this one *does* clear on a page view — and
        that's the right rule for it, because it isn't a to-do. Nothing is
        outstanding: the client already exists. It's a "this appeared while
        you weren't looking" notice, and looking is exactly what settles it.
        """
        return _count("new-client", lambda: sender_rules.unseen_client_count(
            current_user.company_id,
        ))

    def integration_alert_count() -> int:
        """How many connected integrations are currently failing.

        Named for integrations rather than mailboxes: the badge's promise to
        the user is "something you connected has stopped working", and a
        second provider added later should count here without the nav
        changing. Today the only integrations are Google accounts.
        """
        return _count("integration-alert", lambda: len(
            account_service.failing_accounts(current_user.company_id),
        ))

    return {
        "pending_lead_count": pending_lead_count,
        "new_client_count": new_client_count,
        "client_mail_count": client_mail_count,
        "integration_alert_count": integration_alert_count,
    }


def _local_datetime(value, fmt: str = "%b %d, %Y at %H:%M") -> str:
    """A naive-UTC timestamp rendered in the company's chosen zone.

    Every stored timestamp is naive UTC (see the models docstring), which is
    the right thing to store and the wrong thing to show — "10:31" on a
    Vancouver studio's screen has to mean 10:31 there. The zone is
    `Company.timezone`, set at /settings/general.

    The zone name is deliberately not printed alongside: there is one setting
    for the whole company, so repeating it on every line says nothing.

    Falls back to UTC when there's no user (the login page) or the stored zone
    isn't one this Python has data for — a wrong-looking time beats a 500 on a
    page that merely mentions a date.
    """
    if value is None:
        return ""
    return value.replace(tzinfo=timezone.utc).astimezone(_company_zone()).strftime(fmt)


def _company_zone():
    """The logged-in user's company zone, or UTC if it can't be resolved.

    Falls back rather than raising: this runs on the login page (no user) and
    could meet a stored zone this Python has no data for. A wrong-looking time
    beats a 500 on a page that merely mentions a date. The one place that must
    *not* be forgiving is writing a time to a provider — see `_to_utc`.
    """
    try:
        name = current_user.company.timezone if current_user.is_authenticated else None
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except Exception:  # noqa: BLE001 — see docstring
        return timezone.utc


def _to_utc(value: datetime) -> datetime:
    """A wall-clock time the user typed, as naive UTC for storage and the wire.

    The inverse of `_local_datetime`: a form says "2pm" and means 2pm where the
    studio is. Getting this wrong doesn't look wrong — it silently books the
    appointment a few hours out.
    """
    return value.replace(tzinfo=_company_zone()).astimezone(timezone.utc).replace(tzinfo=None)


def _local_time(value) -> str:
    """A naive-UTC timestamp's time-of-day, in the company's zone, as e.g.
    "12:00pm" — 12-hour, lowercase am/pm, no leading zero. Used where a
    calendar event's start/end is shown next to its own date, so only the
    time is needed (see `_local_datetime` for the zone-conversion rules).
    """
    if value is None:
        return ""
    return _local_datetime(value, "%I:%M%p").lstrip("0").lower()


def register(app) -> None:
    """Attach the module to a Flask app.

    Also exposes csrf_token() to Jinja — module templates call it in every
    form, and a template global beats threading it through each
    render_template call — plus the `local_datetime` filter its templates use
    for every time they print. Both are registered here rather than in app.py
    so the module carries what its own templates need.
    """
    app.jinja_env.globals.setdefault("csrf_token", csrf_token)
    app.jinja_env.filters.setdefault("local_datetime", _local_datetime)
    app.jinja_env.filters.setdefault("local_time", _local_time)
    app.register_blueprint(bp)
