"""
Real costs per order â€” costing/REQUIREMENTS.md (RC*).

The rules this file defends: a margin is derived on every read and never
stored; revenue is what billing says the order is worth before tax â€” the
frozen invoice figure once it's out, never the lines re-added; a rate change
doesn't rewrite time already logged; the calculator's price reaches the
order only as a new line, never by editing one; and nothing here crosses a
tenant, or reaches Showcase.
"""

import ast
import pathlib
from datetime import date

import pytest

from costing import pricing, services
from costing.models import CostingSettings, OrderEstimate, TimeEntry
from inventory import services as inventory_service
from models import Client, Order, OrderLine, OrderType, db
from tests.test_invoicing import draft, issue, registered  # noqa: F401
from tests.test_save_notices import located

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _rate(company, rate=50.0, overhead=10.0, target=30.0):
    services.update_settings(
        company.id, hourly_rate=rate, overhead_percent=overhead,
        target_margin_percent=target)
    db.session.flush()


def _log(company, order, minutes, day=date(2026, 7, 2), user_id=None):
    return services.add_entry(company.id, order.id, user_id=user_id,
                              worked_on=day, minutes=minutes)


def _foreign_order(other_company):
    client = Client(company_id=other_company.id, first_name="Foreign", last_name="Client")
    db.session.add(client)
    db.session.flush()
    row = Order(client_id=client.id, item="Foreign order", start=date(2026, 7, 1),
                due=date(2026, 7, 15), status="confirmed")
    db.session.add(row)
    db.session.flush()
    db.session.add(OrderLine(order_id=row.id, description="x", quantity=1, unit_price=100.0))
    db.session.flush()
    return row


# --- The arithmetic (RC6â€“RC9, RC21) ------------------------------------------

def test_a_breakdown_adds_up():
    b = pricing.breakdown(revenue=1000, material_cost=200, labour_cost=400,
                          hours=8, overhead_percent=10)
    assert b.overhead == 60.0            # 10% of materials + labour
    assert b.cost == 660.0
    assert b.profit == 340.0
    assert b.margin_percent == 34.0
    # (revenue âˆ’ materials âˆ’ overhead) Ã· hours = the rate + profit per hour
    assert b.effective_hourly == pytest.approx(92.5)
    assert b.effective_hourly == pytest.approx(400 / 8 + 340 / 8)


def test_no_hours_means_no_hourly_rate_rather_than_zero():
    b = pricing.breakdown(revenue=500, material_cost=100, labour_cost=0,
                          hours=0, overhead_percent=0)
    assert b.effective_hourly is None
    assert b.margin_percent == 80.0


def test_no_revenue_means_no_margin_rather_than_a_division_error():
    b = pricing.breakdown(revenue=0, material_cost=50, labour_cost=100,
                          hours=2, overhead_percent=0)
    assert b.margin_percent is None
    assert b.profit == -150.0


def test_a_loss_reads_as_a_negative_margin():
    b = pricing.breakdown(revenue=140, material_cost=25, labour_cost=157.5,
                          hours=3.5, overhead_percent=15)
    assert b.profit < 0 and b.margin_percent < 0


def test_the_suggested_price_makes_exactly_the_target_margin():
    s = pricing.suggest_price(hours=10, hourly_rate=45, material_cost=110,
                              overhead_percent=15, target_margin_percent=35)
    assert s.labour_cost == 450.0
    assert s.overhead == 84.0
    assert s.cost == 644.0
    assert s.price == round(644 / 0.65, 2)
    check = pricing.breakdown(revenue=s.price, material_cost=110, labour_cost=450,
                              hours=10, overhead_percent=15)
    assert check.margin_percent == pytest.approx(35.0, abs=0.05)


def test_a_target_margin_is_capped_below_one_hundred_percent():
    s = pricing.suggest_price(hours=1, hourly_rate=100, material_cost=0,
                              overhead_percent=0, target_margin_percent=100)
    assert s.target_margin_percent == pricing.MAX_TARGET_MARGIN
    assert s.price == 2000.0


def test_combined_figures_weigh_big_orders_more():
    big = pricing.breakdown(revenue=900, material_cost=100, labour_cost=200,
                            hours=4, overhead_percent=0)       # 66.7%
    small = pricing.breakdown(revenue=40, material_cost=10, labour_cost=60,
                              hours=1.2, overhead_percent=0)   # -75%
    both = pricing.combine([big, small])
    assert both.revenue == 940.0
    assert both.margin_percent == pytest.approx(570 / 940 * 100, abs=0.1)


