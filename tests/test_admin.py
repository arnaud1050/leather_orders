"""
The platform admin area: provisioning, user management, impersonation,
the announcement banner.

Defends admin/REQUIREMENTS.md (PA1–PA30). The deliberate regressions this
file was checked against are noted on the tests that catch them.
"""

import pytest

from admin import services
from models import Company, User, create_company, db, normalise_email


def signed_out_client(app):
    """A second test client, deliberately *not* used as a context manager.

    `admin_client` and `logged_in` are both `with app.test_client()`, which
    preserves a request context for the life of the fixture. Opening a
    second one the same way nests two preserved contexts and Flask pops
    them in the wrong order; the resulting "Popped wrong request context"
    says nothing about the code under test.

    The other half of driving two clients at once is handled in conftest —
    see `_forget_cached_login`.
    """
    return app.test_client()


# --- PA1: only a platform admin gets in -----------------------------------

def test_an_ordinary_user_is_refused(logged_in):
    """403, not a redirect — a redirect to /login would tell a signed-in
    tenant user that /admin exists and they're merely at the wrong door."""
    assert logged_in.get("/admin/").status_code == 403


def test_a_signed_out_visitor_is_sent_to_login(app):
    response = app.test_client().get("/admin/")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_a_platform_admin_gets_in(admin_client):
    """`/admin/` on its own is a bookmark, not a page — it redirects to
    Companies, the area's default landing spot."""
    index = admin_client.get("/admin/", follow_redirects=False)
    assert index.status_code == 302
    assert index.headers["Location"].endswith("/admin/companies")
    assert admin_client.get("/admin/companies").status_code == 200


def test_platform_admins_page_is_reachable(admin_client):
    assert admin_client.get("/admin/platform-admins").status_code == 200


def test_both_list_pages_are_guarded(logged_in):
    """Split from the single page into two, so both need the same check
    `/admin/` itself already gets."""
    assert logged_in.get("/admin/companies").status_code == 403
    assert logged_in.get("/admin/platform-admins").status_code == 403
    assert logged_in.get("/admin/settings").status_code == 403


def test_every_mutating_route_is_guarded(logged_in, company, user):
    """Regression: the guard on the list page is easy to remember and the
    guard on ten POST routes is not. Each of these once had to be added
    by hand, so each is checked by hand."""
    posts = [
        "/admin/companies",
        f"/admin/companies/{company.id}",
        f"/admin/companies/{company.id}/active",
        f"/admin/companies/{company.id}/users",
        f"/admin/users/{user.id}/password",
        f"/admin/users/{user.id}/active",
        f"/admin/users/{user.id}/impersonate",
        "/admin/platform-admins",
        "/admin/settings/announcement",
    ]
    for path in posts:
        assert logged_in.post(path, data={}).status_code == 403, path


# --- PA2: the nav link is platform-admin only -----------------------------

def test_the_admin_link_is_hidden_from_a_tenant_user(logged_in):
    assert 'href="/admin/companies"' not in logged_in.get("/").get_data(as_text=True)


def test_the_admin_link_shows_for_a_platform_admin(admin_client):
    assert 'href="/admin/companies"' in admin_client.get("/").get_data(as_text=True)


def test_the_admin_sub_nav_separates_companies_and_users(admin_client):
    """The point of this change: Companies and Admin Users are two pages
    with their own links, not one page with everything on it."""
    companies_page = admin_client.get("/admin/companies").get_data(as_text=True)
    users_page = admin_client.get("/admin/platform-admins").get_data(as_text=True)

    for body in (companies_page, users_page):
        assert 'href="/admin/companies"' in body
        assert 'href="/admin/platform-admins"' in body

    assert "settings-nav__link is-active\">Companies" in companies_page
    assert "settings-nav__link is-active\">Admin Users" in users_page


# --- PA3-PA5: provisioning ------------------------------------------------

