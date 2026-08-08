"""
What a brand new database contains — and, more importantly, what it doesn't.

`seed_if_empty()` used to insert ten fake clients and twelve fake orders. A
production install that shipped with those in it would be worse than useless,
so the split is now load-bearing: bootstrap in `models.py`, demo dataset in
`sample_data.py`, and nothing imports the latter at startup. These tests fail
if that ever quietly merges back together (CO9a, CO9b, CO9c).
"""

import ast
import pathlib
from datetime import date, timedelta

from models import (
    Client, Company, Order, OrderType, Payment, SourceOption, User, db,
    seed_if_empty,
)
from sample_data import seed_sample_data

from billing.models import Invoice
from billing.services import invoicing

ROOT = pathlib.Path(__file__).resolve().parent.parent


# --- CO9a: the bootstrap inserts a tenant, not a dataset ------------------

def test_seed_if_empty_creates_one_company_with_an_admin(app):
    seed_if_empty()

    assert Company.query.count() == 1
    company = Company.query.first()
    assert company.name == "By Monsieur"
    assert [u.username for u in User.query.all()] == ["admin"]
    assert User.query.first().company_id == company.id


def test_seed_if_empty_creates_the_option_lists_forms_read_from(app):
    seed_if_empty()

    company = Company.query.first()
    assert SourceOption.query.filter_by(company_id=company.id).count() > 0
    assert OrderType.query.filter_by(company_id=company.id).count() > 0
    # Ordered by their position in the defaults, not by insertion accident.
    labels = [
        o.label for o in SourceOption.query
        .filter_by(company_id=company.id).order_by(SourceOption.sort_order)
    ]
    assert labels[0] == "Google Search"


def test_seed_if_empty_creates_no_clients_orders_or_invoices(app):
    """The whole point of the split: a production install starts empty."""
    seed_if_empty()

    assert Client.query.count() == 0
    assert Order.query.count() == 0
    assert Invoice.query.count() == 0


def test_seed_if_empty_leaves_the_letterhead_blank(app):
    """Only the display name. A plausible-looking placeholder address would
    print on a real invoice before anyone noticed it was fake."""
    seed_if_empty()

    profile = invoicing.profile_for(Company.query.first().id)
    assert profile.display_name == "By Monsieur"
    assert profile.invoice_prefix == "INV"  # the module's own default
    assert not profile.street
    assert not profile.city
    assert not profile.gst_number
    assert not profile.pst_number
    assert not profile.payment_instructions


def test_seed_if_empty_is_not_a_reset_mechanism(app):
    """Returns the moment a company exists — it never re-inserts, and never
    overwrites a name the studio changed from /settings."""
    seed_if_empty()
    Company.query.first().name = "Renamed Studio"
    db.session.commit()

    seed_if_empty()

    assert Company.query.count() == 1
    assert Company.query.first().name == "Renamed Studio"
    assert User.query.count() == 1


# --- CO9c: the demo dataset is opt-in and refuses a populated install -----

def test_seed_sample_data_adds_clients_orders_and_invoices(app):
    seed_if_empty()

    assert seed_sample_data() is True
    assert Client.query.count() == 10
    assert Order.query.count() == 12
    assert Invoice.query.count() == 4
    # Raised through the billing module, so they're real invoices with
    # numbers from its own sequence rather than rows the running app
    # couldn't have produced.
    numbers = sorted(i.number for i in Invoice.query)
    assert numbers[0] == f"BM-{numbers[0].split('-')[1]}-0001"
    assert all(n.startswith("BM-") for n in numbers)


def test_seed_sample_data_writes_the_placeholder_letterhead(app):
    seed_if_empty()
    seed_sample_data()

    profile = invoicing.profile_for(Company.query.first().id)
    assert profile.invoice_prefix == "BM"
    assert profile.city == "Vancouver"


# --- the dates move with the seed date ------------------------------------

