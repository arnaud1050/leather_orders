"""
Sign-in hardening: `?next=` stays on this site (CO4g) and failed attempts
are limited per address and per client IP (CO4k–CO4m).

The lockout tests move the clock through `login_throttle._utcnow` rather
than sleeping, and assert on what the page *does* (where it redirects,
whether a session exists) rather than only on the words it shows.
"""

from datetime import timedelta
from urllib.parse import quote

import pytest

import login_throttle
from login_throttle import LOCKED_MESSAGE, MAX_FAILURES_PER_EMAIL, MAX_FAILURES_PER_IP, WINDOW
from models import User, db
from tests.conftest import csrf_token_for
from tests.conftest import sign_in as staff_sign_in

import app as app_module


def _sign_in(client, email="admin@example.com", password="changeme", next_url=None, ip=None):
    url = "/login" if next_url is None else "/login?next=" + next_url
    environ = {"REMOTE_ADDR": ip} if ip else {}
    return client.post(url, data={"email": email, "password": password},
                       environ_base=environ)


def _is_signed_in(client):
    with client.session_transaction() as session:
        return "_user_id" in session


# --- ?next= ------------------------------------------------------------------

@pytest.mark.parametrize("hostile", [
    "https://evil.example/phish",
    "http://evil.example",
    "//evil.example",
    "///evil.example",
    "/\\evil.example",
    "\\\\evil.example",
    "/%5Cevil.example",
    "%2F%2Fevil.example",
    "/%2F/evil.example",
    "/\t/evil.example",
    "javascript:alert(1)",
    "evil.example",
    "",
])
def test_a_next_that_leaves_the_site_is_ignored(app, user, hostile):
    with app.test_client() as client:
        response = _sign_in(client, next_url=hostile)
    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_a_next_on_this_site_is_honoured(app, user):
    target = "/orders/5?return_to=/%3Fstart%3D2026-07-01"
    with app.test_client() as client:
        response = _sign_in(client, next_url=quote(target, safe=""))
    assert response.status_code == 302
    assert response.headers["Location"] == target


@pytest.mark.parametrize("value,expected", [
    ("/orders/5", "/orders/5"),
    ("/orders/5?return_to=/timeline", "/orders/5?return_to=/timeline"),
    ("//evil", None),
    ("/\\evil", None),
    ("https://evil", None),
    ("/%2F/evil", None),
    ("/ok\r\nSet-Cookie: x", None),
    (None, None),
])
def test_safe_next_path(value, expected):
    assert app_module.safe_next_path(value) == expected


def test_staff_still_ignore_next(app, platform_admin):
    with app.test_client() as client:
        response = staff_sign_in(client, "platform@example.com", next_url="/orders/5")
    assert response.headers["Location"] == "/admin/companies"


# --- lockout -------------------------------------------------------------------

@pytest.fixture
def clock(monkeypatch):
    """A movable clock for login_throttle."""
    state = {"now": login_throttle._utcnow()}
    monkeypatch.setattr(login_throttle, "_utcnow", lambda: state["now"])

    def advance(delta):
        state["now"] += delta
    return advance


def _fail(client, times, email="admin@example.com", ip=None):
    for _ in range(times):
        response = _sign_in(client, email=email, password="wrong", ip=ip)
        assert response.status_code == 200
        assert "Incorrect email or password." in response.get_data(as_text=True)


def test_the_lockout_triggers_after_too_many_failures(app, user, clock):
    with app.test_client() as client:
        _fail(client, MAX_FAILURES_PER_EMAIL)
        response = _sign_in(client, password="wrong")
    assert response.status_code == 429
    assert LOCKED_MESSAGE in response.get_data(as_text=True)


def test_the_right_password_is_refused_during_a_lockout(app, user, clock):
    with app.test_client() as client:
        _fail(client, MAX_FAILURES_PER_EMAIL)
        response = _sign_in(client)
        assert response.status_code == 429
        assert not _is_signed_in(client)