def test_creating_a_company_gives_it_the_full_starting_point(admin_client, app):
    """The point of routing through models.create_company: a tenant made
    here is indistinguishable from the one seed_if_empty() bootstraps."""
    from billing.services import invoicing
    from models import OrderType, SourceOption

    admin_client.post("/admin/companies", data={
        "name": "Second Studio", "timezone": "America/Toronto",
        "admin_email": "owner@second.example", "admin_password": "long-enough",
        "admin_full_name": "Second Owner",
    }, follow_redirects=True)

    made = Company.query.filter_by(name="Second Studio").one()
    assert made.timezone == "America/Toronto"
    assert made.is_active is True
    assert SourceOption.query.filter_by(company_id=made.id).count() > 0
    assert OrderType.query.filter_by(company_id=made.id).count() > 0
    assert invoicing.profile_for(made.id, made.name) is not None


def test_the_first_user_is_not_a_platform_admin(admin_client):
    """A studio owner runs their studio, not the platform. Regression: the
    obvious implementation copies seed_if_empty(), which *does* set the
    flag because on an empty database there's nobody else to set it on."""
    admin_client.post("/admin/companies", data={
        "name": "Third Studio", "timezone": "UTC",
        "admin_email": "owner@third.example", "admin_password": "long-enough",
        "admin_full_name": "Third Owner",
    }, follow_redirects=True)

    owner = User.query.filter_by(email="owner@third.example").one()
    assert owner.is_platform_admin is False
    assert owner.is_active is True


def test_a_new_company_gets_no_clients_or_orders(admin_client):
    """CO9a extended to every tenant, not just the bootstrapped one."""
    from models import Client, Order

    admin_client.post("/admin/companies", data={
        "name": "Fourth Studio", "timezone": "UTC",
        "admin_email": "owner@fourth.example", "admin_password": "long-enough",
    }, follow_redirects=True)

    made = Company.query.filter_by(name="Fourth Studio").one()
    assert Client.query.filter_by(company_id=made.id).count() == 0
    assert Order.query.join(Client).filter(Client.company_id == made.id).count() == 0


@pytest.mark.parametrize("field,value,fragment", [
    ("name", "", "company name is required"),
    ("admin_email", "", "email address is required"),
    ("admin_email", "not-an-address", "look like an email address"),
    ("admin_password", "short", "at least 8 characters"),
])
def test_a_bad_field_rejects_the_whole_company(admin_client, field, value, fragment):
    form = {
        "name": "Rejected Studio", "timezone": "UTC",
        "admin_email": "owner@rejected.example", "admin_password": "long-enough",
    }
    form[field] = value
    body = admin_client.post(
        "/admin/companies", data=form, follow_redirects=True).get_data(as_text=True)

    assert fragment in body
    # Nothing half-created: the company and its user land together or not
    # at all.
    assert Company.query.filter_by(name="Rejected Studio").count() == 0
    assert User.query.filter_by(email="owner@rejected.example").count() == 0


# --- PA6: email is unique across the whole platform -----------------------

def test_an_address_already_in_use_is_refused(admin_client, company, user):
    body = admin_client.post(f"/admin/companies/{company.id}/users", data={
        "email": "ADMIN@example.com", "password": "long-enough",
        "full_name": "Impostor",
    }, follow_redirects=True).get_data(as_text=True)

    assert "already belongs to a user" in body
    assert User.query.filter_by(email="admin@example.com").count() == 1


def test_the_same_address_is_refused_across_tenants(admin_client, other_company, user):
    """Regression: a per-company uniqueness check passes this and then
    makes the login lookup ambiguous, since login is by email alone."""
    body = admin_client.post(f"/admin/companies/{other_company.id}/users", data={
        "email": "admin@example.com", "password": "long-enough",
    }, follow_redirects=True).get_data(as_text=True)

    assert "already belongs to a user" in body


def test_addresses_are_stored_folded(admin_client, company):
    admin_client.post(f"/admin/companies/{company.id}/users", data={
        "email": "  Marie@Example.COM  ", "password": "long-enough",
    }, follow_redirects=True)

    assert User.query.filter_by(email="marie@example.com").count() == 1


def test_a_folded_address_signs_in(app, company):
    """The login lookup and the column write go through the same helper,
    so a capitalised address is the same account, not a missing one."""
    user = User(company_id=company.id, email=normalise_email("Marie@Example.com"))
    user.set_password("changeme")
    db.session.add(user)
    db.session.commit()

    with app.test_client() as client:
        response = client.post("/login", data={
            "email": "MARIE@example.com", "password": "changeme",
        }, follow_redirects=True)
        assert b"Incorrect email or password." not in response.data


