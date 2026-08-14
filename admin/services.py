"""
The rules behind the admin pages, kept out of the route bodies.

Everything here either answers a question the templates ask or performs a
change and returns an error string. Errors are returned rather than raised
because every one of them is a thing a person can plausibly type — a
duplicate address, a short password, the last platform admin demoting
themselves — and each belongs back on the form that caused it, not on a
500 page.
"""

from flask import session
from flask_login import current_user, login_user

from models import Company, User, db, normalise_email

from admin.models import PlatformSettings

# Matches MIN_PASSWORD_LENGTH in app.py. Duplicated rather than imported
# because importing app.py from here would be circular (app.py registers
# this blueprint), and a hook for one integer is more machinery than the
# constant is worth. tests/test_admin.py asserts the two agree.
MIN_PASSWORD_LENGTH = 8

# The session key holding the *real* platform admin's user id while they're
# impersonating someone. Its presence is the whole definition of "currently
# impersonating" — there is no second flag that could disagree with it.
IMPERSONATOR_KEY = "impersonator_id"


# ---------------------------------------------------------------------------
# Who is allowed to be here
# ---------------------------------------------------------------------------

def acting_platform_admin() -> User | None:
    """The signed-in platform admin, or None.

    Returns None while impersonating, and that's the load-bearing part:
    it means /admin is unreachable from inside a tenant session, so a
    platform admin who forgot they were impersonating cannot provision a
    company or reset a password "as" somebody else. The way back is the
    banner's exit button, which is deliberately the one route that looks
    at `IMPERSONATOR_KEY` instead of calling this.
    """
    if not current_user.is_authenticated:
        return None
    if session.get(IMPERSONATOR_KEY) is not None:
        return None
    return current_user if current_user.is_platform_admin else None


def impersonator() -> User | None:
    """The real platform admin behind an impersonated session, or None."""
    admin_id = session.get(IMPERSONATOR_KEY)
    return db.session.get(User, admin_id) if admin_id is not None else None


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def companies() -> list[Company]:
    """Every tenant, active first, then by name.

    Active-first rather than purely alphabetical because a deactivated
    company is an archive entry: it should still be findable, but it
    shouldn't sit between two live studios in the list you scan daily.
    """
    return (
        Company.query.order_by(Company.is_active.desc(), Company.name).all()
    )


def company_or_none(company_id: int) -> Company | None:
    return db.session.get(Company, company_id)


def users_of(company_id: int) -> list[User]:
    """A company's people, active first, then by the name they're shown by."""
    return (
        User.query.filter_by(company_id=company_id)
        .order_by(User.is_active.desc(), User.full_name, User.email)
        .all()
    )


def platform_admins() -> list[User]:
    """Platform staff — the company-less accounts that can reach /admin.

    Queried on `company_id IS NULL` rather than on the flag, because that
    null is what the rest of the app keys off (`is_tenant_user`, the route
    guard, the nav). If the two ever disagreed, this list should show the
    row that's actually behaving like staff, not the one that claims to be.
    """
    return (
        User.query.filter(User.company_id.is_(None))
        .order_by(User.is_active.desc(), User.full_name, User.email)
        .all()
    )


def user_counts() -> dict[int, int]:
    """Active users per company id, for the list page.

    One grouped query rather than a count per row: this page is the one
    place that renders every tenant at once, and it's the obvious spot for
    an N+1 to appear later and never be noticed at this scale.
    """
    rows = (
        db.session.query(User.company_id, db.func.count(User.id))
        .filter(User.is_active.is_(True))
        .group_by(User.company_id)
        .all()
    )
    return {company_id: count for company_id, count in rows}


# ---------------------------------------------------------------------------
# Validation shared by every form that takes an address and a password
# ---------------------------------------------------------------------------