def test_pricing_uses_the_standard_library_only():
    """RC2: reusable by a future quote without an app around it."""
    tree = ast.parse((ROOT / "costing" / "pricing.py").read_text(encoding="utf-8"))
    imported = {
        (node.module if isinstance(node, ast.ImportFrom) else alias.name).split(".")[0]
        for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported <= {"dataclasses", "__future__", "math"}


# --- Module boundaries (RC1, RC40) -------------------------------------------

def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, None
        elif isinstance(node, ast.ImportFrom):
            yield node.module or "", tuple(a.name for a in node.names)


ALLOWED_TOP_LEVEL = {
    "costing", "dataclasses", "datetime", "flask", "flask_login", "logging",
    "math", "re", "sqlalchemy", "usage", "models",
}


def test_costing_imports_only_db_from_the_host_and_no_sibling_module():
    for path in sorted((ROOT / "costing").glob("*.py")):
        for module, names in _imports(path):
            top = module.split(".")[0]
            assert top in ALLOWED_TOP_LEVEL, f"{path.name} imports {module}"
            if top == "models":
                assert names == ("db",), f"{path.name} imports {names} from models"
            if top == "usage":
                assert module == "usage" and names == ("track",), f"{path.name}: usage.store is host-only"


def test_showcase_never_reads_costs():
    """RC40: costs are internal; Showcase's website serializer and catalog
    mode must not be able to reach them."""
    for path in sorted((ROOT / "showcase").rglob("*")):
        if path.suffix in (".py", ".html"):
            text = path.read_text(encoding="utf-8")
            assert "costing" not in text, path
            assert "time_entries" not in text and "hourly_rate" not in text, path
    assert "costing" not in (ROOT / "showcase_adapter.py").read_text(encoding="utf-8")


# --- Settings (RC10â€“RC12) ----------------------------------------------------

def test_a_company_that_never_saved_reads_the_defaults_without_a_row(company):
    settings = services.settings_for(company.id)
    assert (settings.hourly_rate, settings.overhead_percent, settings.target_margin_percent) == (0, 0, 30)
    db.session.flush()
    assert CostingSettings.query.count() == 0


def test_settings_out_of_range_are_refused_and_nothing_is_written(company):
    with pytest.raises(ValueError):
        services.update_settings(company.id, hourly_rate=-1)
    with pytest.raises(ValueError):
        services.update_settings(company.id, target_margin_percent=99)
    assert CostingSettings.query.count() == 0


def test_settings_are_per_company(company, other_company):
    _rate(company, rate=60)
    assert services.settings_for(other_company.id).hourly_rate == 0


def test_the_settings_form_saves(logged_in, company):
    body = logged_in.post("/settings/costs", data={
        "hourly_rate": "45", "overhead_percent": "15", "target_margin_percent": "35",
        "notice_section": "costs"}, follow_redirects=True).get_data(as_text=True)
    assert located(body, "Costs settings saved.") == ("costs", "success")
    row = db.session.get(CostingSettings, company.id)
    assert (row.hourly_rate, row.overhead_percent, row.target_margin_percent) == (45, 15, 35)


def test_a_settings_field_the_form_left_out_is_left_alone(logged_in, company):
    """Hard rule 9."""
    _rate(company, rate=50, overhead=10, target=30)
    db.session.commit()
    logged_in.post("/settings/costs", data={"hourly_rate": "70"})
    db.session.expire_all()
    row = db.session.get(CostingSettings, company.id)
    assert (row.hourly_rate, row.overhead_percent, row.target_margin_percent) == (70, 10, 30)


@pytest.mark.parametrize("value, message", [
    ("", "Enter a number"), ("abc", "Enter a number"), ("inf", "Enter a number"),
    ("nan", "Enter a number"), ("-5", "between $0 and $10,000"),
])
def test_a_settings_value_that_isnt_a_usable_number_is_refused(logged_in, company, value, message):
    body = logged_in.post("/settings/costs", data={
        "hourly_rate": value, "notice_section": "costs"},
        follow_redirects=True).get_data(as_text=True)
    assert located(body, message) == ("costs", "error")
    assert CostingSettings.query.count() == 0


# --- Time entries (RC3â€“RC5) --------------------------------------------------

def test_an_entry_keeps_the_rate_it_was_logged_at(company, order):
    """RC4: raising the rate rewrites nothing already logged."""
    _rate(company, rate=40)
    first = _log(company, order, 120)
    _rate(company, rate=80)
    second = _log(company, order, 60)
    assert (first.hourly_rate, second.hourly_rate) == (40, 80)
    costs = services.order_costs(company.id, order.id, revenue=760, material_cost=0)
    assert costs.breakdown.labour_cost == 80 + 80


@pytest.mark.parametrize("minutes", [0, -30, 24 * 60 + 1])
def test_a_duration_that_isnt_one_is_refused(company, order, minutes):
    with pytest.raises(ValueError):
        _log(company, order, minutes)


def test_logging_time_through_the_tab(logged_in, company, order, user):
    _rate(company, rate=50)
    db.session.commit()
    body = logged_in.post(f"/orders/{order.id}/costs/time", data={
        "worked_on": "2026-07-03", "hours": "1.5", "minutes": "20",
        "note": "  Edge   finishing ", "notice_section": "time",
    }, follow_redirects=True).get_data(as_text=True)
    assert located(body, "Time logged.") == ("time", "success")
    [entry] = TimeEntry.query.all()
    assert (entry.minutes, entry.note, entry.user_id, entry.company_id) == (110, "Edge finishing", user.id, company.id)
    assert entry.hourly_rate == 50
    assert "1h 50m" in body


def test_a_missing_minutes_field_is_just_hours(logged_in, order):
    logged_in.post(f"/orders/{order.id}/costs/time", data={"worked_on": "2026-07-03", "hours": "2"})
    assert TimeEntry.query.one().minutes == 120


@pytest.mark.parametrize("form, message", [
    ({"worked_on": "not a date", "hours": "1"}, "Choose the day"),
    ({"worked_on": "2026-07-03"}, "Enter how long"),
    ({"worked_on": "2026-07-03", "hours": "-1"}, "can't be negative"),
    ({"worked_on": "2026-07-03", "hours": "30"}, "24 hours"),
])
def test_a_bad_time_entry_is_refused_in_its_section(logged_in, order, form, message):
    body = logged_in.post(f"/orders/{order.id}/costs/time",
                          data={**form, "notice_section": "time"},
                          follow_redirects=True).get_data(as_text=True)
    assert located(body, message) == ("time", "error")
    assert TimeEntry.query.count() == 0


def test_deleting_an_entry_is_scoped_to_its_order(logged_in, company, order, client_record):
    """RC5: the right entry id under the wrong order does nothing."""
    other = Order(client_id=client_record.id, item="Belt", start=date(2026, 7, 1),
                  due=date(2026, 7, 9), status="confirmed")
    db.session.add(other)
    db.session.flush()
    entry = _log(company, order, 60)
    db.session.commit()
    logged_in.post(f"/orders/{other.id}/costs/time/{entry.id}/delete")
    assert TimeEntry.query.count() == 1
    logged_in.post(f"/orders/{order.id}/costs/time/{entry.id}/delete")
    assert TimeEntry.query.count() == 0


# --- What an order earned (RC6â€“RC9) ------------------------------------------

def test_revenue_is_pre_tax_and_net_of_the_discount(registered, client_record):
    from tests.test_invoicing import make_order

    order = make_order(client_record, 1000.0)
    order.discount_kind, order.discount_value = "percent", 10
    db.session.flush()
    assert order.total > 900          # QC tax on top
    assert order.pre_tax_total == 900.0


def test_an_issued_order_keeps_its_invoiced_revenue_when_lines_change(registered, client_record):
    """RC6: never re-derived from the lines once the invoice is out."""
    from tests.test_invoicing import make_order

    order = make_order(client_record, 1000.0)
    issue(registered, order)
    db.session.add(OrderLine(order_id=order.id, description="Added later",
                             quantity=1, unit_price=500.0))
    db.session.flush()
    db.session.expire(order)
    assert order.subtotal == 1500.0
    assert order.pre_tax_total == 1000.0


def test_a_draft_invoice_still_follows_the_lines(registered, client_record):
    from tests.test_invoicing import make_order

    order = make_order(client_record, 1000.0)
    draft(registered, order)
    db.session.add(OrderLine(order_id=order.id, description="More", quantity=1, unit_price=200.0))
    db.session.flush()
    db.session.expire(order)
    assert order.pre_tax_total == 1200.0


def test_the_tab_shows_the_margin_from_the_frozen_figure(logged_in, registered, client_record):
    from tests.test_invoicing import make_order

    order = make_order(client_record, 1000.0)
    _rate(registered, rate=50, overhead=0, target=30)
    _log(registered, order, 240)                         # $200 of labour
    inventory_service.add_other(registered.id, order.id, "Leather", 100.0)
    issue(registered, order)
    db.session.add(OrderLine(order_id=order.id, description="Later", quantity=1, unit_price=999.0))
    db.session.commit()
    body = logged_in.get(f"/orders/{order.id}/costs").get_data(as_text=True)
    assert "as invoiced" in body
    assert "$700.00" in body          # 1000 âˆ’ 100 âˆ’ 200
    assert "70.0%" in body


def test_an_order_with_no_time_says_its_margin_counts_no_labour(logged_in, order):
    body = logged_in.get(f"/orders/{order.id}/costs").get_data(as_text=True)
    assert "No time logged on this order yet." in body
    assert "counts no labour" in body
    assert "No hourly rate is set yet" in body


def test_underpricing_is_flagged(logged_in, company, order):
    _rate(company, rate=50, overhead=0, target=50)
    _log(company, order, 12 * 60)                         # $600, price $760
    db.session.commit()
    body = logged_in.get(f"/orders/{order.id}/costs").get_data(as_text=True)
    assert "should have been priced at $1,200.00" in body


def test_order_costs_is_derived_on_every_read(company, order):
    """Hard rule 10: nothing stores the answer."""
    _rate(company, rate=50, overhead=0)
    entry = _log(company, order, 60)
    before = services.order_costs(company.id, order.id, revenue=760, material_cost=0)
    entry.minutes = 120
    db.session.flush()
    after = services.order_costs(company.id, order.id, revenue=760, material_cost=0)
    assert (before.breakdown.labour_cost, after.breakdown.labour_cost) == (50, 100)


# --- The calculator (RC20â€“RC23) ----------------------------------------------

def test_saving_an_estimate_shows_the_suggestion(logged_in, company, order):
    _rate(company, rate=50, overhead=0, target=50)
    inventory_service.add_other(company.id, order.id, "Leather", 100.0)
    db.session.commit()
    body = logged_in.post(f"/orders/{order.id}/costs/estimate", data={
        "hours": "8", "material_cost": "", "target_margin_percent": "",
        "notice_section": "pricing"}, follow_redirects=True).get_data(as_text=True)
    assert located(body, "Estimate saved.") == ("pricing", "success")
    estimate = OrderEstimate.query.one()
    assert (estimate.hours, estimate.material_cost, estimate.target_margin_percent) == (8, None, None)
    # (8 Ã— 50 + 100) Ã· (1 âˆ’ 0.5)
    assert "$1,000.00" in body
    assert "Priced $240.00 below the suggestion." in body


def test_an_estimate_field_left_off_the_form_keeps_its_value(logged_in, company, order):
    services.save_estimate(company.id, order.id, hours=5, material_cost=80,
                           target_margin_percent=40)
    db.session.commit()
    logged_in.post(f"/orders/{order.id}/costs/estimate", data={"hours": "6"})
    db.session.expire_all()
    row = OrderEstimate.query.one()
    assert (row.hours, row.material_cost, row.target_margin_percent) == (6, 80, 40)


@pytest.mark.parametrize("form, message", [
    ({"hours": ""}, "Enter the hours"), ({"hours": "-1"}, "between 0 and 10,000"),
    ({"hours": "4", "target_margin_percent": "120"}, "between 0% and 95%"),
    ({"hours": "4", "material_cost": "-3"}, "can't be negative"),
])
def test_a_bad_estimate_is_refused(logged_in, order, form, message):
    body = logged_in.post(f"/orders/{order.id}/costs/estimate",
                          data={**form, "notice_section": "pricing"},
                          follow_redirects=True).get_data(as_text=True)
    assert located(body, message) == ("pricing", "error")
    assert OrderEstimate.query.count() == 0


def test_the_estimate_uses_todays_rate(company, order):
    """RC21: the calculator prices work ahead, at today's rate."""
    _rate(company, rate=40, overhead=0, target=0)
    services.save_estimate(company.id, order.id, hours=2, material_cost=0, target_margin_percent=None)
    _rate(company, rate=60, overhead=0, target=0)
    s = services.estimate_suggestion(company.id, order.id, material_cost_on_order=0)
    assert s.price == 120.0


def test_adding_the_suggestion_adds_a_line_and_edits_none(logged_in, order):
    """RC23: never overwrites a price."""
    [existing] = order.lines
    body = logged_in.post(f"/orders/{order.id}/costs/suggested-line", data={
        "description": "Additional work", "unit_price": "240",
        "notice_section": "pricing"}, follow_redirects=True).get_data(as_text=True)
    assert located(body, "Added") == ("pricing", "success")
    db.session.expire_all()
    lines = db.session.get(Order, order.id).lines
    assert [(l.description, l.unit_price) for l in lines] == [
        (existing.description, 760.0), ("Additional work", 240.0)]


def test_the_suggestion_form_offers_the_difference_when_lines_exist(logged_in, company, order):
    _rate(company, rate=100, overhead=0, target=0)
    services.save_estimate(company.id, order.id, hours=10, material_cost=0, target_margin_percent=None)
    db.session.commit()
    body = logged_in.get(f"/orders/{order.id}/costs").get_data(as_text=True)
    assert 'name="unit_price" min="0.01" step="0.01" required\n      value="240.00"' in body


@pytest.mark.parametrize("form", [
    {"description": "", "unit_price": "10"}, {"description": "x", "unit_price": "0"},
    {"description": "x", "unit_price": "inf"},
])
def test_an_unusable_suggested_line_is_refused(logged_in, order, form):
    logged_in.post(f"/orders/{order.id}/costs/suggested-line", data=form)
    assert OrderLine.query.filter_by(order_id=order.id).count() == 1


def test_the_suggested_line_is_locked_with_the_invoice(logged_in, registered, client_record):
    from tests.test_invoicing import make_order

    order = make_order(client_record, 100.0)
    _rate(registered, rate=100, overhead=0, target=0)
    services.save_estimate(registered.id, order.id, hours=5, material_cost=0, target_margin_percent=None)
    issue(registered, order)
    db.session.commit()
    body = logged_in.get(f"/orders/{order.id}/costs").get_data(as_text=True)
    assert "Add as a line item" not in body
    body = logged_in.post(f"/orders/{order.id}/costs/suggested-line", data={
        "description": "More", "unit_price": "400", "notice_section": "pricing"},
        follow_redirects=True).get_data(as_text=True)
    assert located(body, "can't change")[0] == "pricing"
    assert OrderLine.query.filter_by(order_id=order.id).count() == 1


# --- Tenancy and sign-in (RC41) ----------------------------------------------

def test_another_tenants_order_is_not_found(logged_in, other_company):
    foreign = _foreign_order(other_company)
    db.session.commit()
    assert logged_in.get(f"/orders/{foreign.id}/costs").status_code == 404
    for url in (f"/orders/{foreign.id}/costs/time", f"/orders/{foreign.id}/costs/estimate",
                f"/orders/{foreign.id}/costs/suggested-line"):
        assert logged_in.post(url, data={"worked_on": "2026-07-03", "hours": "2",
                                         "description": "x", "unit_price": "5"}).status_code == 404
    assert TimeEntry.query.count() == 0 and OrderEstimate.query.count() == 0


def test_labour_by_order_is_scoped_to_the_company(company, other_company, order):
    foreign = _foreign_order(other_company)
    _log(company, order, 60)
    services.add_entry(other_company.id, foreign.id, user_id=None,
                       worked_on=date(2026, 7, 2), minutes=90)
    assert set(services.labour_by_order(company.id)) == {order.id}


@pytest.mark.parametrize("method, url", [
    ("get", "/orders/1/costs"), ("get", "/settings/costs"),
    ("post", "/orders/1/costs/time"), ("post", "/orders/1/costs/time/1/delete"),
    ("post", "/orders/1/costs/estimate"), ("post", "/orders/1/costs/suggested-line"),
    ("post", "/settings/costs"),
])
def test_every_costs_route_needs_sign_in(app, method, url):
    with app.test_client() as anonymous:
        response = getattr(anonymous, method)(url)
    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_deleting_an_order_takes_its_time_with_it(logged_in, company, client_record):
    tentative = Order(client_id=client_record.id, item="Consult", start=date(2026, 7, 1),
                      due=date(2026, 7, 2), status="tentative")
    db.session.add(tentative)
    db.session.flush()
    _log(company, tentative, 60)
    services.save_estimate(company.id, tentative.id, hours=3, material_cost=None,
                           target_margin_percent=None)
    db.session.commit()
    logged_in.post(f"/orders/{tentative.id}/delete")
    assert TimeEntry.query.count() == 0 and OrderEstimate.query.count() == 0


# --- Analytics (RC30â€“RC33) ---------------------------------------------------

def _this_year_order(client_record, price, order_type=None, status="confirmed"):
    today = date.today()
    row = Order(client_id=client_record.id, item="Piece", start=today, due=today,
                status=status, order_type_id=order_type.id if order_type else None)
    db.session.add(row)
    db.session.flush()
    db.session.add(OrderLine(order_id=row.id, description="Piece", quantity=1, unit_price=price))
    db.session.flush()
    return row


def test_analytics_counts_only_orders_with_time(logged_in, company, client_record):
    bags = OrderType(company_id=company.id, label="Bags", sort_order=0)
    db.session.add(bags)
    db.session.flush()
    _rate(company, rate=50, overhead=0)
    timed = _this_year_order(client_record, 1000.0, bags)
    _this_year_order(client_record, 5000.0)                 # no time: ignored
    cancelled = _this_year_order(client_record, 300.0, status="cancelled")
    _log(company, timed, 600)                               # $500
    _log(company, cancelled, 60)
    db.session.commit()
    body = logged_in.get("/analytics").get_data(as_text=True)
    assert "Only orders due this year with time logged" in body
    assert "(1 so far)" in body
    assert "50.0%" in body                                  # (1000 âˆ’ 500) Ã· 1000
    assert "$100.00/h" in body                              # 1000 Ã· 10h
    assert "Bags (1)" in body


def test_analytics_margins_ignore_another_tenant(logged_in, company, other_company, client_record):
    foreign = _foreign_order(other_company)
    foreign.start = foreign.due = date.today()
    services.add_entry(other_company.id, foreign.id, user_id=None,
                       worked_on=date.today(), minutes=60)
    db.session.commit()
    body = logged_in.get("/analytics").get_data(as_text=True)
    assert "(0 so far)" in body
    assert "No time logged on this year's orders yet." in body


def test_analytics_with_no_time_anywhere_still_renders(logged_in):
    body = logged_in.get("/analytics").get_data(as_text=True)
    assert "Margin YTD" in body and "(0 so far)" in body


# --- Sample data (CO9c–CO9e) -------------------------------------------------

def test_the_sample_data_logs_time_inside_each_orders_dates(app):
    """Day offsets, never after the seed day nor before the order began."""
    from models import seed_if_empty
    from sample_data import seed_sample_data

    seed_if_empty()
    today = date(2029, 9, 4)
    assert seed_sample_data(today=today)
    entries = TimeEntry.query.all()
    assert entries
    for entry in entries:
        order = db.session.get(Order, entry.order_id)
        assert order.start <= entry.worked_on <= today
        assert entry.hourly_rate == 45.0
    company_id = entries[0].company_id
    briefcase = services.order_costs(
        company_id, 1, revenue=db.session.get(Order, 1).pre_tax_total,
        material_cost=inventory_service.total_material_cost(1))
    assert briefcase.under_target                     # the demo's point
    assert services.estimate_for(4) is not None


# --- Usage (usage/CLAUDE.md) -------------------------------------------------

def test_the_costs_actions_are_recorded(app, user, company, order):
    from tests.test_usage import events

    db.session.commit()
    with app.test_client() as tenant:
        tenant.post("/login", data={"email": "admin@example.com", "password": "changeme"})
        tenant.get(f"/orders/{order.id}/costs")
        tenant.post(f"/orders/{order.id}/costs/time", data={"worked_on": "2026-07-03", "hours": "1"})
        tenant.post(f"/orders/{order.id}/costs/estimate", data={"hours": "4"})
        tenant.post("/settings/costs", data={"hourly_rate": "40"})
        tenant.post(f"/orders/{order.id}/costs/suggested-line",
                    data={"description": "x", "unit_price": "5"})
    assert len(events("view.order_costs")) == 1
    assert len(events("order.time_logged")) == 1
    assert len(events("order.price_estimated")) == 1
    assert [r.props for r in events("settings.changed")] == ['{"section": "costs"}']
    assert len(events("order.line_added")) == 1