def test_sample_orders_straddle_the_day_they_were_seeded(app):
    """The timeline is the landing page, so a fresh seed has to open on
    something: work in progress, work still to start, not an archive."""
    seed_if_empty()
    today = date(2031, 3, 17)  # far from any date written into the source

    seed_sample_data(today=today)

    orders = Order.query.all()
    assert [o for o in orders if o.start <= today <= o.due], "nothing in progress"
    assert [o for o in orders if o.start > today], "nothing upcoming"
    assert [o for o in orders if o.due < today], "nothing already delivered"
    # And the whole set sits in a window a person would actually scroll to.
    assert min(o.start for o in orders) >= today - timedelta(days=30)
    assert max(o.due for o in orders) <= today + timedelta(days=30)


def test_seeding_a_year_later_shifts_every_date_by_a_year(app):
    """The dates are computed, not stored — same shape, moved wholesale."""
    seed_if_empty()
    first = date(2030, 6, 1)
    seed_sample_data(today=first)
    before = {o.id: (o.start, o.due) for o in Order.query}

    Order.query.delete()
    Client.query.delete()
    db.session.commit()
    seed_sample_data(today=first + timedelta(days=365))
    after = {o.id: (o.start, o.due) for o in Order.query}

    assert after.keys() == before.keys()
    for order_id, (start, due) in before.items():
        assert after[order_id] == (
            start + timedelta(days=365), due + timedelta(days=365))


def test_no_sample_order_is_ready_or_delivered_before_it_starts(app):
    """Now that the dates move and the statuses don't, the two can disagree.
    "Ready for pickup" on an order that hasn't been started yet is the kind
    of nonsense a demo gets noticed for."""
    seed_if_empty()
    today = date(2029, 9, 4)

    seed_sample_data(today=today)

    for order in Order.query.filter(Order.start > today):
        assert order.status in {"in_progress", "rush"}, (
            f"order {order.id} starts in the future but is {order.status!r}")


def test_no_sample_payment_or_invoice_is_dated_in_the_future(app):
    """Money received tomorrow, or an invoice issued tomorrow, is nonsense —
    and it would skew the analytics page's year-to-date figures."""
    seed_if_empty()
    today = date(2029, 9, 4)

    seed_sample_data(today=today)

    assert all(p.paid_date <= today for p in Payment.query)
    assert all(i.issued_date <= today for i in Invoice.query)


def test_sample_invoice_numbers_follow_the_seed_year(app):
    """Numbering is `PREFIX-YEAR-0001` off the issue date, so a hardcoded
    year would collide with the module's own sequence the moment the seed
    date moved into another one."""
    seed_if_empty()

    seed_sample_data(today=date(2033, 7, 12))

    assert sorted(i.number for i in Invoice.query) == [
        "BM-2033-0001", "BM-2033-0002", "BM-2033-0003", "BM-2033-0004",
    ]


def test_seed_sample_data_refuses_a_company_that_has_clients(app):
    """It fills an empty install; it never resets a populated one."""
    seed_if_empty()
    company = Company.query.first()
    db.session.add(Client(
        company_id=company.id, first_name="Real", last_name="Customer"))
    db.session.commit()

    assert seed_sample_data() is False
    assert Client.query.count() == 1
    assert Order.query.count() == 0


def test_seed_sample_data_does_nothing_without_a_company(app):
    assert seed_sample_data() is False
    assert Client.query.count() == 0


# --- CO9b/CO9c: the split, enforced against the source ---------------------

def test_the_app_never_imports_the_sample_data(app):
    """A single `from sample_data import ...` at startup would put the demo
    clients back into every production deployment. Read the source, don't
    trust the intent."""
    for path in [ROOT / "app.py", ROOT / "models.py"]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
            elif isinstance(node, ast.Import):
                modules += [a.name for a in node.names]
            for module in modules:
                assert module.split(".")[0] != "sample_data", (
                    f"{path.name} imports sample_data — the demo dataset must "
                    "only be reachable from scripts/seed_sample_data.py."
                )
