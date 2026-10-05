"""
Feature-usage events (usage/REQUIREMENTS.md, US1–US12) and the admin page
that reads them (admin/REQUIREMENTS.md, PA31–PA33).

The fixtures in conftest.py only `flush()`, which leaves the test session
holding SQLite's write lock — and the event writer is *designed* to give up
on a held lock rather than wait (US3). So every test here commits its
fixtures before making requests; `committed` does that.
"""

import ast
import json
import pathlib
import sqlite3
import time
from datetime import timedelta

import pytest

from models import Client, Company, Order, User, db
from usage import EVENTS, track
from usage import store as usage_store
from usage.store import UsageEvent

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULES = ["ai", "billing", "communications", "documents", "inventory"]


@pytest.fixture
def committed(app, user, company):
    db.session.commit()
    return user


@pytest.fixture
def tenant(app, committed):
    with app.test_client() as test_client:
        test_client.post("/login", data={"email": "admin@example.com", "password": "changeme"})
        yield test_client


@pytest.fixture
def staff(app, platform_admin, committed):
    db.session.commit()
    with app.test_client() as test_client:
        test_client.post("/login", data={"email": "platform@example.com", "password": "changeme"})
        yield test_client


def events(name=None):
    db.session.expire_all()
    query = UsageEvent.query.order_by(UsageEvent.id)
    if name is not None:
        query = query.filter_by(event=name)
    return query.all()


def props(row):
    return json.loads(row.props) if row.props else {}


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def test_sign_in_is_recorded_against_the_user_and_company(tenant, committed, company):
    [row] = events("auth.login")
    assert row.user_id == committed.id
    assert row.company_id == company.id


def test_a_successful_action_is_recorded_with_its_props(tenant):
    """US7: props are the small enums the catalog promises."""
    tenant.post("/clients/new", data={"first_name": "Ana", "last_name": "Roy"})
    [row] = events("client.created")
    assert props(row) == {"via": "form"}


def test_a_refused_action_is_not_recorded(tenant):
    """US2: a save that failed its checks is not a use of the feature."""
    response = tenant.post("/orders/new", data={"client_id": "new", "item": ""})
    assert response.status_code == 400
    assert events("order.created") == []
    assert events("client.created") == []


def test_order_edits_say_where_they_came_from_and_record_status_moves(tenant, order):
    db.session.commit()
    form = {"item": order.item, "start": "2026-07-01", "due": "2026-07-15",
            "status": "ready", "client_id": str(order.client_id)}
    tenant.post(f"/orders/{order.id}/edit", data=form)
    assert [props(r) for r in events("order.updated")] == [{"via": "modal"}]
    assert [props(r) for r in events("order.status_changed")] == [
        {"from": "confirmed", "to": "ready"}]


def test_a_payment_records_its_method_but_never_its_amount(tenant, order):
    db.session.commit()
    tenant.post(f"/orders/{order.id}/payments",
                data={"amount": "120", "paid_date": "2026-07-02", "method": "etransfer"})
    [row] = events("order.payment_recorded")
    assert props(row) == {"method": "etransfer"}


def test_settings_changes_name_their_section(tenant):
    tenant.post("/settings/order-types", data={"label": "Wallets"})
    assert [props(r) for r in events("settings.changed")] == [{"section": "order_types"}]


def test_a_module_route_records_through_the_same_queue(tenant, order):
    """Modules call `usage.track` too — billing here."""
    db.session.commit()
    tenant.post(f"/subjects/{order.id}/invoice")
    assert len(events("invoice.created")) == 1


# ---------------------------------------------------------------------------
# Page views
# ---------------------------------------------------------------------------

def test_a_view_counts_once_per_person_per_day(tenant):
    """US5."""
    tenant.get("/orders")
    tenant.get("/orders")
    tenant.get("/orders")
    assert len(events("view.orders_list")) == 1


def test_a_view_with_different_props_counts_separately(tenant):
    tenant.get("/calendar")
    tenant.get("/week")
    tenant.get("/calendar")
    assert sorted(props(r)["mode"] for r in events("view.calendar")) == ["month", "week"]


def test_a_view_counts_again_the_next_day(tenant):
    tenant.get("/orders")
    row = events("view.orders_list")[0]
    row.created_at -= timedelta(days=1)
    db.session.commit()
    tenant.get("/orders")
    assert len(events("view.orders_list")) == 2


def test_a_redirect_is_not_a_view(tenant):
    """Only a 200 is a page shown. `/` redirects nowhere here, but an
    unmapped endpoint must record nothing at all."""
    tenant.get("/")
    assert [r.event for r in events() if r.event.startswith("view.")] == []


# ---------------------------------------------------------------------------
# Who counts
# ---------------------------------------------------------------------------

def test_platform_staff_are_never_counted(staff):
    """US4."""
    staff.get("/admin/companies")
    assert events() == [], [r.event for r in events()]


