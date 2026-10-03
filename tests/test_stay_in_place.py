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


def test_every_save_message_is_marked_so_it_is_scrolled_to():
    """A message from a save shown above the fold is a refusal nobody sees
    once the page reopens where they were.

    Every save message is drawn by `_save_notice.html` (MOD8), which marks
    it; so what's checked is that the macro does, and that no template
    draws one by hand, where the marker could be forgotten."""
    macro = (ROOT / "templates" / "_save_notice.html").read_text(encoding="utf-8")
    assert "<p data-save-notice" in macro

    by_hand = re.compile(r"{{\s*\w*notice(\.message)?\s*}}")
    for path in templates():
        if path.name == "_save_notice.html":
            continue
        assert not by_hand.search(path.read_text(encoding="utf-8")), (
            f"{path.relative_to(ROOT)} renders a save notice by hand; "
            "use save_notice / notice_slot from _save_notice.html")


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
