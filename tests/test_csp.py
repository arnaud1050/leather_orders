"""
Content-Security-Policy and self-hosted fonts (REQUIREMENTS CO4q).

The header is checked on each kind of response the app sends (a page, the
sign-in page, an error page, a static file, a kiosk link). The policy's own
contents are pinned so a loosening (`*`, `unsafe-eval`, a new outside host)
has to change this file. And because the policy only allows this origin, a
template that loads anything from another host would silently break in the
browser; the scan below catches that here instead. The e2e suite's fixture
fails any spec whose page reports a violation (e2e/fixtures/auth.fixture.ts).
"""

import re
import subprocess
from pathlib import Path

import app as app_module

ROOT = Path(__file__).resolve().parent.parent
POLICY = app_module.CONTENT_SECURITY_POLICY

# Elements that make the browser *load* something. <a href> to another site
# is just a link, and stays allowed.
EXTERNAL_LOAD = re.compile(
    r'<(?:script|link|img|iframe|source|video|audio|embed|object)\b[^>]*\b(?:src|href)="https?://',
    re.I | re.S)


def _directives():
    return dict(
        (part.split()[0], part.split()[1:]) for part in POLICY.split("; "))


def test_every_kind_of_response_carries_the_policy(app, logged_in):
    responses = {
        "page": logged_in.get("/"),
        "login": app.test_client().get("/login"),
        "404": logged_in.get("/no-such-page"),
        "static": logged_in.get("/static/assets/css/style.css"),
        "kiosk": app.test_client().get("/k/not-a-real-token/"),
    }
    for kind, response in responses.items():
        assert response.headers.get("Content-Security-Policy") == POLICY, kind


def test_the_policy_allows_this_origin_only(app):
    directives = _directives()
    assert directives["default-src"] == ["'self'"]
    assert directives["font-src"] == ["'self'"]
    assert directives["connect-src"] == ["'self'"]
    assert directives["object-src"] == ["'none'"]
    assert directives["base-uri"] == ["'self'"]
    assert directives["frame-ancestors"] == ["'self'"]
    # The one outside host: Google's consent screen, where the Connect
    # form's POST redirects.
    assert directives["form-action"] == ["'self'", "https://accounts.google.com"]
    assert "*" not in POLICY
    assert "unsafe-eval" not in POLICY
    assert "http:" not in POLICY


def test_no_template_loads_anything_from_another_host():
    out = subprocess.check_output(["git", "ls-files", "*.html"], cwd=ROOT, text=True)
    offenders = []
    for name in out.split():
        if name.startswith((".claude/", "e2e/")):
            continue
        text = (ROOT / name).read_text(encoding="utf-8")
        for match in EXTERNAL_LOAD.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            offenders.append(f"{name}:{line}")
    assert not offenders, "loads from another host, which the CSP blocks:\n" + "\n".join(offenders)


def test_inter_is_served_from_this_app(logged_in):
    body = logged_in.get("/").get_data(as_text=True)
    assert "/static/assets/css/fonts.css" in body
    assert "fonts.googleapis.com" not in body
    css = logged_in.get("/static/assets/css/fonts.css").get_data(as_text=True)
    for font in re.findall(r'url\("\.\./fonts/([^"]+)"\)', css):
        response = logged_in.get(f"/static/assets/fonts/{font}")
        assert response.status_code == 200, font
        assert response.data[:4] == b"wOF2", font
    assert (ROOT / "static/assets/fonts/OFL.txt").exists()