def test_an_impersonated_session_is_never_counted(staff, committed):
    """US4: it's us looking, not the studio using."""
    staff.post(f"/admin/users/{committed.id}/impersonate")
    staff.post("/clients/new", data={"first_name": "Ana", "last_name": "Roy"})
    staff.get("/orders")
    assert Client.query.filter_by(first_name="Ana").count() == 1
    assert events() == []


def test_track_outside_a_request_is_a_no_op(app):
    """A scheduler job is not a person using a feature."""
    track("mail.manual_sync", scope="mail")  # must not raise


def test_an_unknown_event_is_dropped_not_raised(tenant, app):
    with app.test_request_context("/"):
        track("no.such.event")  # must not raise


def test_props_that_are_not_plain_values_are_dropped(tenant, app):
    """US7: a stray object can't end up serialised into the table."""
    from flask import g

    with app.test_request_context("/"):
        track("order.created", ok="yes", bad=object(), also_bad=1.5)
        assert g.usage_events == [("order.created", {"ok": "yes"})]


# ---------------------------------------------------------------------------
# Failure isolation — the action never pays for a broken event write
# ---------------------------------------------------------------------------

def test_a_crashing_writer_does_not_fail_the_action(tenant, monkeypatch):
    """US1."""
    def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(usage_store, "_write", boom)
    response = tenant.post("/clients/new", data={"first_name": "Ana", "last_name": "Roy"})
    assert response.status_code == 302
    assert Client.query.filter_by(first_name="Ana").count() == 1


def test_a_missing_table_does_not_fail_the_action(tenant):
    """US1, with a real database error rather than a patched one."""
    UsageEvent.__table__.drop(db.engine)
    try:
        response = tenant.post("/clients/new", data={"first_name": "Ana", "last_name": "Roy"})
        assert response.status_code == 302
        assert Client.query.filter_by(first_name="Ana").count() == 1
        assert tenant.get("/orders").status_code == 200
    finally:
        UsageEvent.__table__.create(db.engine)


def test_a_locked_database_costs_the_request_a_fraction_of_a_second(tenant, app):
    """US3: SQLite's default busy wait is five seconds — added to someone's
    click to record that they clicked. The event write gives up much sooner."""
    path = app.config["SQLALCHEMY_DATABASE_URI"].removeprefix("sqlite:///")
    holder = sqlite3.connect(path, timeout=0)
    holder.execute("BEGIN IMMEDIATE")  # another writer holds the lock
    try:
        started = time.monotonic()
        response = tenant.get("/orders")
        elapsed = time.monotonic() - started
    finally:
        holder.rollback()
        holder.close()
    assert response.status_code == 200
    assert elapsed < 2, f"request waited {elapsed:.1f}s on the usage write"
    assert events("view.orders_list") == []


def test_the_short_lock_wait_is_put_back_on_the_pooled_connection(app):
    """US3: the connection returns to the pool; the next ordinary query on
    it must get the normal wait, not ours."""
    with db.engine.connect() as conn:
        before = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
        restore = usage_store._limit_lock_wait(conn)
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == usage_store.LOCK_WAIT_MS
        restore()
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == before


def test_an_uncommitted_action_records_nothing_and_waits_for_nothing(logged_in):
    """US3a: conftest's fixtures only flush, so this session still holds
    its own write. The action isn't durable, and waiting on our own lock
    would cost every request `LOCK_WAIT_MS` for nothing."""
    started = time.monotonic()
    for _ in range(8):
        assert logged_in.get("/orders").status_code == 200
    elapsed = time.monotonic() - started
    assert elapsed < 8 * usage_store.LOCK_WAIT_MS / 1000, f"{elapsed:.2f}s"
    db.session.commit()
    assert events() == []


def test_an_error_response_records_nothing(tenant):
    """US2: a handler that queued an event and then failed didn't deliver."""
    assert tenant.get("/orders/999999").status_code == 404
    assert tenant.post("/orders/999999/rush").status_code == 404
    assert [r.event for r in events() if r.event != "auth.login"] == []


# ---------------------------------------------------------------------------
# The catalog and the boundary — read from source
# ---------------------------------------------------------------------------

def _python_sources():
    for path in ROOT.rglob("*.py"):
        parts = path.relative_to(ROOT).parts
        if parts[0] in {"tests", "e2e", ".venv", "venv", "scripts"}:
            continue
        yield path


def _tracked_names():
    """Every literal first argument to `track(...)` in the app's source."""
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "track" and node.args):
                arg = node.args[0]
                if isinstance(arg, ast.Constant):
                    yield path, arg.value


def test_every_tracked_name_is_in_the_catalog():
    """US9: an unknown name is dropped at runtime, so a typo would otherwise
    vanish silently."""
    for path, name in _tracked_names():
        assert name in EVENTS, f"{path.name} tracks {name!r}, which isn't in usage.EVENTS"