def test_the_lockout_expires(app, user, clock):
    with app.test_client() as client:
        _fail(client, MAX_FAILURES_PER_EMAIL)
        clock(WINDOW + timedelta(seconds=1))
        response = _sign_in(client)
        assert response.status_code == 302
        assert _is_signed_in(client)


def test_hammering_a_locked_address_does_not_extend_the_lock(app, user, clock):
    with app.test_client() as client:
        _fail(client, MAX_FAILURES_PER_EMAIL)
        for _ in range(10):
            clock(timedelta(minutes=1))
            assert _sign_in(client, password="wrong").status_code == 429
        clock(WINDOW - timedelta(minutes=10) + timedelta(seconds=1))
        assert _sign_in(client).status_code == 302


def test_the_lockout_is_per_email(app, user, clock):
    other = User(company_id=user.company_id, email="cutter@example.com", full_name="Cutter")
    other.set_password("changeme")
    db.session.add(other)
    db.session.flush()
    with app.test_client() as client:
        _fail(client, MAX_FAILURES_PER_EMAIL)
        assert _sign_in(client).status_code == 429
        assert _sign_in(client, email="cutter@example.com").status_code == 302


def test_the_lockout_applies_to_an_unknown_email_too(app, user, clock):
    """Same count, same message, same status — or the lockout itself would
    say which addresses have accounts."""
    with app.test_client() as client:
        _fail(client, MAX_FAILURES_PER_EMAIL, email="nobody@example.com")
        unknown = _sign_in(client, email="nobody@example.com", password="wrong")
        _fail(client, MAX_FAILURES_PER_EMAIL)
        known = _sign_in(client, password="wrong")
    assert unknown.status_code == known.status_code == 429
    assert LOCKED_MESSAGE in unknown.get_data(as_text=True)


def test_the_address_is_normalised_before_counting(app, user, clock):
    with app.test_client() as client:
        for spelling in ["Admin@Example.com", "ADMIN@example.com", " admin@example.com",
                         "admin@EXAMPLE.com", "admin@example.com"]:
            _sign_in(client, email=spelling, password="wrong")
        assert _sign_in(client).status_code == 429


def test_a_successful_sign_in_clears_the_counter(app, user, clock):
    with app.test_client() as client:
        _fail(client, MAX_FAILURES_PER_EMAIL - 1)
        assert _sign_in(client).status_code == 302
        client.post("/logout")
        _fail(client, MAX_FAILURES_PER_EMAIL - 1)
        assert _sign_in(client).status_code == 302


def test_the_counter_is_kept_in_the_database(app, user, clock):
    """Two gunicorn workers share nothing but the database."""
    with app.test_client() as client:
        _fail(client, 2)
    assert login_throttle.LoginAttempt.query.count() == 2
    attempt = login_throttle.LoginAttempt.query.first()
    assert attempt.email == "admin@example.com"
    assert not hasattr(attempt, "password")


def test_old_failures_are_pruned(app, user, clock):
    with app.test_client() as client:
        _fail(client, 3)
        clock(WINDOW + timedelta(seconds=1))
        _fail(client, 1)
    assert login_throttle.LoginAttempt.query.count() == 1


def test_one_ip_trying_many_addresses_is_limited(app, user, clock):
    with app.test_client() as client:
        for n in range(MAX_FAILURES_PER_IP):
            _sign_in(client, email=f"guess{n}@example.com", password="wrong", ip="203.0.113.7")
        from_there = _sign_in(client, ip="203.0.113.7")
        from_elsewhere = _sign_in(client, ip="198.51.100.2")
    assert from_there.status_code == 429
    assert from_elsewhere.status_code == 302


