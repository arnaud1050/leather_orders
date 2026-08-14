"""
The blueprint: companies, the people in them, and impersonation.

`time_zones` is handed in at registration rather than imported, for the
same reason `documents.routes.register` takes `resolve_order` — `app.py`
imports and registers this blueprint, so importing `app.TIME_ZONES` back
out of it would be circular.

No CSRF layer, matching every other mutating route in `app.py` and in
`documents/`: the app-wide `SESSION_COOKIE_SAMESITE=Lax` is what stops a
forged cross-site POST reaching any of them. Communications has tokens
because it sends mail and disconnects Google accounts; nothing here is
outward-facing in that way.
"""

from functools import wraps

from flask import (
    Blueprint, abort, redirect, render_template, request, session, url_for,
)
from flask_login import current_user, login_required, logout_user

from admin import services
from models import DEFAULT_TIMEZONE, User, db

bp = Blueprint("admin", __name__, url_prefix="/admin", template_folder="templates")

_time_zones: list[tuple[str, str]] = []


def register(app, *, time_zones) -> None:
    global _time_zones
    _time_zones = time_zones
    app.register_blueprint(bp)


def platform_admin_required(view):
    """403 rather than a redirect to the login page.

    A redirect would tell a signed-in tenant user that /admin exists and
    that they're merely at the wrong door. 403 says the door isn't theirs
    and stops there. The nav link doesn't render for them either, so in
    practice this only fires on a typed URL or a stale bookmark — which is
    exactly the case worth being blunt about.
    """
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if services.acting_platform_admin() is None:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def _flash(message: str) -> None:
    """One-shot message for the next render.

    Session key owned by this blueprint, same arrangement as
    `documents.routes._flash`: the app has no flash-message convention and
    this isn't the change that should introduce one app-wide.
    """
    session["admin_notice"] = message


@bp.app_context_processor
def _inject_admin_context():
    """What `base.html` needs on *every* page, not just this blueprint's.

    Two callables rather than two values, matching inventory's badge
    processor: the queries only run on a template that actually renders
    them, and both stay quiet when there's no signed-in user (the login
    page extends `base.html` too).
    """
    def is_platform_admin() -> bool:
        return services.acting_platform_admin() is not None

    def impersonating_as():
        """The tenant user being impersonated, or None — the banner's cue."""
        if not current_user.is_authenticated:
            return None
        return current_user if session.get(services.IMPERSONATOR_KEY) else None

    def platform_announcement():
        """The installation-wide banner text, or None.

        Deliberately **not** gated on `current_user.is_authenticated` —
        unlike the two callables above, a maintenance notice is most
        useful to someone about to sign in, so it has to render on
        `/login`, `/privacy` and `/terms` too. See `active_announcement()`
        for why this stays a plain read.
        """
        return services.active_announcement()

    return {
        "is_platform_admin": is_platform_admin,
        "impersonating_as": impersonating_as,
        "platform_announcement": platform_announcement,
    }


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------

@bp.route("/")
@platform_admin_required
def index():
    """`/admin` on its own — a bookmark, a typed URL — lands on Companies.

    A separate endpoint rather than a second `@bp.route` on `companies()`:
    two rules sharing one endpoint makes `url_for('admin.companies')`
    ambiguous about which path it builds, and everything in this blueprint
    (and `app.py`'s landing-page/redirect logic) already calls `url_for`
    by that name. Keeping `index` as a plain redirect means the endpoint
    stays pinned to the one real path, `/admin/companies`.
    """
    return redirect(url_for("admin.companies"))


@bp.route("/companies")
@platform_admin_required
def companies():
    return render_template(
        "admin_companies.html",
        companies=services.companies(),
        user_counts=services.user_counts(),
        time_zones=_time_zones,
        default_timezone=DEFAULT_TIMEZONE,
        notice=session.pop("admin_notice", None),
        min_password_length=services.MIN_PASSWORD_LENGTH,
        section="companies",
        active_view="admin",
    )


@bp.route("/platform-admins")
@platform_admin_required
def platform_admins():
    return render_template(
        "admin_users.html",
        platform_admins=services.platform_admins(),
        notice=session.pop("admin_notice", None),
        min_password_length=services.MIN_PASSWORD_LENGTH,
        section="admins",
        active_view="admin",
    )


@bp.route("/companies", methods=["POST"])
@platform_admin_required
def create_company():
    company, error = services.create_company(
        request.form.get("name", ""),
        request.form.get("timezone", DEFAULT_TIMEZONE),
        request.form.get("admin_email", ""),
        request.form.get("admin_password", ""),
        request.form.get("admin_full_name", ""),
    )
    if error is not None:
        _flash(error)
        return redirect(url_for("admin.companies"))
    # Straight to the new tenant's page: the next thing anyone wants after
    # creating a studio is to look at it or add a second user to it.
    _flash(f"{company.name} created.")
    return redirect(url_for("admin.company", company_id=company.id))


def _company_or_404(company_id: int):
    company = services.company_or_none(company_id)
    if company is None:
        abort(404)
    return company