def test_every_catalog_entry_is_emitted_somewhere():
    """US9: a catalog row nothing emits is a permanent zero on the admin
    page that looks like a finding."""
    emitted = {name for _, name in _tracked_names()}
    emitted |= {event for event, _ in usage_store.VIEW_ENDPOINTS.values()}
    # `lead.dismissed` and `lead.trashed` pass through _thread_action's
    # `event=` keyword rather than a literal first argument.
    source = (ROOT / "communications" / "routes.py").read_text(encoding="utf-8")
    emitted |= {name for name in EVENTS if f'event="{name}"' in source}
    assert set(EVENTS) - emitted == set()


def test_view_endpoints_all_exist(app):
    """A renamed route would silently stop being counted."""
    endpoints = set(app.view_functions)
    for endpoint in usage_store.VIEW_ENDPOINTS:
        assert endpoint in endpoints, endpoint


@pytest.mark.parametrize("module", MODULES)
def test_modules_import_only_the_dependency_free_half(module):
    """US10: `from usage import track` is allowed in a module; `usage.store`
    (which knows Company, User and impersonation) is not."""
    for path in (ROOT / module).rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("usage"):
                assert node.module == "usage", f"{path} imports {node.module}"
                assert {a.name for a in node.names} == {"track"}, path
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("usage"), path


