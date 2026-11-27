"""
Two-factor sign-in (REQUIREMENTS CO4s-CO4w).

Setting it up, the two-step sign-in, recovery codes, the lockout it shares
with passwords, the staff requirement, and turning it off (yourself, an
admin for someone else, the server script). Codes are real TOTP codes for a
known secret; the clock is `two_factor._now`, moved by a fixture rather
than slept, and the code step's own wait is `app.time`.
"""

import re

import pyotp
import pytest

import app as app_module
import two_factor
from login_throttle import LOCKED_MESSAGE, MAX_FAILURES_PER_EMAIL
from models import User, db
from tests.conftest import STAFF_TOTP_SECRET, current_code, sign_in

SECRET = "KRSXG5CTMVRXEZLUKRSXG5CTMVRXEZLU"


@pytest.fixture
def clock(monkeypatch):
    """A frozen, movable TOTP clock, in whole 30-second steps."""
    state = {"now": 1_800_000_000.0}
    monkeypatch.setattr(two_factor, "_now", lambda: state["now"])

    def advance(steps=1):
        state["now"] += steps * two_factor.STEP_SECONDS
    return advance


def code_at(offset_steps=0, secret=SECRET):
    return pyotp.TOTP(secret).at(two_factor._now() + offset_steps * two_factor.STEP_SECONDS)


@pytest.fixture
def protected_user(user):
    """The studio user, with two-factor already on."""
    two_factor.enable(user, SECRET, proved_step=0)
    db.session.commit()
    return user


def _signed_in(client):
    with client.session_transaction() as session:
        return "_user_id" in session


def _password_step(client, email="admin@example.com", next_url=None):
    url = "/login" if next_url is None else f"/login?next={next_url}"
    return client.post(url, data={"email": email, "password": "changeme"})


def _setup_secret(client):
    """The secret the setup page is showing, read from the session."""
    client.get("/account/two-factor")
    with client.session_transaction() as session:
        return two_factor.decrypt_secret(session["two_factor_setup_secret"])


# --- CO4s: setting it up ---------------------------------------------------------

def test_the_setup_page_shows_a_qr_code_and_the_key(logged_in, clock):
    body = logged_in.get("/account/two-factor").get_data(as_text=True)
    assert 'class="two-factor-qr"' in body
    secret = _setup_secret(logged_in)
    assert two_factor.format_secret(secret) in body
    assert "<script" not in body.split('class="two-factor-qr"')[1].split("</svg>")[0]


def test_nothing_is_stored_until_a_code_proves_the_scan(logged_in, user, clock):
    _setup_secret(logged_in)
    assert db.session.get(User, user.id).totp_secret is None
    logged_in.post("/account/two-factor/enable", data={"code": "000000"})
    db.session.expire_all()
    assert not db.session.get(User, user.id).two_factor_enabled
    body = logged_in.get("/account/two-factor").get_data(as_text=True)
    assert "That code didn" in body


def test_a_right_code_turns_it_on_and_shows_the_codes_once(logged_in, user, clock):
    secret = _setup_secret(logged_in)
    response = logged_in.post("/account/two-factor/enable", data={"code": code_at(0, secret)})
    assert response.status_code == 302
    db.session.expire_all()
    stored = db.session.get(User, user.id)
    assert stored.two_factor_enabled
    assert secret not in stored.totp_secret          # encrypted at rest
    assert two_factor.decrypt_secret(stored.totp_secret) == secret

    first = logged_in.get("/account/two-factor").get_data(as_text=True)
    codes = re.findall(r"<li>([a-z0-9]{5}-[a-z0-9]{5})</li>", first)
    assert len(codes) == two_factor.RECOVERY_CODE_COUNT
    second = logged_in.get("/account/two-factor").get_data(as_text=True)
    assert codes[0] not in second


def test_the_setup_code_cannot_then_sign_in(app, logged_in, user, clock):
    """The code that proved the scan was seen by the page: it counts as
    used, like any other."""
    secret = _setup_secret(logged_in)
    code = code_at(0, secret)
    logged_in.post("/account/two-factor/enable", data={"code": code})
    logged_in.post("/logout")
    _password_step(logged_in)
    assert logged_in.post("/login/verify", data={"code": code}).status_code == 200
    assert not _signed_in(logged_in)


# --- CO4s: signing in ----------------------------------------------------------------

def test_the_password_alone_does_not_sign_in(app, protected_user, clock):
    with app.test_client() as client:
        response = _password_step(client)
        assert response.headers["Location"] == "/login/verify"
        assert not _signed_in(client)
        assert client.get("/").status_code == 302


def test_the_code_completes_it_and_next_is_kept(app, protected_user, clock):
    with app.test_client() as client:
        _password_step(client, next_url="/clients")
        response = client.post("/login/verify", data={"code": code_at()})
        assert response.headers["Location"] == "/clients"
        assert _signed_in(client)