# --- PA7-PA9: adding users and resetting passwords ------------------------

def test_a_user_is_added_to_the_named_company(admin_client, other_company):
    admin_client.post(f"/admin/companies/{other_company.id}/users", data={
        "email": "hire@other.example", "password": "long-enough",
        "full_name": "New Hire",
    }, follow_redirects=True)

    added = User.query.filter_by(email="hire@other.example").one()
    assert added.company_id == other_company.id
    assert added.is_platform_admin is False


def test_a_reset_password_is_what_signs_in_afterwards(admin_client, app, user):
    """No current password required, unlike /settings/account — that check
    guards an unattended browser and can't apply to an administrator
    acting on somebody else's account."""
    admin_client.post(
        f"/admin/users/{user.id}/password",
        data={"password": "brand-new-password"}, follow_redirects=True)

    fresh = signed_out_client(app)
    stale = fresh.post("/login", data={
        "email": "admin@example.com", "password": "changeme",
    }, follow_redirects=True)
    assert b"Incorrect email or password." in stale.data

    current = fresh.post("/login", data={
        "email": "admin@example.com", "password": "brand-new-password",
    }, follow_redirects=True)
    assert b"Incorrect email or password." not in current.data


def test_a_short_reset_is_refused(admin_client, user):
    original = user.password_hash
    body = admin_client.post(
        f"/admin/users/{user.id}/password",
        data={"password": "short"}, follow_redirects=True).get_data(as_text=True)

    assert "at least 8 characters" in body
    assert user.password_hash == original


def test_the_admin_minimum_matches_the_apps(app):
    """Two constants, one rule. They're duplicated to avoid a circular
    import, so something has to notice when they drift."""
    import app as app_module

    assert services.MIN_PASSWORD_LENGTH == app_module.MIN_PASSWORD_LENGTH


# --- PA10-PA12: deactivation, and what it does and doesn't touch ----------

def test_a_deactivated_user_cannot_sign_in(admin_client, app, user):
    admin_client.post(f"/admin/users/{user.id}/active", data={"active": "0"},
                      follow_redirects=True)

    response = signed_out_client(app).post("/login", data={
        "email": "admin@example.com", "password": "changeme",
    }, follow_redirects=True)
    assert b"no longer active" in response.data


def test_deactivating_a_company_locks_everyone_there_out(admin_client, app,
                                                         other_company):
    hire = User(company_id=other_company.id, email="hire@other.example")
    hire.set_password("changeme")
    db.session.add(hire)
    db.session.commit()

    admin_client.post(f"/admin/companies/{other_company.id}/active",
                      data={"active": "0"}, follow_redirects=True)

    response = signed_out_client(app).post("/login", data={
        "email": "hire@other.example", "password": "changeme",
    }, follow_redirects=True)
    assert b"no longer active" in response.data


def test_a_live_session_ends_the_moment_the_user_is_deactivated(admin_client,
                                                                app, user):
    """Regression: guarding only the login route makes deactivation a
    request to leave rather than an instruction — the existing session
    keeps working until its cookie expires. load_user() is what closes it."""
    tenant = signed_out_client(app)
    tenant.post("/login", data={"email": "admin@example.com",
                                "password": "changeme"}, follow_redirects=True)
    assert tenant.get("/").status_code == 200

    admin_client.post(f"/admin/users/{user.id}/active", data={"active": "0"},
                      follow_redirects=True)

    assert tenant.get("/", follow_redirects=False).status_code == 302


def test_deactivating_a_company_touches_none_of_its_records(admin_client,
                                                            other_company):
    """Hide, don't delete (hard rule 8) — and 'hide' here is roster scope
    only, exactly as it is for a Client."""
    from models import Client

    theirs = Client(company_id=other_company.id, first_name="Jean",
                    last_name="Tremblay")
    db.session.add(theirs)
    db.session.commit()

    admin_client.post(f"/admin/companies/{other_company.id}/active",
                      data={"active": "0"}, follow_redirects=True)

    assert Client.query.filter_by(company_id=other_company.id).count() == 1
    assert db.session.get(Company, other_company.id) is not None