def test_the_ip_limit_reads_the_forwarded_address_behind_the_proxy(app, user, clock):
    """With ProxyFix (TRUST_PROXY_HEADERS=1) remote_addr is the browser's
    address from X-Forwarded-For, not nginx's — otherwise one source's
    guesses would lock out everyone."""
    from werkzeug.middleware.proxy_fix import ProxyFix

    original = app.wsgi_app
    app.wsgi_app = ProxyFix(original, x_for=1, x_proto=1, x_host=1)
    try:
        with app.test_client() as client:
            for n in range(MAX_FAILURES_PER_IP):
                client.post("/login", data={"email": f"g{n}@example.com", "password": "x"},
                            headers={"X-Forwarded-For": "203.0.113.7"},
                            environ_base={"REMOTE_ADDR": "172.18.0.2"})
            response = client.post(
                "/login", data={"email": "admin@example.com", "password": "changeme"},
                headers={"X-Forwarded-For": "198.51.100.2"},
                environ_base={"REMOTE_ADDR": "172.18.0.2"})
    finally:
        app.wsgi_app = original
    assert response.status_code == 302
    assert {a.ip for a in login_throttle.LoginAttempt.query} == {"203.0.113.7"}


# --- a sign-in starts a fresh session (CO4o) ----------------------------------

def _studio_user(user):
    row = User(company_id=user.company_id, email="owner@studio.example", full_name="Owner")
    row.set_password("changeme")
    db.session.add(row)
    db.session.commit()
    return row


def test_signing_out_mid_impersonation_leaves_admin_reachable(app, user, platform_admin):
    """The production bug: impersonate, sign out from inside the tenant
    session, sign back in as staff — and /admin answered 403, because
    `impersonator_id` outlived the sign-out."""
    tenant = _studio_user(user)
    with app.test_client() as client:
        staff_sign_in(client, "platform@example.com")
        client.post(f"/admin/users/{tenant.id}/impersonate")
        client.post("/logout")
        with client.session_transaction() as session:
            assert "impersonator_id" not in session
        response = staff_sign_in(client, "platform@example.com")
        assert response.headers["Location"] == "/admin/companies"
        assert client.get("/admin/companies").status_code == 200


def test_a_stale_impersonation_key_does_not_survive_sign_in(app, platform_admin):
    """However it got there (a session dropped by load_user mid-
    impersonation, a cookie from before this fix), signing in clears it."""
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["impersonator_id"] = platform_admin.id
        staff_sign_in(client, "platform@example.com")
        assert client.get("/admin/companies").status_code == 200


def test_sign_in_starts_a_fresh_session(app, user):
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["planted"] = "by someone else"
        _sign_in(client)
        with client.session_transaction() as session:
            assert "planted" not in session
            assert "_user_id" in session


# --- signing out is a POST (CO4p) --------------------------------------------

def test_a_get_to_logout_does_not_sign_out(logged_in):
    """Any page on the web can make a browser GET a URL; none should be
    able to sign the studio out."""
    page = logged_in.get("/logout")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert 'method="post" action="/logout"' in body
    assert _is_signed_in(logged_in)
    assert logged_in.get("/").status_code == 200


def test_posting_to_logout_signs_out(logged_in):
    response = logged_in.post("/logout")
    assert response.status_code == 302
    assert response.headers["Location"] == "/login"
    assert not _is_signed_in(logged_in)
    assert logged_in.get("/").status_code == 302


def test_logging_out_needs_a_csrf_token(logged_in, enforce_csrf):
    assert logged_in.post("/logout").status_code == 400
    assert _is_signed_in(logged_in)
    token = csrf_token_for(logged_in)
    assert logged_in.post("/logout", data={"csrf_token": token}).status_code == 302
    assert not _is_signed_in(logged_in)


def test_the_nav_logs_out_with_a_form(logged_in):
    body = logged_in.get("/").get_data(as_text=True)
    assert 'href="/logout"' not in body
    assert 'class="view-switch__logout-form"' in body


def test_a_signed_out_visit_to_logout_goes_to_sign_in(app):
    with app.test_client() as client:
        response = client.get("/logout")
    assert response.status_code == 302
    assert response.headers["Location"] == "/login"