def test_a_wrong_code_is_refused(app, protected_user, clock):
    with app.test_client() as client:
        _password_step(client)
        response = client.post("/login/verify", data={"code": "123456"})
        assert response.status_code == 200
        assert "didn" in response.get_data(as_text=True)
        assert not _signed_in(client)


def test_a_code_is_good_once(app, protected_user, clock):
    code = code_at()
    with app.test_client() as client:
        _password_step(client)
        client.post("/login/verify", data={"code": code})
        client.post("/logout")
        _password_step(client)
        client.post("/login/verify", data={"code": code})
        assert not _signed_in(client)


@pytest.mark.parametrize("offset,accepted", [(-1, True), (1, True), (-2, False), (2, False)])
def test_the_clock_may_be_one_step_off(app, protected_user, clock, offset, accepted):
    clock(10)
    with app.test_client() as client:
        _password_step(client)
        client.post("/login/verify", data={"code": code_at(offset)})
        assert _signed_in(client) is accepted


def test_the_code_step_waits_five_minutes(app, protected_user, clock, monkeypatch):
    with app.test_client() as client:
        _password_step(client)
        later = app_module.time.time() + app_module._TWO_FACTOR_PENDING_SECONDS + 1
        monkeypatch.setattr(app_module.time, "time", lambda: later)
        response = client.post("/login/verify", data={"code": code_at()})
        assert response.headers["Location"] == "/login"
        assert not _signed_in(client)


def test_the_verify_page_needs_a_password_first(app, protected_user):
    with app.test_client() as client:
        assert client.get("/login/verify").headers["Location"] == "/login"


def test_an_account_switched_off_mid_sign_in_is_refused(app, protected_user, clock):
    with app.test_client() as client:
        _password_step(client)
        protected_user.is_active = False
        db.session.commit()
        client.post("/login/verify", data={"code": code_at()})
        assert not _signed_in(client)


def test_an_account_without_it_signs_in_with_the_password(app, user):
    with app.test_client() as client:
        assert _password_step(client).headers["Location"] == "/"
        assert _signed_in(client)


# --- CO4s: the lockout covers codes -------------------------------------------

def test_wrong_codes_lead_to_the_lockout(app, protected_user, clock):
    with app.test_client() as client:
        _password_step(client)
        for _ in range(MAX_FAILURES_PER_EMAIL):
            client.post("/login/verify", data={"code": "000000"})
        response = client.post("/login/verify", data={"code": code_at()})
        assert response.status_code == 429
        assert LOCKED_MESSAGE in response.get_data(as_text=True)
        assert not _signed_in(client)


def test_a_right_password_does_not_reset_the_code_failures(app, protected_user, clock):
    """Or a stolen password could alternate with a few guesses forever."""
    with app.test_client() as client:
        for _ in range(MAX_FAILURES_PER_EMAIL):
            _password_step(client)
            client.post("/login/verify", data={"code": "000000"})
        assert _password_step(client).status_code == 429


def test_a_full_sign_in_clears_the_failures(app, protected_user, clock):
    with app.test_client() as client:
        _password_step(client)
        for _ in range(MAX_FAILURES_PER_EMAIL - 1):
            client.post("/login/verify", data={"code": "000000"})
        client.post("/login/verify", data={"code": code_at()})
        assert _signed_in(client)
    import login_throttle
    assert login_throttle.LoginAttempt.query.count() == 0


# --- CO4t: recovery codes ----------------------------------------------------------

def test_a_recovery_code_signs_in_once(app, protected_user, clock):
    codes = two_factor.replace_recovery_codes(protected_user)
    db.session.commit()
    with app.test_client() as client:
        _password_step(client)
        response = client.post("/login/verify", data={"code": codes[0].upper().replace("-", " ")})
        assert response.headers["Location"] == "/account/two-factor"
        assert _signed_in(client)
        page = client.get("/account/two-factor").get_data(as_text=True)
        assert f"{two_factor.RECOVERY_CODE_COUNT - 1} left" in page
        client.post("/logout")
        _password_step(client)
        client.post("/login/verify", data={"code": codes[0]})
        assert not _signed_in(client)


def test_recovery_codes_are_stored_as_digests(protected_user):
    codes = two_factor.replace_recovery_codes(protected_user)
    db.session.commit()
    stored = {row.code_hash for row in two_factor.RecoveryCode.query}
    assert not any(code in stored or code.replace("-", "") in stored for code in codes)


