"""
Saving keeps your place (REQUIREMENTS.md MOD7, CLAUDE.md hard rule 17).

The behaviour itself is a script (`static/assets/js/stay-in-place.js`) and
was checked in a browser; what the suite can hold in place is the wiring it
depends on, which is exactly what a new page or form would forget:

- the script is on every page, through the base layout;
- every message a save puts on a page is marked [data-save-notice], so the
  script scrolls to it instead of leaving it off-screen;
- no form is submitted with form.submit(), which skips the submit event
  the script listens for.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE_DIRS = ["templates", "billing", "communications", "ai", "documents",
                 "inventory", "admin"]


def templates():
    for directory in TEMPLATE_DIRS:
        yield from sorted((ROOT / directory).rglob("*.html"))


def test_every_page_loads_the_script(logged_in):
    for path in ("/settings/invoicing", "/settings/orders", "/invoices", "/"):
        body = logged_in.get(path, follow_redirects=True).get_data(as_text=True)
        assert "assets/js/stay-in-place.js" in body, path


def test_the_script_is_served(app):
    response = app.test_client().get("/static/assets/js/stay-in-place.js")

    assert response.status_code == 200
    assert b"data-save-notice" in response.data


# The flash-style values a save leaves for the next page to show.
SAVE_MESSAGES = re.compile(
    r"{%\s*if\s+(\w*notice|password_status|signature_saved)\s*%}\s*<p([^>]*)>")


def test_every_save_message_is_marked_so_it_is_scrolled_to():
    """A message from a save shown above the fold is a refusal nobody sees
    once the page reopens where they were."""
    found = 0
    for path in templates():
        for match in SAVE_MESSAGES.finditer(path.read_text(encoding="utf-8")):
            found += 1
            assert "data-save-notice" in match.group(2), (
                f"{path.relative_to(ROOT)}: the {match.group(1)} message needs "
                "data-save-notice (see stay-in-place.js)")
    assert found >= 15, "the scan matched too little to mean anything"


def test_no_form_is_submitted_without_its_submit_event():
    """form.submit() skips the submit event, so the page would come back at
    the top. requestSubmit() fires it."""
    sources = list(templates()) + sorted((ROOT / "static" / "assets" / "js").glob("*.js"))
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(?<!request)\.submit\(\)", text), (
            f"{path.relative_to(ROOT)} calls .submit(); use .requestSubmit()")


def test_the_payment_instructions_box_is_six_lines_tall(logged_in, company):
    body = logged_in.get("/settings/invoicing").get_data(as_text=True)

    assert '<textarea name="payment_instructions" rows="6"' in body
