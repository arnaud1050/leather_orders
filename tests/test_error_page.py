"""
CO4j: a crashed request gets the app's own error page, not Flask's plain
"Internal Server Error" — which, after a client edit failed on a locked
database, told the studio nothing about whether the save went through.

Flask re-raises instead of calling the 500 handler while TESTING is on, so
each test turns PROPAGATE_EXCEPTIONS off, the way production runs.
"""

import pytest

import app as app_module
from models import db


@pytest.fixture
def crashing(app, monkeypatch):
    """Make a view raise, as the locked-database client edit did."""
    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)
    # The handler rolls the session back, and conftest's fixtures only
    # flush — so without this the signed-in user would vanish with it.
    db.session.commit()

    def crash(endpoint):
        def boom(**kwargs):
            raise RuntimeError("database is locked")
        monkeypatch.setitem(app.view_functions, endpoint, boom)

    return crash


def test_a_crashed_save_says_the_changes_may_not_be_saved(logged_in, crashing):
    crashing("edit_client")
    response = logged_in.post("/clients/1/edit", data={"phone": "555"})

    assert response.status_code == 500
    page = response.get_data(as_text=True)
    assert "Your changes may not have been saved" in page
    assert "Internal Server Error" not in page
    assert 'class="view-switch' in page  # drawn on base.html, nav and all


def test_a_crashed_page_says_it_could_not_be_shown(logged_in, crashing):
    crashing("orders_list")
    response = logged_in.get("/orders")

    assert response.status_code == 500
    page = response.get_data(as_text=True)
    assert "This page couldn't be shown" in page
    assert "may not have been saved" not in page


def test_the_page_still_draws_when_base_html_cannot(logged_in, crashing, monkeypatch):
    """base.html's nav badges query the database — the thing that may have
    failed. The error page must not fail with it."""
    crashing("edit_client")
    real_render = app_module.render_template

    def render(template, **context):
        if context.get("layout") == "base.html":
            raise RuntimeError("database is locked")
        return real_render(template, **context)

    monkeypatch.setattr(app_module, "render_template", render)
    response = logged_in.post("/clients/1/edit", data={"phone": "555"})

    assert response.status_code == 500
    page = response.get_data(as_text=True)
    assert "Your changes may not have been saved" in page
    assert 'class="view-switch' not in page  # the bare layout