def test_the_shared_half_drags_nothing_in_behind_it():
    """US10: the reason modules may import it at all."""
    tree = ast.parse((ROOT / "usage" / "__init__.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module in {"flask"}, node.module
        elif isinstance(node, ast.Import):
            assert all(a.name in {"logging"} for a in node.names)


# ---------------------------------------------------------------------------
# /admin/usage
# ---------------------------------------------------------------------------

def _seed(company_id, user_id, event, days_ago=0, **props):
    db.session.add(UsageEvent(
        company_id=company_id, user_id=user_id, event=event,
        props=json.dumps(props, sort_keys=True) if props else None,
        created_at=usage_store.utcnow() - timedelta(days=days_ago),
    ))


def test_the_usage_page_is_closed_to_tenants(tenant):
    """PA31: same guard as every other /admin page."""
    assert tenant.get("/admin/usage").status_code == 403


def test_the_usage_page_opens_for_staff(staff):
    response = staff.get("/admin/usage")
    assert response.status_code == 200
    assert 'href="/admin/usage" class="settings-nav__link is-active"' in response.get_data(as_text=True)


def test_the_report_counts_companies_people_and_uses(app, company, other_company, committed):
    other_user = User(company_id=other_company.id, email="o@example.com")
    other_user.set_password("changeme")
    db.session.add(other_user)
    db.session.flush()
    for _ in range(5):
        _seed(company.id, committed.id, "invoice.created")
    _seed(other_company.id, other_user.id, "invoice.created")
    _seed(company.id, committed.id, "order.updated", via="modal")
    _seed(company.id, committed.id, "order.updated", via="page")
    _seed(company.id, committed.id, "order.updated", via="modal")
    db.session.commit()

    report = {row["event"]: row for row in usage_store.feature_report(None)}
    assert (report["invoice.created"]["companies"], report["invoice.created"]["users"],
            report["invoice.created"]["uses"]) == (2, 2, 6)
    assert report["order.updated"]["breakdown"] == [("via: modal", 2), ("via: page", 1)]
    # PA32: a feature nobody used is still a row — it's an answer too.
    assert report["ai.render_saved"]["uses"] == 0

    only_ours = {row["event"]: row for row in usage_store.feature_report(None, other_company.id)}
    assert only_ours["invoice.created"]["uses"] == 1
    assert only_ours["order.updated"]["uses"] == 0


def test_the_period_filter_excludes_older_events(app, company, committed):
    _seed(company.id, committed.id, "mail.sent", days_ago=100)
    _seed(company.id, committed.id, "mail.sent", days_ago=1)
    db.session.commit()
    recent = {r["event"]: r for r in usage_store.feature_report(usage_store.period_start("90"))}
    everything = {r["event"]: r for r in usage_store.feature_report(usage_store.period_start("all"))}
    assert recent["mail.sent"]["uses"] == 1
    assert everything["mail.sent"]["uses"] == 2


def test_the_company_table_lists_silent_tenants_too(app, company, other_company, committed):
    _seed(company.id, committed.id, "auth.login")
    _seed(company.id, committed.id, "order.created")
    db.session.commit()
    rows = {r["name"]: r for r in usage_store.company_report(None)}
    assert rows["By Monsieur"]["logins"] == 1
    assert rows["By Monsieur"]["features"] == 2
    assert rows["Other Studio"]["events"] == 0


def test_the_page_shows_counts_never_content(staff, company, committed):
    """PA33: the page names features and companies, never a studio's
    clients or orders — that would break PA0.2."""
    db.session.add(Client(company_id=company.id, first_name="Secretive", last_name="Client"))
    _seed(company.id, committed.id, "client.created", via="form")
    db.session.commit()
    page = staff.get("/admin/usage").get_data(as_text=True)
    assert "Client created" in page
    assert "Secretive" not in page

    one = staff.get(f"/admin/usage?company={company.id}&period=30").get_data(as_text=True)
    assert "Feature usage at By Monsieur" in one
    assert 'id="usage-companies"' not in one


def test_an_unknown_filter_falls_back_rather_than_failing(staff):
    response = staff.get("/admin/usage?period=7&company=nope")
    assert response.status_code == 200
    assert "across every company" in response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Excluding a company (US4a, PA31a)
# ---------------------------------------------------------------------------

def test_an_excluded_company_is_left_out_of_every_figure(app, company, other_company, committed):
    other_company.exclude_from_usage = True
    other_user = User(company_id=other_company.id, email="demo@example.com")
    other_user.set_password("changeme")
    db.session.add(other_user)
    db.session.flush()
    _seed(company.id, committed.id, "invoice.created")
    _seed(other_company.id, other_user.id, "invoice.created")
    _seed(other_company.id, other_user.id, "mail.sent")
    db.session.commit()

    report = {r["event"]: r for r in usage_store.feature_report(None)}
    assert (report["invoice.created"]["companies"], report["invoice.created"]["uses"]) == (1, 1)
    assert report["mail.sent"]["uses"] == 0
    assert [r["name"] for r in usage_store.company_report(None)] == ["By Monsieur"]


def test_an_excluded_company_is_still_recorded(app, company, committed):
    """US4a: excluding hides, it doesn't stop recording — so un-excluding
    brings the history straight back."""
    company.exclude_from_usage = True
    db.session.commit()
    with app.test_client() as test_client:
        test_client.post("/login", data={"email": "admin@example.com", "password": "changeme"})
    assert len(events("auth.login")) == 1
    assert {r["event"]: r for r in usage_store.feature_report(None)}["auth.login"]["uses"] == 0

    company.exclude_from_usage = False
    db.session.commit()
    assert {r["event"]: r for r in usage_store.feature_report(None)}["auth.login"]["uses"] == 1


def test_an_excluded_company_is_not_offered_in_the_dropdown(staff, other_company):
    other_company.exclude_from_usage = True
    db.session.commit()
    page = staff.get("/admin/usage").get_data(as_text=True)
    assert f'<option value="{other_company.id}"' not in page
    assert "By Monsieur" in page

    # A hand-typed URL for it falls back to the all-companies view.
    page = staff.get(f"/admin/usage?company={other_company.id}").get_data(as_text=True)
    assert "across every company" in page


def test_the_flag_is_set_when_creating_a_company(staff):
    response = staff.post("/admin/companies", data={
        "name": "Demo Studio", "timezone": "America/Vancouver",
        "admin_email": "demo-admin@example.com", "admin_password": "longenough1",
        "admin_full_name": "Demo", "exclude_from_usage": "1",
    })
    assert response.status_code == 302
    assert Company.query.filter_by(name="Demo Studio").one().exclude_from_usage is True


def test_a_new_company_counts_by_default(staff):
    staff.post("/admin/companies", data={
        "name": "Real Studio", "timezone": "America/Vancouver",
        "admin_email": "real@example.com", "admin_password": "longenough1",
    })
    assert Company.query.filter_by(name="Real Studio").one().exclude_from_usage is False


def test_the_flag_is_toggled_from_the_company_page(staff, company):
    url = f"/admin/companies/{company.id}"
    staff.post(url, data={"name": company.name, "timezone": company.timezone,
                          "exclude_from_usage_shown": "1", "exclude_from_usage": "1"})
    db.session.expire_all()
    assert db.session.get(Company, company.id).exclude_from_usage is True
    assert 'name="exclude_from_usage" value="1" checked' in staff.get(url).get_data(as_text=True)

    # Unticked box: the marker is there, the value isn't.
    staff.post(url, data={"name": company.name, "timezone": company.timezone,
                          "exclude_from_usage_shown": "1"})
    db.session.expire_all()
    assert db.session.get(Company, company.id).exclude_from_usage is False


def test_a_form_without_the_box_leaves_the_flag_alone(staff, company):
    """Hard rule 9: absent means "leave it alone", not "untick"."""
    company.exclude_from_usage = True
    db.session.commit()
    staff.post(f"/admin/companies/{company.id}",
               data={"name": "Renamed", "timezone": company.timezone})
    db.session.expire_all()
    row = db.session.get(Company, company.id)
    assert (row.name, row.exclude_from_usage) == ("Renamed", True)