def test_new_recovery_codes_need_a_code_and_cancel_the_old(app, protected_user, clock):
    old = two_factor.replace_recovery_codes(protected_user)
    db.session.commit()
    with app.test_client() as client:
        _password_step(client)
        client.post("/login/verify", data={"code": code_at()})
        clock()
        client.post("/account/two-factor/recovery-codes", data={"code": "000000"})
        assert two_factor.use_recovery_code(protected_user, old[1]) is True
        db.session.rollback()
        client.post("/account/two-factor/recovery-codes", data={"code": code_at()})
    db.session.expire_all()
    assert two_factor.use_recovery_code(protected_user, old[2]) is False
    assert two_factor.recovery_codes_left(protected_user) == two_factor.RECOVERY_CODE_COUNT


# --- CO4u: required for staff ---------------------------------------------------------

def test_staff_without_it_are_held_on_setup(app, platform_admin, clock):
    two_factor.disable(platform_admin)
    db.session.commit()
    with app.test_client() as client:
        response = client.post("/login", data={"email": "platform@example.com", "password": "changeme"})
        assert response.headers["Location"] == "/admin/companies"
        assert client.get("/admin/companies").headers["Location"] == "/account/two-factor"
        assert client.get("/admin/platform-admins").headers["Location"] == "/account/two-factor"
        page = client.get("/account/two-factor").get_data(as_text=True)
        assert "Platform admins need two-factor sign-in" in page

        secret = _setup_secret(client)
        client.post("/account/two-factor/enable", data={"code": code_at(0, secret)})
        assert client.get("/admin/companies").status_code == 200


def test_staff_with_it_sign_in_with_a_code(app, platform_admin):
    db.session.commit()
    with app.test_client() as client:
        assert client.post("/login", data={
            "email": "platform@example.com", "password": "changeme",
        }).headers["Location"] == "/login/verify"
        client.post("/login/verify", data={"code": current_code(STAFF_TOTP_SECRET)})
        assert client.get("/admin/companies").status_code == 200


def test_staff_cannot_turn_it_off(app, admin_client, platform_admin):
    admin_client.post("/account/two-factor/disable", data={"code": current_code()})
    db.session.expire_all()
    assert db.session.get(User, platform_admin.id).two_factor_enabled


# --- CO4w: turning it off yourself -----------------------------------------------------

def test_a_studio_user_turns_it_off_with_a_code(app, protected_user, clock):
    with app.test_client() as client:
        _password_step(client)
        client.post("/login/verify", data={"code": code_at()})
        clock()
        client.post("/account/two-factor/disable", data={"code": "000000"})
        db.session.expire_all()
        assert db.session.get(User, protected_user.id).two_factor_enabled
        client.post("/account/two-factor/disable", data={"code": code_at()})
    db.session.expire_all()
    stored = db.session.get(User, protected_user.id)
    assert not stored.two_factor_enabled
    assert stored.totp_secret is None
    assert two_factor.RecoveryCode.query.count() == 0


def test_settings_account_links_to_the_page(logged_in):
    body = logged_in.get("/settings/account").get_data(as_text=True)
    assert 'href="/account/two-factor"' in body
    assert "Set up two-factor sign-in" in body


# --- CO4v: someone else turns it off -----------------------------------------------------

def test_an_admin_resets_a_studio_user(app, admin_client, protected_user):
    page = admin_client.get(f"/admin/companies/{protected_user.company_id}").get_data(as_text=True)
    assert f"/admin/users/{protected_user.id}/two-factor/reset" in page
    admin_client.post(f"/admin/users/{protected_user.id}/two-factor/reset")
    db.session.expire_all()
    assert not db.session.get(User, protected_user.id).two_factor_enabled


def test_an_admin_cannot_reset_their_own(app, admin_client, platform_admin):
    admin_client.post(f"/admin/users/{platform_admin.id}/two-factor/reset")
    db.session.expire_all()
    assert db.session.get(User, platform_admin.id).two_factor_enabled


def test_a_tenant_user_cannot_reset_anyone(app, logged_in, protected_user):
    assert logged_in.post(f"/admin/users/{protected_user.id}/two-factor/reset").status_code == 403


def test_the_server_script_resets_an_account(app, protected_user, capsys):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "reset_two_factor.py"
    spec = importlib.util.spec_from_file_location("reset_two_factor", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    assert script.main(["reset_two_factor.py", "ADMIN@example.com"]) == 0
    db.session.expire_all()
    assert not db.session.get(User, protected_user.id).two_factor_enabled
    assert "turned off" in capsys.readouterr().out
    assert script.main(["reset_two_factor.py", "nobody@example.com"]) == 1


# --- storage -------------------------------------------------------------------------------

def test_an_unreadable_secret_fails_closed(app, protected_user, clock, monkeypatch):
    """A rotated key: codes stop working (recovery codes still do), and
    nothing crashes."""
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", "0" * 43 + "=")
    with app.test_client() as client:
        _password_step(client)
        assert client.post("/login/verify", data={"code": code_at()}).status_code == 200
        assert not _signed_in(client)