def _email_error(email: str, *, exclude_user_id: int | None = None) -> str | None:
    """Reject an address that's empty, obviously not an address, or taken.

    The shape check is deliberately one `@` with something either side and
    nothing else — an address is validated by sending mail to it, and a
    stricter regex here would only reject real addresses while still
    accepting fake ones.
    """
    if not email:
        return "An email address is required."
    local, _, domain = email.partition("@")
    if not local or "." not in domain or domain.startswith(".") or domain.endswith("."):
        return f"{email!r} doesn't look like an email address."
    existing = User.query.filter_by(email=email).first()
    if existing is not None and existing.id != exclude_user_id:
        # Named rather than a bare "taken": across tenants the platform
        # admin can't see who has it, and being told which company holds
        # the address is the difference between a fixable and a baffling
        # error. A staff account has no company to name, so it says so.
        where = (f"in {existing.company.name}" if existing.company is not None
                 else "on the platform admin team")
        return f"{email} already belongs to a user {where}."
    return None


def _password_error(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Choose a password of at least {MIN_PASSWORD_LENGTH} characters."
    return None


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def create_company(
    name: str, timezone: str, admin_email: str,
    admin_password: str, admin_full_name: str,
) -> tuple[Company | None, str | None]:
    """Provision a tenant. Returns (company, error) — exactly one is set.

    Delegates the actual creation to `models.create_company`, the same
    function `seed_if_empty()` uses, so a company made here is
    indistinguishable from the one an empty database bootstraps itself
    with. All this layer adds is the validation a web form needs and the
    first company's founding admin is never given.
    """
    from models import create_company as provision

    name = (name or "").strip()
    email = normalise_email(admin_email)

    error = (
        "A company name is required." if not name
        else _email_error(email) or _password_error(admin_password)
    )
    if error is not None:
        return None, error

    company, _admin = provision(
        name, email, admin_password,
        timezone=timezone, admin_full_name=admin_full_name,
    )
    db.session.commit()
    return company, None


def update_company(company: Company, name: str, timezone: str) -> str | None:
    name = (name or "").strip()
    if not name:
        return "A company name is required."
    company.name = name
    company.timezone = timezone
    db.session.commit()
    return None


def set_company_active(company: Company, active: bool) -> str | None:
    """Switch a tenant on or off. Never deletes — see Company.is_active.

    No "not your own company" guard, and none is needed: a platform admin
    belongs to no company (see `User`), so there is no company whose
    deactivation could lock them out. That guard existed only while staff
    lived inside a tenant.
    """
    company.is_active = active
    db.session.commit()
    return None


def add_user(
    company: Company, email: str, password: str, full_name: str,
) -> str | None:
    """Add a person to a company with an initial password.

    The platform admin sets that password and hands it over out of band.
    There's no invitation email because the app has no address of its own
    to send from — the Gmail accounts under Settings → Email/Calendar are
    the studio's client mail, not the platform's (see N2 in REQUIREMENTS.md).
    """
    email = normalise_email(email)
    error = _email_error(email) or _password_error(password)
    if error is not None:
        return error

    user = User(
        company_id=company.id,
        email=email,
        full_name=(full_name or "").strip() or None,
    )
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    return None


def reset_password(user: User, password: str) -> str | None:
    """Set someone's password without knowing the old one.

    Deliberately unlike `/settings/account`, which demands the current
    password: that check exists to stop an unattended browser becoming a
    permanent one, and it can't apply to an administrator acting on
    somebody else's account. This is the app's only password recovery
    path — there is still no reset-by-email.
    """
    error = _password_error(password)
    if error is not None:
        return error
    user.set_password(password)
    db.session.commit()
    return None


def set_user_active(user: User, active: bool) -> str | None:
    """Switch a login off or back on — tenant user or platform staff alike.

    One refusal, and it's load-bearing twice over. You may not deactivate
    your own account, because the next request would bounce you to the
    login page with no signed-in way back.

    That single guard is also the whole of "the platform can never be left
    without an admin", which is worth spelling out because a separate
    last-active-admin check *looks* necessary and is in fact unreachable:
    whoever is calling this got past `platform_admin_required`, so they are
    themselves an active platform admin, and the only account they can't
    switch off is their own. Every other deactivation therefore leaves at
    least one — them. Deactivating is also the only way to remove staff
    access at all, since platform admins are created as staff rather than
    promoted (see `add_platform_admin`).
    """
    if not active and user.id == current_user.id:
        return "You can't deactivate your own account."
    user.is_active = active
    db.session.commit()
    return None


def add_platform_admin(email: str, password: str, full_name: str) -> str | None:
    """Add a member of platform staff — a user with no company.

    There is deliberately no way to *promote* a tenant user into this role,
    and that absence is the point. A platform admin who also belonged to a
    studio would be exactly the thing this model exists to prevent: the
    person who administers the installation quietly sitting inside one
    customer's company, with their orders and their clients. Staff are
    created as staff or not at all.

    The counterpart — demoting a platform admin — is missing for the same
    reason: it would leave a user with no company *and* no rights, an
    account that can't do anything and can't be reached. Deactivating is
    what "this person no longer works here" means (see `set_user_active`).
    """
    email = normalise_email(email)
    error = _email_error(email) or _password_error(password)
    if error is not None:
        return error

    admin = User(
        company_id=None,
        email=email,
        full_name=(full_name or "").strip() or None,
        is_platform_admin=True,
    )
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()
    return None


# ---------------------------------------------------------------------------
# Impersonation
# ---------------------------------------------------------------------------

def start_impersonating(user: User) -> str | None:
    """Enter a tenant as one of its users.

    Safe by construction rather than by care: this swaps who
    `current_user` is and changes nothing else, so every page renders
    through the same `current_user.company_id` filter it always does.
    There is no "view as" mode with its own query paths to get wrong.

    Neither a deactivated user nor a deactivated company can be entered,
    and that's `load_user` in app.py talking rather than a policy invented
    here: it drops any session whose user or company is switched off, so
    an impersonation into one would survive exactly one redirect before
    dumping the platform admin at the login page. Refusing up front, with
    a sentence saying what to do about it, beats a mysterious sign-out.
    """
    if user.id == current_user.id:
        return "You're already signed in as that user."
    if user.company is None:
        # Another staff account. There'd be nothing to see — they have no
        # studio either — and it would only muddy who did what.
        return "That's a platform admin, not a tenant user."
    if not user.is_active:
        return "That account is deactivated. Reactivate it first."
    if not user.company.is_active:
        return f"{user.company.name} is deactivated. Reactivate it first."

    admin_id = current_user.id
    if not login_user(user):
        return "Couldn't sign in as that user."
    session[IMPERSONATOR_KEY] = admin_id
    return None


def stop_impersonating() -> str | None:
    """Return to the platform admin who started it.

    Reads `IMPERSONATOR_KEY` rather than asking `acting_platform_admin()`,
    which by definition returns None right now — this is the one route
    that has to work from inside an impersonated session.
    """
    admin = impersonator()
    session.pop(IMPERSONATOR_KEY, None)
    if admin is None or not admin.is_active:
        # The real admin was deactivated while impersonating. Nothing sane
        # to return to, so let the caller sign the session out entirely
        # rather than leave it stranded as the tenant user.
        return "Your platform admin account is no longer available."
    login_user(admin)
    return None


# ---------------------------------------------------------------------------
# Platform settings
# ---------------------------------------------------------------------------

def get_platform_settings() -> PlatformSettings:
    """The one row of installation-wide config, creating it on first read.

    Always `id=1` — see PlatformSettings' docstring for why a singleton
    rather than a `company_id`-scoped table.
    """
    row = db.session.get(PlatformSettings, 1)
    if row is None:
        row = PlatformSettings(id=1)
        db.session.add(row)
        db.session.commit()
    return row


def active_announcement() -> str | None:
    """The banner text to show right now, or None.

    Called on *every* page render (see the context processor in
    routes.py), so it stays a plain read with no side effect — creating
    the settings row here on a cache-miss would mean the first page view
    on a fresh install writes to the database before anyone has touched
    a form.
    """
    settings = db.session.get(PlatformSettings, 1)
    if settings is None or not settings.is_active:
        return None
    return settings.announcement or None


def set_announcement(message: str, active: bool) -> str | None:
    """Save the platform announcement. Returns an error, or None.

    Refuses "on" with a blank message — a banner with nothing in it isn't
    a notice, it's an empty coloured bar across the top of every page on
    the installation. Saving a blank message while *off* is allowed: it's
    how a draft gets cleared without also having to remember to switch
    the banner off in the same click.
    """
    message = (message or "").strip()
    if active and not message:
        return "Write a message before turning the announcement on."
    settings = get_platform_settings()
    settings.announcement = message or None
    settings.is_active = active
    db.session.commit()
    return None