def test_reactivating_restores_access(admin_client, app, other_company):
    hire = User(company_id=other_company.id, email="hire@other.example")
    hire.set_password("changeme")
    db.session.add(hire)
    db.session.commit()

    for active in ("0", "1"):
        admin_client.post(f"/admin/companies/{other_company.id}/active",
                          data={"active": active}, follow_redirects=True)

    response = signed_out_client(app).post("/login", data={
        "email": "hire@other.example", "password": "changeme",
    }, follow_redirects=True)
    assert b"no longer active" not in response.data


def test_any_company_can_be_deactivated(admin_client, company):
    """There's no "not your own company" guard any more, and there doesn't
    need to be: a platform admin belongs to no company, so switching one
    off can't lock them out of anything."""
    admin_client.post(f"/admin/companies/{company.id}/active",
                      data={"active": "0"}, follow_redirects=True)

    assert company.is_active is False


def test_you_cannot_deactivate_yourself(admin_client, platform_admin):
    body = admin_client.post(
        f"/admin/users/{platform_admin.id}/active", data={"active": "0"},
        follow_redirects=True).get_data(as_text=True)

    assert "can&#39;t deactivate your own account" in body
    assert platform_admin.is_active is True


# --- staff are a separate kind of account, never a promoted tenant user ---

def test_a_platform_admin_belongs_to_no_company(admin_client):
    admin_client.post("/admin/platform-admins", data={
        "email": "staff@example.test", "password": "long-enough",
        "full_name": "New Staff",
    }, follow_redirects=True)

    added = User.query.filter_by(email="staff@example.test").one()
    assert added.company_id is None
    assert added.is_platform_admin is True
    assert added.is_staff is True


def test_there_is_no_route_to_promote_a_tenant_user(admin_client, user):
    """The absence is the feature. A platform admin who also belonged to a
    studio is exactly what this model exists to prevent, so promotion isn't
    guarded — it doesn't exist."""
    response = admin_client.post(
        f"/admin/users/{user.id}/platform-admin",
        data={"is_platform_admin": "1"})

    assert response.status_code == 404
    assert user.is_platform_admin is False


def test_a_staff_address_collision_names_the_platform(admin_client,
                                                      platform_admin):
    body = admin_client.post("/admin/platform-admins", data={
        "email": "platform@example.com", "password": "long-enough",
    }, follow_redirects=True).get_data(as_text=True)

    assert "platform admin team" in body


def test_adding_a_platform_admin_returns_to_the_admin_users_page(admin_client):
    """Staff have no company, so `_back_to` can't send them to a company
    page — it has to know the Admin Users page is their home instead."""
    response = admin_client.post("/admin/platform-admins", data={
        "email": "new-staff@example.test", "password": "long-enough",
    }, follow_redirects=False)

    assert response.headers["Location"].endswith("/admin/platform-admins")


def test_resetting_a_staff_password_returns_to_the_admin_users_page(admin_client,
                                                                    platform_admin):
    response = admin_client.post(
        f"/admin/users/{platform_admin.id}/password",
        data={"password": "long-enough"}, follow_redirects=False)

    assert response.headers["Location"].endswith("/admin/platform-admins")


# --- PA13: the platform can never be left without an admin ----------------

def test_an_active_platform_admin_always_survives(admin_client, platform_admin):
    """The installation can never be left with nobody who can administer it.

    There is deliberately no separate last-active-admin check: whoever is
    calling got past `platform_admin_required`, so they're an active
    platform admin themselves, and the only account they can't switch off
    is their own. Deactivating every *other* staff account therefore still
    leaves one — them. This test is what says that reasoning still holds.
    """
    for address in ("second@example.test", "third@example.test"):
        row = User(company_id=None, email=address, is_platform_admin=True)
        row.set_password("changeme")
        db.session.add(row)
    db.session.commit()

    for row in User.query.filter(User.id != platform_admin.id).all():
        admin_client.post(f"/admin/users/{row.id}/active",
                          data={"active": "0"}, follow_redirects=True)

    survivors = User.query.filter(
        User.is_platform_admin.is_(True), User.is_active.is_(True)).all()
    assert [u.id for u in survivors] == [platform_admin.id]


def test_staff_can_be_deactivated_while_another_remains(admin_client,
                                                        platform_admin):
    second = User(company_id=None, email="second@example.test",
                  is_platform_admin=True)
    second.set_password("changeme")
    db.session.add(second)
    db.session.commit()

    admin_client.post(f"/admin/users/{second.id}/active", data={"active": "0"},
                      follow_redirects=True)

    assert second.is_active is False


