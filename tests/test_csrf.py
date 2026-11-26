"""
App-wide CSRF (REQUIREMENTS CO4n): Flask-WTF's `CSRFProtect`, on every
unsafe request into the app, its blueprints and its modules.

Two kinds of test, like `test_save_notices.py`:

- **static** — every POST form in every template carries
  `{{ csrf_token() }}`, and every script `fetch` that posts sends the
  `X-CSRFToken` header. A form without one works in the test suite (which
  runs with CSRF off) and fails for the studio, so this is the test that
  catches it;
- **enforced** — with protection switched back on (`enforce_csrf`), a post
  without a token is a 400 on a route from `app.py`, from `admin/` and from
  modules, and the same post with the token goes through.
"""

import re
import subprocess
from pathlib import Path

import pytest

import app as app_module
from models import SourceOption, db
from tests.conftest import csrf_token_for

ROOT = Path(__file__).resolve().parent.parent

FORM_TAG = re.compile(r'<form\b(?:[^>"\']|"[^"]*"|\'[^\']*\')*>', re.I | re.S)
POSTS = re.compile(r'method\s*=\s*["\']?post', re.I)
FETCH = re.compile(r"\bfetch\(")
POST_METHOD = re.compile(r"method:\s*['\"]POST['\"]", re.I)


def _tracked(pattern):
    """The app's own files: no e2e suite, no skill templates."""
    out = subprocess.check_output(["git", "ls-files", pattern], cwd=ROOT, text=True)
    return [ROOT / p for p in out.split()
            if not p.startswith((".claude/", "e2e/", "node_modules/"))]


def _templates():
    return _tracked("*.html")


def test_the_scan_sees_the_templates():
    """Guard against the scan below passing because it read nothing."""
    names = {p.as_posix() for p in _templates()}
    assert any(n.endswith("templates/order_page.html") for n in names)
    assert any(n.endswith("admin/templates/admin_company.html") for n in names)
    assert any(n.endswith("showcase/templates/showcase/settings.html") for n in names)


def test_every_post_form_carries_a_csrf_token():
    missing = []
    for path in _templates():
        text = path.read_text(encoding="utf-8")
        for match in FORM_TAG.finditer(text):
            if not POSTS.search(match.group(0)):
                continue
            end = text.find("</form>", match.end())
            body = text[match.end(): end if end != -1 else len(text)]
            if "csrf_token()" not in body:
                line = text.count("\n", 0, match.start()) + 1
                missing.append(f"{path.relative_to(ROOT).as_posix()}:{line}")
    assert not missing, "POST forms without {{ csrf_token() }}:\n" + "\n".join(missing)


def test_every_script_post_sends_the_csrf_header():
    missing = []
    for path in _templates() + _tracked("*.js"):
        text = path.read_text(encoding="utf-8")
        for match in FETCH.finditer(text):
            # The fetch's own argument list: up to the `})` that closes its
            # options object. Generous enough for every call in the app.
            close = text.find("})", match.end())
            call = text[match.end(): close if close != -1 else match.end() + 600]
            if POST_METHOD.search(call) and "X-CSRFToken" not in call:
                line = text.count("\n", 0, match.start()) + 1
                missing.append(f"{path.relative_to(ROOT).as_posix()}:{line}")
    assert not missing, "fetch() posts without X-CSRFToken:\n" + "\n".join(missing)


def test_base_html_publishes_the_token_for_scripts(logged_in):
    body = logged_in.get("/settings/account").get_data(as_text=True)
    assert re.search(r'<meta name="csrf-token" content="[^"]+">', body)
    assert "window.csrfToken" in body


def test_nothing_is_exempt():
    """No route is exempt today: nothing here is posted to by a machine
    (the showcase website protocol is outbound, kiosk links are GET-only).
    An exemption added later must come with its reason, and this test is
    where that change gets noticed."""
    assert not app_module.csrf._exempt_views
    assert not app_module.csrf._exempt_blueprints


def test_a_token_outlives_the_hour():
    """A page left open all afternoon must still save."""
    assert app_module.app.config["WTF_CSRF_TIME_LIMIT"] is None


# --- enforced -----------------------------------------------------------------

@pytest.fixture
def tenant_posts(client_record):
    """(path, form) pairs from app.py and the modules, each a real save."""
    return [
        (f"/clients/{client_record.id}/edit",
         {"first_name": "Marie", "last_name": "Alarie", "return_to": "/"}),
        ("/inventory/new", {"name": "Veg-tan shoulder"}),
        ("/integrations/sync-settings", {"sync_enabled": "on"}),
    ]


def test_tenant_posts_without_a_token_are_refused(logged_in, enforce_csrf, tenant_posts):
    for path, form in tenant_posts:
        response = logged_in.post(path, data=form)
        assert response.status_code == 400, path
        assert "This page has expired" in response.get_data(as_text=True)


def test_tenant_posts_with_a_token_go_through(logged_in, enforce_csrf, tenant_posts):
    token = csrf_token_for(logged_in)
    for path, form in tenant_posts:
        response = logged_in.post(path, data={**form, "csrf_token": token})
        assert response.status_code == 302, path


def test_a_wrong_token_is_refused(logged_in, enforce_csrf, client_record):
    token = csrf_token_for(logged_in)
    response = logged_in.post(f"/clients/{client_record.id}/edit",
                              data={"first_name": "X", "csrf_token": token[:-2] + "xx"})
    assert response.status_code == 400


def test_another_sessions_token_is_refused(app, logged_in, enforce_csrf, client_record):
    with app.test_client() as stranger:
        theirs = csrf_token_for(stranger, "/login")
    response = logged_in.post(f"/clients/{client_record.id}/edit",
                              data={"first_name": "X", "csrf_token": theirs})
    assert response.status_code == 400


def test_an_admin_post_without_a_token_is_refused(admin_client, enforce_csrf):
    form = {"name": "Forged Studio", "admin_email": "forged@example.com",
            "admin_password": "changeme1", "admin_full_name": "Forged"}
    assert admin_client.post("/admin/companies", data=form).status_code == 400
    token = csrf_token_for(admin_client, "/admin/companies")
    assert admin_client.post("/admin/companies",
                             data={**form, "csrf_token": token}).status_code == 302


def test_a_script_post_needs_the_header(logged_in, enforce_csrf, company):
    first = SourceOption(company_id=company.id, label="Instagram", sort_order=0)
    second = SourceOption(company_id=company.id, label="Referral", sort_order=1)
    db.session.add_all([first, second])
    db.session.commit()
    payload = {"order": [second.id, first.id]}

    assert logged_in.post("/settings/sources/reorder", json=payload).status_code == 400
    assert db.session.get(SourceOption, first.id).sort_order == 0

    token = csrf_token_for(logged_in)
    response = logged_in.post("/settings/sources/reorder", json=payload,
                              headers={"X-CSRFToken": token})
    assert response.status_code < 400
    db.session.expire_all()
    assert db.session.get(SourceOption, first.id).sort_order == 1


def test_signing_in_needs_a_token_too(app, user, enforce_csrf):
    """Login CSRF: a forged sign-in would put the studio in a session the
    attacker chose."""
    with app.test_client() as client:
        form = {"email": "admin@example.com", "password": "changeme"}
        assert client.post("/login", data=form).status_code == 400
        token = csrf_token_for(client, "/login")
        assert client.post("/login", data={**form, "csrf_token": token}).status_code == 302


def test_get_requests_need_no_token(logged_in, enforce_csrf):
    assert logged_in.get("/").status_code == 200
