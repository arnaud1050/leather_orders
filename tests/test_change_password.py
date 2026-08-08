"""
Settings > Account: changing the signed-in user's own password.

The rules worth defending are the ones that fail quietly: a change that
"succeeds" without the current password, a rejected attempt that writes a
new hash anyway, and a successful change that doesn't actually take effect
at the login form. Each test below asserts the hash, not just the message —
a redirect with the right words on the far side proves nothing about what
was stored.
"""

from models import User, db


def _change(client, current="changeme", new="new-password", confirm=None):
    return client.post(
        "/settings/account/password",
        data={
            "current_password": current,
            "new_password": new,
            "confirm_password": new if confirm is None else confirm,
        },
        follow_redirects=True,
    )


def test_change_password_replaces_the_hash(logged_in, user):
    response = _change(logged_in)

    assert response.status_code == 200
    assert b"Password changed." in response.data
    assert db.session.get(User, user.id).check_password("new-password")


def test_the_new_password_is_what_logs_in_afterwards(app, logged_in, user):
    _change(logged_in)
    logged_in.get("/logout")

    with app.test_client() as fresh:
        rejected = fresh.post(
            "/login", data={"username": "admin", "password": "changeme"},
            follow_redirects=True,
        )
        assert b"Incorrect username or password." in rejected.data

        accepted = fresh.post(
            "/login", data={"username": "admin", "password": "new-password"},
            follow_redirects=True,
        )
        assert b"Incorrect username or password." not in accepted.data


def test_a_wrong_current_password_changes_nothing(logged_in, user):
    response = _change(logged_in, current="not-my-password")

    assert b"isn&#39;t your current password" in response.data
    assert db.session.get(User, user.id).check_password("changeme")


def test_a_mismatched_confirmation_changes_nothing(logged_in, user):
    response = _change(logged_in, new="new-password", confirm="new-passward")

    assert b"don&#39;t match" in response.data
    assert db.session.get(User, user.id).check_password("changeme")


def test_a_short_new_password_is_rejected(logged_in, user):
    """The minlength attribute on the input is a convenience, not the rule —
    it's absent from anything that isn't a browser."""
    response = _change(logged_in, new="short")

    assert b"at least 8 characters" in response.data
    assert db.session.get(User, user.id).check_password("changeme")


def test_reusing_the_current_password_is_rejected(logged_in, user):
    response = _change(logged_in, new="changeme")

    assert b"already your password" in response.data


def test_the_status_message_shows_once(logged_in, user):
    _change(logged_in)

    assert b"Password changed." not in logged_in.get("/settings/account").data


def test_the_change_password_routes_require_login(app, user):
    with app.test_client() as anonymous:
        page = anonymous.get("/settings/account")
        assert page.status_code == 302
        assert "/login" in page.headers["Location"]

        posted = anonymous.post(
            "/settings/account/password",
            data={"current_password": "changeme", "new_password": "new-password",
                  "confirm_password": "new-password"},
        )
        assert posted.status_code == 302
        assert "/login" in posted.headers["Location"]

    assert db.session.get(User, user.id).check_password("changeme")
