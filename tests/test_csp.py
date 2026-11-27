"""
Content-Security-Policy, script nonces and self-hosted fonts (REQUIREMENTS
CO4q, CO4r).

The header is checked on each kind of response the app sends (a page, the
sign-in page, an error page, a static file, a kiosk link). The policy's own
contents are pinned so a loosening (`*`, `unsafe-eval`, `unsafe-inline` for
scripts, a new outside host) has to change this file. A page's inline
scripts must carry the nonce its own header allows, a fresh one per
request. And because the browser blocks silently, three template scans
catch here what would otherwise only show as a dead button: an inline
<script> without the nonce, an on*= handler attribute, and anything loaded
from another host. The e2e suite's fixture fails any spec whose page
reports a violation (e2e/fixtures/auth.fixture.ts).
"""

import re
import subprocess
from pathlib import Path

import app as app_module

ROOT = Path(__file__).resolve().parent.parent

CSP_NONCE = re.compile(r"script-src 'self' 'nonce-([A-Za-z0-9_-]+)'")
BODY_NONCE = re.compile(r'<script nonce="([^"]+)"')
INLINE_SCRIPT = re.compile(r"<script\b(?![^>]*\bsrc=)[^>]*>", re.I)
HANDLER_ATTR = re.compile(r"""<[a-z][^>]*\son[a-z]+\s*=\s*["']""", re.I | re.S)

# Elements that make the browser *load* something. <a href> to another site
# is just a link, and stays allowed.
EXTERNAL_LOAD = re.compile(
    r'<(?:script|link|img|iframe|source|video|audio|embed|object)\b[^>]*\b(?:src|href)="https?://',
    re.I | re.S)


def _directives(policy=None):
    policy = policy or app_module.content_security_policy()
    return dict(
        (part.split()[0], part.split()[1:]) for part in policy.split("; "))


def _templates():
    out = subprocess.check_output(["git", "ls-files", "*.html"], cwd=ROOT, text=True)
    return [n for n in out.split() if not n.startswith((".claude/", "e2e/"))]


def _line(text, match):
    return text.count("\n", 0, match.start()) + 1


# --- the header -----------------------------------------------------------------

def test_every_kind_of_response_carries_the_policy(app, logged_in):
    responses = {
        "page": logged_in.get("/"),
        "login": app.test_client().get("/login"),
        "404": logged_in.get("/no-such-page"),
        "static": logged_in.get("/static/assets/css/style.css"),
        "kiosk": app.test_client().get("/k/not-a-real-token/"),
    }
    for kind, response in responses.items():
        policy = response.headers.get("Content-Security-Policy", "")
        assert policy.startswith("default-src 'self'"), kind
        assert "'unsafe-inline'" not in _directives(policy)["script-src"], kind


def test_the_policy_allows_this_origin_only():
    directives = _directives()
    assert directives["default-src"] == ["'self'"]
    assert directives["script-src"] == ["'self'"]
    assert _directives(app_module.content_security_policy("abc"))["script-src"] == [
        "'self'", "'nonce-abc'"]
    assert directives["font-src"] == ["'self'"]
    assert directives["connect-src"] == ["'self'"]
    assert directives["object-src"] == ["'none'"]
    assert directives["base-uri"] == ["'self'"]
    assert directives["frame-ancestors"] == ["'self'"]
    # The one outside host: Google's consent screen, where the Connect
    # form's POST redirects.
    assert directives["form-action"] == ["'self'", "https://accounts.google.com"]
    policy = app_module.content_security_policy("abc")
    assert "*" not in policy
    assert "unsafe-eval" not in policy
    assert "http:" not in policy


# --- nonces ---------------------------------------------------------------------

def test_a_page_s_scripts_carry_the_nonce_its_policy_allows(logged_in):
    response = logged_in.get("/")
    allowed = CSP_NONCE.search(response.headers["Content-Security-Policy"]).group(1)
    used = set(BODY_NONCE.findall(response.get_data(as_text=True)))
    assert used == {allowed}


def test_the_nonce_is_new_on_every_request(logged_in):
    first = CSP_NONCE.search(logged_in.get("/").headers["Content-Security-Policy"]).group(1)
    second = CSP_NONCE.search(logged_in.get("/").headers["Content-Security-Policy"]).group(1)
    assert first != second
    assert len(first) >= 22


def test_a_response_with_no_inline_script_allows_none(logged_in):
    policy = logged_in.get("/static/assets/css/style.css").headers["Content-Security-Policy"]
    assert _directives(policy)["script-src"] == ["'self'"]


# --- template scans --------------------------------------------------------------

def test_every_inline_script_carries_the_nonce():
    missing = []
    for name in _templates():
        text = (ROOT / name).read_text(encoding="utf-8")
        for match in INLINE_SCRIPT.finditer(text):
            if 'nonce="{{ csp_nonce() }}"' not in match.group(0):
                missing.append(f"{name}:{_line(text, match)}")
    assert not missing, "inline <script> without the nonce (blocked):\n" + "\n".join(missing)


def test_no_inline_event_handlers_or_javascript_urls():
    """The policy runs no on*= attribute and no javascript: URL, nonce or
    not: data-confirm / data-action="back" (page-actions.js) do their jobs."""
    offenders = []
    for name in _templates():
        text = (ROOT / name).read_text(encoding="utf-8")
        for match in HANDLER_ATTR.finditer(text):
            offenders.append(f"{name}:{_line(text, match)}")
        if re.search(r'href="javascript:', text, re.I):
            offenders.append(f"{name}: javascript: URL")
    assert not offenders, "inline handlers (blocked by the CSP):\n" + "\n".join(offenders)


def test_both_layouts_load_page_actions(app, logged_in):
    assert "/static/assets/js/page-actions.js" in logged_in.get("/").get_data(as_text=True)
    bare = (ROOT / "templates/errors/_bare.html").read_text(encoding="utf-8")
    assert "assets/js/page-actions.js" in bare
    script = (ROOT / "static/assets/js/page-actions.js").read_text(encoding="utf-8")
    assert "data-confirm" in script and 'data-action="back"' in script


def test_no_template_loads_anything_from_another_host():
    offenders = []
    for name in _templates():
        text = (ROOT / name).read_text(encoding="utf-8")
        for match in EXTERNAL_LOAD.finditer(text):
            offenders.append(f"{name}:{_line(text, match)}")
    assert not offenders, "loads from another host, which the CSP blocks:\n" + "\n".join(offenders)


# --- fonts -------------------------------------------------------------------------

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