@bp.route("/companies/<int:company_id>")
@platform_admin_required
def company(company_id: int):
    row = _company_or_404(company_id)
    # No `section`/`_admin_nav.html` here on purpose — this is a single
    # record's detail page, and the roster-level nav belongs on the roster,
    # not on one row's page (see _admin_nav.html's docstring, and compare
    # client_page.html, which likewise doesn't include _clients_nav.html).
    return render_template(
        "admin_company.html",
        company=row,
        users=services.users_of(row.id),
        time_zones=_time_zones,
        notice=session.pop("admin_notice", None),
        min_password_length=services.MIN_PASSWORD_LENGTH,
        active_view="admin",
    )


@bp.route("/companies/<int:company_id>", methods=["POST"])
@platform_admin_required
def update_company(company_id: int):
    row = _company_or_404(company_id)
    error = services.update_company(
        row, request.form.get("name", ""),
        request.form.get("timezone", row.timezone),
    )
    _flash(error or "Company saved.")
    return redirect(url_for("admin.company", company_id=row.id))


@bp.route("/companies/<int:company_id>/active", methods=["POST"])
@platform_admin_required
def set_company_active(company_id: int):
    row = _company_or_404(company_id)
    active = request.form.get("active") == "1"
    error = services.set_company_active(row, active)
    _flash(error or (f"{row.name} reactivated." if active
                     else f"{row.name} deactivated — nobody there can sign in."))
    return redirect(url_for("admin.company", company_id=row.id))


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def _user_or_404(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    return user


def _back_to(user: User) -> str:
    """The page a change to this user should return to.

    Their company's page for a tenant user; the Admin Users page for staff.
    Staff have no `company_id`, so the obvious
    `url_for(..., company_id=user.company_id)` would raise a BuildError
    rather than 404 — worth a helper, since four routes take either kind
    of user.
    """
    if user.company_id is None:
        return url_for("admin.platform_admins")
    return url_for("admin.company", company_id=user.company_id)


@bp.route("/companies/<int:company_id>/users", methods=["POST"])
@platform_admin_required
def add_user(company_id: int):
    row = _company_or_404(company_id)
    error = services.add_user(
        row,
        request.form.get("email", ""),
        request.form.get("password", ""),
        request.form.get("full_name", ""),
    )
    _flash(error or "User added. Give them their password directly — "
                    "the app doesn't send one.")
    return redirect(url_for("admin.company", company_id=row.id))


@bp.route("/users/<int:user_id>/password", methods=["POST"])
@platform_admin_required
def reset_password(user_id: int):
    user = _user_or_404(user_id)
    error = services.reset_password(user, request.form.get("password", ""))
    _flash(error or f"Password reset for {user.display_name}.")
    return redirect(_back_to(user))


@bp.route("/users/<int:user_id>/active", methods=["POST"])
@platform_admin_required
def set_user_active(user_id: int):
    user = _user_or_404(user_id)
    active = request.form.get("active") == "1"
    error = services.set_user_active(user, active)
    _flash(error or (f"{user.display_name} reactivated." if active
                     else f"{user.display_name} deactivated."))
    return redirect(_back_to(user))


@bp.route("/platform-admins", methods=["POST"])
@platform_admin_required
def add_platform_admin():
    error = services.add_platform_admin(
        request.form.get("email", ""),
        request.form.get("password", ""),
        request.form.get("full_name", ""),
    )
    _flash(error or "Platform admin added. Give them their password directly — "
                    "the app doesn't send one.")
    return redirect(url_for("admin.platform_admins"))


# ---------------------------------------------------------------------------
# Platform settings
# ---------------------------------------------------------------------------

@bp.route("/settings")
@platform_admin_required
def settings():
    return render_template(
        "admin_settings.html",
        settings=services.get_platform_settings(),
        notice=session.pop("admin_notice", None),
        section="settings",
        active_view="admin",
    )


@bp.route("/settings/announcement", methods=["POST"])
@platform_admin_required
def update_announcement():
    error = services.set_announcement(
        request.form.get("message", ""),
        request.form.get("active") == "1",
    )
    _flash(error or "Announcement saved.")
    return redirect(url_for("admin.settings"))


# ---------------------------------------------------------------------------
# Impersonation
# ---------------------------------------------------------------------------

@bp.route("/users/<int:user_id>/impersonate", methods=["POST"])
@platform_admin_required
def impersonate(user_id: int):
    user = _user_or_404(user_id)
    company_id = user.company_id
    error = services.start_impersonating(user)
    if error is not None:
        _flash(error)
        return redirect(url_for("admin.company", company_id=company_id)
                        if company_id is not None
                        else url_for("admin.platform_admins"))
    # The timeline, not /admin — which is now 403 for this session anyway.
    # Landing on the tenant's own default page is the whole point: you're
    # seeing what they see, from the first screen on.
    return redirect(url_for("timeline_view"))


@bp.route("/stop-impersonating", methods=["POST"])
@login_required
def stop_impersonating():
    """Deliberately *not* `@platform_admin_required`.

    That guard returns None for an impersonated session by design, so
    requiring it here would make the exit button the one thing you can't
    press. `stop_impersonating()` does its own check against the session
    key instead.
    """
    if session.get(services.IMPERSONATOR_KEY) is None:
        abort(403)
    error = services.stop_impersonating()
    if error is not None:
        # Their admin account went away mid-session. Signing out is the
        # only honest end state — staying signed in as the tenant user
        # would be a quiet privilege change nobody asked for.
        logout_user()
        return redirect(url_for("login"))
    return redirect(url_for("admin.companies"))