# --- staff see /admin and nothing else ------------------------------------

@pytest.mark.parametrize("path", [
    "/", "/calendar", "/clients", "/orders", "/invoices", "/analytics",
    "/settings", "/settings/account", "/inventory",
])
def test_a_tenant_route_sends_staff_back_to_admin(admin_client, path):
    """The question those pages answer is "which of *this company's*…", and
    a platform admin hasn't got one. Enforced by a single before_request
    hook rather than a check in 155 places, so a route added later is
    covered without anyone remembering to cover it."""
    response = admin_client.get(path, follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/companies")


def test_staff_see_only_admin_and_log_out_in_the_nav(admin_client):
    body = admin_client.get("/admin/companies").get_data(as_text=True)

    assert 'href="/admin/companies"' in body
    assert 'href="/logout"' in body
    assert 'href="/calendar"' not in body
    assert 'href="/clients"' not in body


def test_a_tenant_user_still_sees_the_tenant_nav(logged_in):
    body = logged_in.get("/").get_data(as_text=True)

    assert 'href="/calendar"' in body
    assert 'href="/clients"' in body


def test_signing_in_as_staff_lands_on_admin(app, platform_admin):
    response = signed_out_client(app).post("/login", data={
        "email": "platform@example.com", "password": "changeme",
    }, follow_redirects=False)

    assert response.headers["Location"].endswith("/admin/companies")


def test_a_bookmarked_tenant_url_does_not_trap_staff(app, platform_admin):
    """`next` is honoured for tenant users only — following it here would
    bounce straight off the route guard."""
    response = signed_out_client(app).post("/login?next=/clients", data={
        "email": "platform@example.com", "password": "changeme",
    }, follow_redirects=False)

    assert response.headers["Location"].endswith("/admin/companies")


# --- PA14-PA16: impersonation ---------------------------------------------

@pytest.fixture
def tenant_user(other_company):
    """Somebody in a *different* company, so impersonation is observable
    as a change of tenant rather than only a change of person."""
    row = User(company_id=other_company.id, email="owner@other.example",
               full_name="Other Owner")
    row.set_password("changeme")
    db.session.add(row)
    db.session.commit()
    return row


def test_impersonating_switches_tenant(admin_client, tenant_user, other_company):
    admin_client.post(f"/admin/users/{tenant_user.id}/impersonate",
                      follow_redirects=True)

    with admin_client.session_transaction() as sess:
        assert sess["_user_id"] == str(tenant_user.id)


def test_the_banner_says_who_and_where(admin_client, tenant_user):
    admin_client.post(f"/admin/users/{tenant_user.id}/impersonate",
                      follow_redirects=True)
    body = admin_client.get("/").get_data(as_text=True)

    assert "impersonation-banner" in body
    assert "Other Owner" in body
    assert "Other Studio" in body


def test_admin_is_unreachable_while_impersonating(admin_client, tenant_user):
    """The load-bearing guard: a platform admin who forgot they were
    impersonating must not be able to provision a company 'as' somebody
    else. Regression — checking `current_user.is_platform_admin` alone
    passes this whenever the impersonated user happens to hold the flag,
    and silently fails open."""
    admin_client.post(f"/admin/users/{tenant_user.id}/impersonate",
                      follow_redirects=True)

    assert admin_client.get("/admin/").status_code == 403
    # The nav link specifically — the banner's "return to admin" form posts
    # to /admin/stop-impersonating and is supposed to be there.
    assert 'href="/admin/companies"' not in admin_client.get("/").get_data(as_text=True)


def test_impersonating_a_flagged_user_still_locks_admin_out(admin_client, user):
    """The same regression from the other side: the impersonated user does
    hold the flag here, so only the session key can tell the difference.

    The state is deliberately impossible to reach through the UI — a
    tenant user with `is_platform_admin` can't be created any more — and
    is built by hand precisely because the guard must not depend on that
    remaining true."""
    user.is_platform_admin = True
    db.session.commit()

    admin_client.post(f"/admin/users/{user.id}/impersonate", follow_redirects=True)
    assert admin_client.get("/admin/").status_code == 403


def test_returning_restores_the_platform_admin(admin_client, tenant_user,
                                               platform_admin):
    admin_client.post(f"/admin/users/{tenant_user.id}/impersonate",
                      follow_redirects=True)
    admin_client.post("/admin/stop-impersonating", follow_redirects=True)

    with admin_client.session_transaction() as sess:
        assert sess["_user_id"] == str(platform_admin.id)
        assert services.IMPERSONATOR_KEY not in sess
    assert admin_client.get("/admin/companies").status_code == 200


def test_the_exit_route_is_refused_when_not_impersonating(admin_client):
    assert admin_client.post("/admin/stop-impersonating").status_code == 403


def test_a_tenant_user_cannot_start_impersonating(logged_in, other_company):
    row = User(company_id=other_company.id, email="victim@other.example")
    row.set_password("changeme")
    db.session.add(row)
    db.session.commit()

    assert logged_in.post(f"/admin/users/{row.id}/impersonate").status_code == 403


def test_a_deactivated_account_cannot_be_impersonated(admin_client, tenant_user):
    tenant_user.is_active = False
    db.session.commit()

    body = admin_client.post(
        f"/admin/users/{tenant_user.id}/impersonate",
        follow_redirects=True).get_data(as_text=True)

    assert "deactivated" in body
    assert admin_client.get("/admin/companies").status_code == 200


def test_a_deactivated_company_cannot_be_impersonated_into(admin_client,
                                                           tenant_user,
                                                           other_company):
    """Not a policy invented in admin/services.py — load_user() drops any
    session whose company is off, so the impersonation would survive one
    redirect and then dump the admin at /login."""
    other_company.is_active = False
    db.session.commit()

    body = admin_client.post(
        f"/admin/users/{tenant_user.id}/impersonate",
        follow_redirects=True).get_data(as_text=True)

    assert "Reactivate it first" in body


# --- the tenant boundary is untouched by any of this ----------------------

def test_impersonation_sees_exactly_that_tenant(admin_client, tenant_user,
                                                company, other_company):
    """The only way staff read tenant data, and it goes through the app's
    ordinary `current_user.company_id` filtering (hard rule 1) rather than
    round it — so what they see is what that user sees, no more."""
    from models import Client

    mine = Client(company_id=company.id, first_name="Marie", last_name="Alarie")
    theirs = Client(company_id=other_company.id, first_name="Jean",
                    last_name="Tremblay")
    db.session.add_all([mine, theirs])
    db.session.commit()

    admin_client.post(f"/admin/users/{tenant_user.id}/impersonate",
                      follow_redirects=True)
    body = admin_client.get("/clients").get_data(as_text=True)

    assert "Tremblay" in body
    assert "Marie" not in body


def test_create_company_is_the_one_provisioning_path(app):
    """models.create_company() is called by seed_if_empty() and by the
    admin area both. Called directly here so a starter list added to one
    caller and not the other shows up as a failure."""
    from models import OrderType, SourceOption

    made, admin = create_company("Direct Studio", "direct@example.test",
                                 "long-enough", timezone="UTC")
    db.session.commit()

    assert made.timezone == "UTC"
    assert admin.company_id == made.id
    assert admin.check_password("long-enough")
    assert SourceOption.query.filter_by(company_id=made.id).count() > 0
    assert OrderType.query.filter_by(company_id=made.id).count() > 0


# --- Platform settings: the announcement banner ----------------------------

def test_the_settings_page_is_the_third_sub_nav_tab(admin_client):
    body = admin_client.get("/admin/settings").get_data(as_text=True)

    assert 'href="/admin/companies"' in body
    assert 'href="/admin/platform-admins"' in body
    assert "settings-nav__link is-active\">Settings" in body


def test_no_banner_by_default(admin_client, app, user):
    """A fresh installation has a lazily-created, inactive settings row —
    nothing renders anywhere until an admin turns it on."""
    assert "announcement-banner" not in admin_client.get(
        "/admin/companies").get_data(as_text=True)

    tenant = signed_out_client(app)
    tenant.post("/login", data={"email": "admin@example.com",
                                "password": "changeme"}, follow_redirects=True)
    assert "announcement-banner" not in tenant.get("/").get_data(as_text=True)


def test_getting_the_settings_row_does_not_create_a_second_one(app):
    """`get_platform_settings()` is called from a route on every request;
    it must return the same singleton, not insert a new row each time."""
    from admin.models import PlatformSettings
    from admin.services import get_platform_settings

    first = get_platform_settings()
    second = get_platform_settings()

    assert first.id == second.id == 1
    assert PlatformSettings.query.count() == 1


def test_saving_an_announcement_shows_it_on_a_tenant_page(admin_client, app,
                                                          user):
    admin_client.post("/admin/settings/announcement", data={
        "message": "Brief downtime Sunday 2-4am Pacific.", "active": "1",
    }, follow_redirects=True)

    tenant = signed_out_client(app)
    tenant.post("/login", data={"email": "admin@example.com",
                                "password": "changeme"}, follow_redirects=True)
    body = tenant.get("/").get_data(as_text=True)
    assert "announcement-banner" in body
    assert "Brief downtime Sunday 2-4am Pacific." in body


def test_saving_an_announcement_shows_it_on_the_staff_page(admin_client):
    admin_client.post("/admin/settings/announcement", data={
        "message": "Staff sees this too.", "active": "1",
    }, follow_redirects=True)

    body = admin_client.get("/admin/companies").get_data(as_text=True)
    assert "Staff sees this too." in body


def test_saving_an_announcement_shows_it_signed_out(admin_client, app):
    """The point of the whole feature: someone about to sign in, during a
    maintenance window, is exactly who most needs to see this."""
    admin_client.post("/admin/settings/announcement", data={
        "message": "Maintenance in progress.", "active": "1",
    }, follow_redirects=True)

    body = signed_out_client(app).get("/login").get_data(as_text=True)
    assert "announcement-banner" in body
    assert "Maintenance in progress." in body


def test_saving_an_announcement_shows_on_the_public_legal_pages(admin_client,
                                                                app):
    admin_client.post("/admin/settings/announcement", data={
        "message": "Visible on privacy too.", "active": "1",
    }, follow_redirects=True)

    body = signed_out_client(app).get("/privacy").get_data(as_text=True)
    assert "Visible on privacy too." in body


def test_turning_it_off_removes_the_banner_but_keeps_the_draft(admin_client):
    admin_client.post("/admin/settings/announcement", data={
        "message": "Recurring maintenance window.", "active": "1",
    }, follow_redirects=True)

    admin_client.post("/admin/settings/announcement", data={
        "message": "Recurring maintenance window.", "active": "0",
    }, follow_redirects=True)

    body = admin_client.get("/admin/companies").get_data(as_text=True)
    assert "announcement-banner" not in body

    # The draft survives being switched off, so it doesn't need retyping
    # the next time the same window comes around.
    settings_page = admin_client.get("/admin/settings").get_data(as_text=True)
    assert "Recurring maintenance window." in settings_page


def test_turning_it_on_with_a_blank_message_is_refused(admin_client):
    body = admin_client.post("/admin/settings/announcement", data={
        "message": "   ", "active": "1",
    }, follow_redirects=True).get_data(as_text=True)

    assert "Write a message" in body
    from admin.services import get_platform_settings
    assert get_platform_settings().is_active is False


def test_a_blank_message_while_off_just_clears_the_draft(admin_client):
    admin_client.post("/admin/settings/announcement", data={
        "message": "Old draft.", "active": "0",
    }, follow_redirects=True)

    admin_client.post("/admin/settings/announcement", data={
        "message": "", "active": "0",
    }, follow_redirects=True)

    from admin.services import get_platform_settings
    assert get_platform_settings().announcement is None


def test_the_message_is_escaped(admin_client, app, user):
    """Plain text, not markup — an admin's typo shouldn't become a script
    tag on every signed-in and signed-out page in the installation."""
    admin_client.post("/admin/settings/announcement", data={
        "message": "<script>alert(1)</script>", "active": "1",
    }, follow_redirects=True)

    tenant = signed_out_client(app)
    tenant.post("/login", data={"email": "admin@example.com",
                                "password": "changeme"}, follow_redirects=True)
    body = tenant.get("/").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_a_tenant_user_cannot_change_the_announcement(logged_in):
    response = logged_in.post("/admin/settings/announcement", data={
        "message": "Injected.", "active": "1",
    })
    assert response.status_code == 403
