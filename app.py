"""
Atelier Order Book — prototype calendar/timeline view
Custom leather goods order & inventory planner (Flask)

This is a prototype: data lives in a local SQLite database (see models.py)
and is scoped per Company, in anticipation of this eventually becoming a
multi-tenant SaaS product. Today only one company ("By Monsieur") is
seeded, but every query already filters by company_id so adding a second
tenant later is additive.
"""

import json
import os
import re

# Load .env before ANY other project import. Not stylistic: communications/
# config.py reads os.environ at *module* level, so loading after that import
# leaves the module reporting itself unconfigured while a perfectly good .env
# sits on disk. `python app.py` never reads .env on its own (only the
# `flask run` CLI does), which is exactly how that bit us.
#
# Optional dependency — the app must still boot where it isn't installed and
# the environment is set directly, as in both Docker deployments.
try:
    from dotenv import load_dotenv
except ImportError:
    pass
else:
    load_dotenv()

from datetime import date, timedelta  # noqa: E402 — must follow load_dotenv
from calendar import Calendar, month_name  # noqa: E402

from flask import Flask, render_template, request, redirect, url_for, abort, session  # noqa: E402
from flask_login import (  # noqa: E402
    LoginManager, current_user, login_required, login_user, logout_user,
)

from models import (  # noqa: E402
    Client, Company, Order, OrderLine, OrderType, Payment,
    SourceOption, User, db, run_migrations, seed_if_empty,
)
# Self-contained module: its own models, services, blueprint and templates
# (see billing/__init__.py). billing_adapter.py is the only file that knows
# an "order" is what this app bills for.
import billing.migrations as billing_migrations  # noqa: E402
import billing.routes as billing_routes  # noqa: E402
import billing_adapter  # noqa: E402
from billing import config as billing_config  # noqa: E402
from billing.services import invoicing  # noqa: E402
from billing.tax import PROVINCES  # noqa: E402
# Self-contained module: its own models, services, templates and blueprint
# (see communications/__init__.py). Importing it here registers its tables
# with db.create_all() below; register() attaches its routes. Nothing in
# this file calls Gmail — the module's services are the only entry point.
import communications.jobs as communications_jobs  # noqa: E402
import communications.migrations as communications_migrations  # noqa: E402
import communications.routes as communications_routes  # noqa: E402
from communications.services import (  # noqa: E402
    calendar_service, email_service, sender_rules,
)
# Self-contained module: its own table, storage, migrations, blueprint and
# templates (see documents/__init__.py). Real files attached to an order —
# resolve_order is handed in below rather than this module importing
# get_order_or_404 directly, to avoid a circular import with this file.
import documents.migrations as documents_migrations  # noqa: E402
import documents.routes as documents_routes  # noqa: E402
from documents import config as documents_config  # noqa: E402
from documents import services as documents_service  # noqa: E402
# Self-contained module: its own tables, migrations, blueprint and templates
# (see inventory/__init__.py). Cost-tracking only — nothing here ever
# touches Order.total/OrderLine/the invoice.
import inventory.migrations as inventory_migrations  # noqa: E402
import inventory.routes as inventory_routes  # noqa: E402
from inventory import services as inventory_service  # noqa: E402
from inventory.config import UNIT_LABELS as INVENTORY_UNIT_LABELS  # noqa: E402
from inventory.config import UNIT_WHOLE as INVENTORY_UNIT_WHOLE  # noqa: E402

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# DATABASE_URL overrides the default SQLite file. Two reasons it's an env
# var rather than a constant: tests and scripts need to point at a throwaway
# database (Flask-SQLAlchemy builds its engine at import, so overriding the
# config afterwards is too late and silently writes to the real file), and
# it's the single change needed to move to Postgres/MySQL later — see
# "Known gaps" in CLAUDE.md.
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///" + os.path.join(DATA_DIR, "atelier.db")
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Set a real SECRET_KEY via the environment in production/Docker — this
# fallback is only safe for local dev.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-not-secure")
# Defence in depth behind the communications blueprint's CSRF tokens: Lax
# stops the session cookie riding along on cross-site POSTs at all, so a
# forged form never even reaches the token check.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
# Only over TLS in production. Off by default because local dev is plain
# http and a Secure cookie there means nobody can log in at all.
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE") == "1"
# Blunt outer guard on a multi-file document upload request, ahead of the
# more precise per-file/per-company checks in documents/validation.py — so
# an oversized request is rejected before Flask fully buffers it.
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

# Behind a TLS-terminating reverse proxy (the demo deployment's nginx), the
# request arrives at gunicorn as plain http, so request.url reads
# "http://..." — and oauthlib refuses to parse an http authorization
# response at all ("OAuth 2 MUST utilize https"), which breaks the Google
# callback specifically. ProxyFix rebuilds scheme/host from
# X-Forwarded-Proto / -Host so it reads what the browser actually used.
#
# Opt-in, because these headers are only trustworthy when something we
# control sets them: exposed directly, any client could send
# X-Forwarded-Proto: https and forge the origin. Set it on the deployment
# that really is behind a proxy, and nowhere else.
if os.environ.get("TRUST_PROXY_HEADERS") == "1":
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

db.init_app(app)
communications_routes.register(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


with app.app_context():
    db.create_all()
    run_migrations()  # adds columns create_all can't; see models.py
    # The module keeps its own column migrations, so the root model file
    # doesn't have to know what it stores. Called here because app.py is the
    # composition root — a call either way between models.py and the module
    # would be circular.
    communications_migrations.run_migrations()
    # Same arrangement for billing: its own tables, its own column
    # migrations, run after the app's (which may still be splitting a
    # legacy address this module then takes over).
    billing_migrations.set_subtotal_resolver(billing_adapter.subtotal_of)
    billing_migrations.run()
    # Same arrangement again: its own table (order_documents) plus a
    # one-time drop of the legacy fake `documents` table (see
    # documents/migrations.py) — run before seeding so a fresh company
    # never sees the old placeholder rows this replaces.
    documents_migrations.run_migrations()
    # Same arrangement again: its own tables (inventory_types,
    # inventory_items, order_materials, order_material_others), nothing to
    # migrate yet since every one is brand new.
    inventory_migrations.run_migrations()
    seed_if_empty(admin_password=os.environ.get("ADMIN_PASSWORD", "changeme"))

# Background mailbox/calendar sync. A no-op unless RUN_SCHEDULER=1 — with
# two gunicorn workers an unguarded scheduler would start twice and race
# itself, so exactly one process should set it. See communications/jobs.py.
communications_jobs.start_scheduler(app)

# Owned by the billing module now — aliased so the rest of this file and
# its templates keep reading one name.
PAYMENT_METHOD_LABELS = billing_config.PAYMENT_METHOD_LABELS
INVOICE_STATUS_LABELS = billing_config.STATUS_LABELS
SETTABLE_INVOICE_STATUSES = billing_config.SETTABLE_STATUSES

STATUS_LABELS = {
    "in_progress": "In progress",
    "ready": "Ready for pickup",
    "delivered": "Delivered",
    "rush": "Rush",
}


# Zones offered at /settings/general. A curated list, not
# zoneinfo.available_timezones() — that's ~600 entries in no useful order, and
# every one of them is a way to mislabel a studio's own mail. Canada first
# (west to east), then the couple of places its clients and suppliers are.
# Anything missing is one line here; the stored value is the IANA name, so
# nothing but this list has to change.
TIME_ZONES = [
    ("America/Vancouver", "Vancouver (Pacific)"),
    ("America/Edmonton", "Edmonton (Mountain)"),
    ("America/Regina", "Regina (Central, no DST)"),
    ("America/Winnipeg", "Winnipeg (Central)"),
    ("America/Toronto", "Toronto (Eastern)"),
    ("America/Halifax", "Halifax (Atlantic)"),
    ("America/St_Johns", "St. John's (Newfoundland)"),
    ("America/New_York", "New York (Eastern)"),
    ("America/Los_Angeles", "Los Angeles (Pacific)"),
    ("Europe/London", "London"),
    ("Europe/Paris", "Paris"),
    ("UTC", "UTC"),
]



# --- What the billing module needs from this app --------------------------
#
# Four small hooks, all host knowledge the module deliberately doesn't
# have: how to price a subject, what hasn't been invoiced, what the seller
# is called, and how to word a back link. billing_adapter.py does the
# actual translation; these just hand it over.

def _uninvoiced_rows(company_id: int):
    """Orders with no invoice yet — the to-do list the invoice page exists
    for, shaped the way the module's template expects."""
    invoiced = invoicing.invoiced_subject_ids(company_id)
    orders = (
        Order.query.join(Client)
        .filter(Client.company_id == company_id)
        .order_by(Order.due)
        .all()
    )
    return [
        {
            "url": url_for("order_page", order_id=order.id),
            "label": order.item,
            "payer": order.client.name,
            "payer_url": url_for("client_page", client_id=order.client.id),
            "total": order.total,
            "due": order.due,
            "status": order.status,
        }
        for order in orders if order.id not in invoiced
    ]


def _seller_name(company_id: int) -> str:
    company = db.session.get(Company, company_id)
    return company.name if company else ""


billing_routes.register(
    app,
    resolve_billable=billing_adapter.resolver,
    uninvoiced=_uninvoiced_rows,
    display_name=_seller_name,
    back_label=lambda path: back_label(path),
)


def get_order_or_404(order_id: int) -> Order:
    order = (
        Order.query.join(Client)
        .filter(Order.id == order_id, Client.company_id == current_user.company_id)
        .first()
    )
    if order is None:
        abort(404)
    return order


documents_routes.register(app, resolve_order=get_order_or_404)
inventory_routes.register(app, resolve_order=get_order_or_404)


def _parse_amount(raw: str | None) -> float | None:
    """Money from a form field, or None if it's blank/not a number.

    Returning None rather than raising lets callers decide between "leave
    the existing value alone" and "reject the request".
    """
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def format_phone(raw: str | None) -> str:
    """Normalize a phone number to 416-555-0133 style on the way in.

    Formatting on save (not just on display) means every form that shows a
    phone number afterwards — client page, clients list, timeline modal —
    reads the same way with no per-template logic. A North American 10-digit
    number (however it was typed: dots, spaces, no separators, a leading "1")
    becomes XXX-XXX-XXXX; anything else (extensions, international numbers)
    is left exactly as entered rather than mangled by a guess.
    """
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"
    return raw.strip()


# Registered so templates can normalize phone numbers saved before this
# formatting existed, not just ones entered after — `format_phone` is applied
# on save too (see new_client/edit_client/new_order), but a filter covers
# whatever's already sitting in the database unformatted.
app.jinja_env.filters["phone"] = format_phone


def back_label(return_to: str) -> str:
    """Wording for a detail page's "← Back" link.

    Client/order pages used to be reachable only from the timeline, so the
    label could be hardcoded. They're linked from the invoice list now too,
    and a link that says "Back to timeline" but doesn't go there is worse
    than a generic one.
    """
    if return_to == "/" or return_to.startswith("/timeline"):
        return "Back to timeline"
    if return_to.startswith("/invoices"):
        return "Back to invoices"
    if return_to == "/orders":
        return "Back to orders"
    if return_to == "/clients":
        return "Back to clients"
    if return_to.startswith("/clients/"):
        return "Back to client"
    if return_to.startswith("/orders/"):
        return "Back to order"
    return "Back"


def get_client_or_404(client_id: int) -> Client:
    client = Client.query.filter_by(
        id=client_id, company_id=current_user.company_id
    ).first()
    if client is None:
        abort(404)
    return client


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("timeline_view"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user is not None and user.check_password(password):
            login_user(user)
            next_url = request.args.get("next") or url_for("timeline_view")
            return redirect(next_url)
        error = "Incorrect username or password."

    return render_template("login.html", error=error, active_view=None)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Calendar view — month grid via Python's stdlib calendar module
# (calendar.Calendar), no external dependency. Orders shown as chips on
# their due date.
# ---------------------------------------------------------------------------

@app.route("/calendar")
@login_required
def calendar_view():
    today = date.today()
    return month_view(today.year, today.month)


@app.route("/month/<int:year>/<int:month>")
@login_required
def month_view(year: int, month: int):
    if not (1 <= month <= 12):
        abort(404)

    cal = Calendar(firstweekday=6)  # Sunday-first, feels right for a paper ledger
    weeks = cal.monthdayscalendar(year, month)
    # Google Calendar events, read from the local mirror rather than from
    # Google — this view renders on every page load and mustn't depend on a
    # third-party round trip (see communications/services/calendar_service.py).
    # Empty for a company with no calendar connected, so the grid is just
    # empty days for anyone not using the integration. This calendar shows
    # only synced appointments, not orders — orders live on the timeline.
    events = calendar_service.events_by_day(current_user.company_id, year, month)
    today = date.today()

    # weeks_data: list of weeks, each week a list of (day_number, [events]) or None for padding
    weeks_data = []
    for week in weeks:
        week_data = []
        for day in week:
            if day == 0:
                week_data.append(None)
            else:
                week_data.append({
                    "day": day,
                    "events": events.get(day, []),
                    "is_today": date(year, month, day) == today,
                })
        weeks_data.append(week_data)

    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)

    # Each event once, however many days it spans — one edit dialog per event,
    # not one per cell it appears in. Same reasoning as the timeline's
    # deduplicated clients_in_view.
    events_in_view = list({event.id: event for day in events.values() for event in day}.values())
    month_total = len(events_in_view)

    return render_template(
        "calendar.html",
        weeks=weeks_data,
        month_name=month_name[month],
        year=year,
        month=month,
        prev_month=prev_month, prev_year=prev_year,
        next_month=next_month, next_year=next_year,
        month_total=month_total,
        today_year=today.year, today_month=today.month,
        # Adding an event needs somewhere to add it to. False means the studio
        # hasn't connected a calendar, so the template shows no event UI at all
        # rather than a button that can only fail.
        has_calendar=calendar_service.has_calendar(current_user.company_id),
        events_in_view=events_in_view,
        clients=Client.query.filter_by(company_id=current_user.company_id)
            .order_by(Client.first_name, Client.last_name).all(),
        default_date=today.replace(year=year, month=month, day=1)
            if (year, month) != (today.year, today.month) else today,
        # The event forms post into the communications blueprint, which reports
        # back through the module's own one-shot notice.
        notice=communications_routes.take_notice(),
        active_view="calendar",
    )


# ---------------------------------------------------------------------------
# Timeline (Gantt-style) view — multiple weeks at once, one row per order,
# client name on the left, a bar spanning start -> due for each order.
# Clicking a client name or a bar opens a quick-view/edit modal (see
# timeline.html); each modal links out to the full client/order page.
# This is the app's default view (see "/" below).
# ---------------------------------------------------------------------------

TIMELINE_WEEKS = 8       # weeks visible at once
TIMELINE_STEP_WEEKS = 4  # how far prev/next moves (half the window, so views overlap)


def _sunday_on_or_before(d: date) -> date:
    """Snap a date back to the most recent Sunday, matching the calendar's
    Sunday-first week convention (Calendar(firstweekday=6) in month_view)."""
    offset = (d.weekday() + 1) % 7  # Python: Monday=0 ... Sunday=6
    return d - timedelta(days=offset)


@app.route("/")
@login_required
def timeline_view():
    start = _sunday_on_or_before(date.today())
    return timeline_window(start.year, start.month, start.day)


@app.route("/timeline/<int:year>/<int:month>/<int:day>")
@login_required
def timeline_window(year: int, month: int, day: int):
    try:
        requested = date(year, month, day)
    except ValueError:
        abort(404)

    window_start = _sunday_on_or_before(requested)
    window_days = TIMELINE_WEEKS * 7
    window_end = window_start + timedelta(days=window_days - 1)  # inclusive

    week_headers = [window_start + timedelta(days=w * 7) for w in range(TIMELINE_WEEKS)]

    all_orders = (
        Order.query.join(Client)
        .filter(Client.company_id == current_user.company_id)
        .order_by(Order.start)
        .all()
    )

    rows = []
    clients_seen: dict[int, Client] = {}  # preserves first-appearance order, deduped
    for order in all_orders:
        # Skip orders that don't overlap the visible window at all
        if order.due < window_start or order.start > window_end:
            continue

        clipped_start = max(order.start, window_start)
        clipped_end = min(order.due, window_end)
        col_start = (clipped_start - window_start).days + 1  # 1-indexed CSS grid column
        span = (clipped_end - clipped_start).days + 1

        rows.append({
            "id": order.id,
            "client": order.client,
            "item": order.item,
            "total": order.total,
            "start": order.start,
            "due": order.due,
            "status": order.status,
            "order_type": order.order_type,
            "notes": order.notes,
            "col_start": col_start,
            "span": span,
            "truncated_start": order.start < window_start,
            "truncated_end": order.due > window_end,
        })
        clients_seen.setdefault(order.client.id, order.client)

    step_days = TIMELINE_STEP_WEEKS * 7
    prev_start = window_start - timedelta(days=step_days)
    next_start = window_start + timedelta(days=step_days)

    return render_template(
        "timeline.html",
        window_start=window_start,
        window_end=window_end,
        week_headers=week_headers,
        window_days=window_days,
        rows=rows,
        clients_in_view=list(clients_seen.values()),
        prev_start=prev_start,
        next_start=next_start,
        status_labels=STATUS_LABELS,
        active_view="timeline",
    )


# ---------------------------------------------------------------------------
# Orders & clients list pages. Full rosters, sortable by clicking a column
# header — unlike the timeline (which is windowed to a few weeks and
# filters/sorts client-side, see timeline.html), these show everything at
# once, so sorting happens server-side via ?sort=&dir= and a real page
# load. Sorting on a computed property (total, balance_due, lifetime_value)
# means fetching everything and sorting in Python rather than in SQL —
# fine at this table's scale, revisit if the row count ever gets large.
# ---------------------------------------------------------------------------

ORDER_SORT_KEYS = {
    "item": lambda o: o.item.lower(),
    "client": lambda o: o.client.name.lower(),
    "type": lambda o: o.order_type.label.lower() if o.order_type else "",
    "status": lambda o: o.status,
    "start": lambda o: o.start,
    "due": lambda o: o.due,
    "total": lambda o: o.total,
    "paid": lambda o: o.amount_paid,
    "balance": lambda o: o.balance_due,
}

# The client page's Orders tab is a single client's own roster, so it uses a
# fixed column set (no per-company customization like ORDER_COLUMNS) plus one
# column that list doesn't have — Invoice, since "did this order get billed"
# reads naturally sitting next to a single client's orders in a way it
# wouldn't in the company-wide list (that already has its own /invoices page).
CLIENT_ORDER_SORT_KEYS = {
    "item": lambda o: o.item.lower(),
    "status": lambda o: o.status,
    "start": lambda o: o.start,
    "due": lambda o: o.due,
    "total": lambda o: o.total,
    "paid": lambda o: o.amount_paid,
    "balance": lambda o: o.balance_due,
    "invoice": lambda o: o.invoice.number.lower() if o.invoice else "",
}

# Canonical Orders-list columns: key -> (label, numeric). This dict is both
# the fallback order for a company that's never saved a preference and the
# whitelist a saved preference is filtered against — a key removed from here
# later just drops out of anyone's stored JSON instead of erroring.
ORDER_COLUMNS = {
    "item": ("Item", False),
    "client": ("Client", False),
    "type": ("Type", False),
    "status": ("Status", False),
    "start": ("Start", False),
    "due": ("Due", False),
    "total": ("Total", True),
    "paid": ("Paid", True),
    "balance": ("Balance", True),
}


def _order_columns_for(company_id: int) -> list[dict]:
    """This company's Orders-list columns, in the order/visibility it saved.

    Stored as one JSON blob on Company.order_columns rather than a table like
    SourceOption/OrderType — this is a fixed set of 9 known columns, not an
    open-ended list a user names, so there's nothing per-row to hide-not-delete.
    Merges the saved list against ORDER_COLUMNS so a column added to the app
    later appears (visible, appended at the end) for a company with an older
    saved blob, and a column since removed from the app silently drops out
    instead of erroring.
    """
    company = db.session.get(Company, company_id)
    saved = []
    if company.order_columns:
        try:
            saved = json.loads(company.order_columns)
        except (ValueError, TypeError):
            saved = []
    seen = set()
    columns = []
    for entry in saved:
        key = entry.get("key") if isinstance(entry, dict) else None
        if key in ORDER_COLUMNS and key not in seen:
            seen.add(key)
            label, numeric = ORDER_COLUMNS[key]
            columns.append({"key": key, "label": label, "numeric": numeric, "visible": bool(entry.get("visible", True))})
    for key, (label, numeric) in ORDER_COLUMNS.items():
        if key not in seen:
            columns.append({"key": key, "label": label, "numeric": numeric, "visible": True})
    return columns


def _save_order_columns(company_id: int, columns: list[dict]) -> None:
    company = db.session.get(Company, company_id)
    company.order_columns = json.dumps([
        {"key": c["key"], "visible": c["visible"]} for c in columns
    ])
    db.session.commit()

CLIENT_SORT_KEYS = {
    "name": lambda c: c.name.lower(),
    "orders": lambda c: len(c.orders),
    "value": lambda c: c.lifetime_value,
}


def _sort_args(valid_keys: dict, default_key: str) -> tuple[str, str]:
    sort_by = request.args.get("sort", default_key)
    if sort_by not in valid_keys:
        sort_by = default_key
    sort_dir = "desc" if request.args.get("dir") == "desc" else "asc"
    return sort_by, sort_dir


@app.route("/orders")
@login_required
def orders_list():
    orders = (
        Order.query.join(Client)
        .filter(Client.company_id == current_user.company_id)
        .all()
    )
    sort_by, sort_dir = _sort_args(ORDER_SORT_KEYS, "due")
    orders.sort(key=ORDER_SORT_KEYS[sort_by], reverse=(sort_dir == "desc"))
    # Any type at all, not just active ones — a hidden type an order still
    # references is exactly the case the Type column needs to keep showing.
    has_order_types = (
        OrderType.query.filter_by(company_id=current_user.company_id).first() is not None
    )
    # A column can be hidden in settings, and Type additionally only ever
    # shows when there's a type to show — same "don't show empty" rule the
    # dropdown on the order form already follows.
    columns = [
        c for c in _order_columns_for(current_user.company_id)
        if c["visible"] and (c["key"] != "type" or has_order_types)
    ]
    return render_template(
        "orders_list.html",
        orders=orders,
        status_labels=STATUS_LABELS,
        has_order_types=has_order_types,
        columns=columns,
        sort_by=sort_by,
        sort_dir=sort_dir,
        active_view="orders",
    )


@app.route("/clients")
@login_required
def clients_list():
    # Seeing the roster is what settles the "a client appeared by itself"
    # badge — the client is already there, so looking at it is the whole of
    # the response required. (Unlike the lead badge, which counts work.)
    sender_rules.acknowledge_all(current_user.company_id)
    clients = Client.query.filter_by(company_id=current_user.company_id).all()
    # One grouped query for the whole table, not a count per row.
    unread_by_client = email_service.unread_counts_by_client(current_user.company_id)
    sort_by, sort_dir = _sort_args(CLIENT_SORT_KEYS, "name")
    clients.sort(key=CLIENT_SORT_KEYS[sort_by], reverse=(sort_dir == "desc"))
    return render_template(
        "clients_list.html",
        clients=clients,
        unread_by_client=unread_by_client,
        sort_by=sort_by,
        sort_dir=sort_dir,
        active_view="clients",
    )


@app.route("/clients/new", methods=["GET", "POST"])
@login_required
def new_client():
    """Standalone client creation — the "+ Add client" button on /clients.

    Separate from the inline "+ Add new client" option in new_order.html
    (which creates a client as a side effect of placing an order): this is
    for a client with no order yet, e.g. someone just inquiring. Only
    collects the basics; address and sources are edited from the full
    client page afterwards, same "quick create, fill in detail later"
    split as new orders.
    """
    return_to = request.values.get("return_to") or url_for("clients_list")

    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        if not first_name or not last_name:
            abort(400)
        client = Client(
            company_id=current_user.company_id,
            first_name=first_name,
            last_name=last_name,
            email=request.form.get("email", "").strip(),
            phone=format_phone(request.form.get("phone", "")),
        )
        db.session.add(client)
        db.session.commit()
        return redirect(return_to)

    return render_template(
        "new_client.html",
        return_to=return_to,
        back_label=back_label(return_to),
        active_view=None,
    )


# ---------------------------------------------------------------------------
# Client & order detail pages. Reachable directly, or via "view full
# profile / open full order page" links inside the timeline's modals.
# `return_to` carries the visitor back to whichever timeline window they
# came from, instead of always bouncing to today's window.
# ---------------------------------------------------------------------------

@app.route("/clients/<int:client_id>")
@login_required
def client_page(client_id: int):
    """The client page's "Information" tab — see client_orders() for the
    other tab. Kept at the bare /clients/<id> URL (rather than e.g.
    /clients/<id>/info) since every other page's links to a client already
    point here and there's no reason to churn all of them."""
    client = get_client_or_404(client_id)
    return_to = request.args.get("return_to") or url_for("timeline_view")
    # Selectable options = active ones, plus any now-hidden option this
    # client already has (so existing data stays visible), deduped and
    # ordered by sort_order.
    active_options = SourceOption.query.filter_by(
        company_id=current_user.company_id, is_active=True
    ).all()
    source_options = sorted(
        {o.id: o for o in active_options + client.sources}.values(),
        key=lambda o: o.sort_order,
    )
    other_source_option = next((o for o in source_options if o.is_other), None)
    return render_template(
        "client_page.html",
        section="info",
        client=client,
        return_to=return_to,
        back_label=back_label(return_to),
        source_options=source_options,
        other_source_option=other_source_option,
        provinces=PROVINCES,
        active_view=None,
    )


@app.route("/clients/<int:client_id>/orders")
@login_required
def client_orders(client_id: int):
    client = get_client_or_404(client_id)
    return_to = request.args.get("return_to") or url_for("timeline_view")
    orders = list(client.orders)
    sort_by, sort_dir = _sort_args(CLIENT_ORDER_SORT_KEYS, "due")
    orders.sort(key=CLIENT_ORDER_SORT_KEYS[sort_by], reverse=(sort_dir == "desc"))
    return render_template(
        "client_page.html",
        section="orders",
        client=client,
        orders=orders,
        return_to=return_to,
        back_label=back_label(return_to),
        status_labels=STATUS_LABELS,
        sort_by=sort_by,
        sort_dir=sort_dir,
        active_view=None,
    )


@app.route("/clients/<int:client_id>/edit", methods=["POST"])
@login_required
def edit_client(client_id: int):
    client = get_client_or_404(client_id)
    client.first_name = request.form.get("first_name", "").strip() or client.first_name
    client.last_name = request.form.get("last_name", "").strip() or client.last_name
    client.email = request.form.get("email", "").strip()
    client.phone = format_phone(request.form.get("phone", ""))
    # Address fields are only on the full client page, not the timeline's
    # quick-edit modal — absent from the form means "not shown", not
    # "cleared". The province gate matters more than it looks: it decides
    # what tax this client is charged (see taxes_for in models.py).
    if "street" in request.form:
        client.street = request.form.get("street", "").strip() or None
        client.city = request.form.get("city", "").strip() or None
        province = request.form.get("province", "").strip().upper()
        client.province = province if province in PROVINCES else None
        client.postal_code = request.form.get("postal_code", "").strip().upper() or None

    source_ids = [int(i) for i in request.form.getlist("source_ids") if i.isdigit()]
    client.sources = SourceOption.query.filter(
        SourceOption.id.in_(source_ids),
        SourceOption.company_id == current_user.company_id,
    ).all()

    # The free-text detail behind whichever source is marked is_other only
    # means anything while that option is actually checked — kept blank the
    # rest of the time rather than holding onto stale text nobody can see.
    if any(o.is_other for o in client.sources):
        client.other_source_detail = request.form.get("other_source_detail", "").strip() or None
    else:
        client.other_source_detail = None

    db.session.commit()
    return_to = request.form.get("return_to") or url_for("timeline_view")
    return redirect(return_to)


@app.route("/orders/new", methods=["GET", "POST"])
@login_required
def new_order():
    clients = (
        Client.query.filter_by(company_id=current_user.company_id)
        .order_by(Client.first_name, Client.last_name)
        .all()
    )
    # Active only — a brand-new order can't already be tagged with a
    # now-hidden type, unlike editing (see order_page()/edit_order()).
    order_types = (
        OrderType.query.filter_by(company_id=current_user.company_id, is_active=True)
        .order_by(OrderType.sort_order)
        .all()
    )
    return_to = request.values.get("return_to") or url_for("timeline_view")

    if request.method == "POST":
        client_id = request.form.get("client_id", "")
        if client_id == "new":
            first_name = request.form.get("new_first_name", "").strip()
            last_name = request.form.get("new_last_name", "").strip()
            if not first_name or not last_name:
                abort(400)
            client = Client(
                company_id=current_user.company_id,
                first_name=first_name,
                last_name=last_name,
                email=request.form.get("new_email", "").strip(),
                phone=format_phone(request.form.get("new_phone", "")),
            )
            db.session.add(client)
            db.session.flush()  # assigns client.id
        else:
            client = Client.query.filter_by(
                id=client_id if client_id.isdigit() else None,
                company_id=current_user.company_id,
            ).first()
            if client is None:
                abort(400)

        status = request.form.get("status")
        item = request.form.get("item", "").strip()
        order_type_id = request.form.get("order_type_id", "")
        order_type = (
            OrderType.query.filter_by(
                id=order_type_id, company_id=current_user.company_id, is_active=True
            ).first()
            if order_type_id.isdigit() else None
        )
        order = Order(
            client_id=client.id,
            item=item,
            start=date.fromisoformat(request.form.get("start")),
            due=date.fromisoformat(request.form.get("due")),
            status=status if status in STATUS_LABELS else "in_progress",
            order_type_id=order_type.id if order_type else None,
            notes=request.form.get("notes", "").strip(),
        )
        db.session.add(order)
        db.session.flush()  # assigns order.id
        # An order's value comes from its lines, so a new order starts with
        # one covering the whole thing. Splitting it into several (materials,
        # surcharges, ...) happens on the full order page — same "quick form
        # here, detail over there" split as payments.
        db.session.add(OrderLine(
            order_id=order.id,
            description=item,
            quantity=1,
            unit_price=_parse_amount(request.form.get("price")) or 0.0,
            sort_order=0,
        ))
        db.session.commit()
        return redirect(return_to)

    return render_template(
        "new_order.html",
        clients=clients,
        order_types=order_types,
        status_labels=STATUS_LABELS,
        return_to=return_to,
        back_label=back_label(return_to),
        today=date.today(),
        active_view=None,
    )


@app.route("/orders/<int:order_id>")
@login_required
def order_page(order_id: int):
    """The order page's "Details" tab — see order_billing() for the other
    tab. Kept at the bare /orders/<id> URL (rather than e.g. /orders/<id>/details)
    since every other page's links to an order already point here."""
    order = get_order_or_404(order_id)
    return_to = request.args.get("return_to") or url_for("timeline_view")
    # Same "active options ∪ whatever's already selected" pattern as the
    # client page's source checkboxes: a now-hidden type this order already
    # has stays selectable here (so saving the rest of the form doesn't
    # silently clear it), just isn't offered to other orders.
    active_types = OrderType.query.filter_by(
        company_id=current_user.company_id, is_active=True
    ).all()
    order_types = sorted(
        {t.id: t for t in active_types + ([order.order_type] if order.order_type else [])}.values(),
        key=lambda t: t.sort_order,
    )
    storage_used_bytes = documents_service.usage_for_company(current_user.company_id)
    has_document_types = documents_service.has_document_types(current_user.company_id)
    return render_template(
        "order_page.html",
        section="details",
        order=order,
        status_labels=STATUS_LABELS,
        order_types=order_types,
        return_to=return_to,
        back_label=back_label(return_to),
        active_view=None,
        # For the documents/_explorer.html partial included below. Flat
        # layout (documents) when no DocumentType exists for the company;
        # sectioned (sections) once at least one does — see has_document_types.
        has_document_types=has_document_types,
        documents=None if has_document_types else documents_service.list_for_order(order.id),
        sections=documents_service.sections_for_order(order.id, current_user.company_id)
            if has_document_types else None,
        documents_notice=documents_routes.take_notice(),
        storage_used_bytes=storage_used_bytes,
        storage_limit_bytes=documents_config.MAX_TOTAL_BYTES,
        storage_percent=min(100, round(storage_used_bytes / documents_config.MAX_TOTAL_BYTES * 100, 1)),
        inline_previewable_content_types=documents_config.INLINE_PREVIEWABLE_CONTENT_TYPES,
        allowed_extensions=documents_config.ALLOWED_EXTENSIONS,
    )


@app.route("/orders/<int:order_id>/billing")
@login_required
def order_billing(order_id: int):
    order = get_order_or_404(order_id)
    return_to = request.args.get("return_to") or url_for("timeline_view")
    return render_template(
        "order_page.html",
        section="billing",
        order=order,
        payment_method_labels=PAYMENT_METHOD_LABELS,
        invoice_status_labels=INVOICE_STATUS_LABELS,
        return_to=return_to,
        back_label=back_label(return_to),
        today=date.today(),
        active_view=None,
    )


@app.route("/orders/<int:order_id>/materials")
@login_required
def order_materials(order_id: int):
    """The order page's "Materials" tab — cost-tracking only, see
    inventory/__init__.py. Renders order_page.html same as order_billing()."""
    order = get_order_or_404(order_id)
    return_to = request.args.get("return_to") or url_for("timeline_view")
    return render_template(
        "order_page.html",
        section="materials",
        order=order,
        materials=inventory_service.list_materials_for_order(order.id),
        others=inventory_service.list_others_for_order(order.id),
        total_material_cost=inventory_service.total_material_cost(order.id),
        selectable_items=inventory_service.selectable_items(current_user.company_id, order.id),
        understocked_materials=inventory_service.understocked_materials_for_order(order.id),
        unit_labels=INVENTORY_UNIT_LABELS,
        unit_whole=INVENTORY_UNIT_WHOLE,
        return_to=return_to,
        back_label=back_label(return_to),
        active_view=None,
    )


@app.route("/orders/<int:order_id>/edit", methods=["POST"])
@login_required
def edit_order(order_id: int):
    order = get_order_or_404(order_id)
    order.item = request.form.get("item", "").strip() or order.item

    start_str = request.form.get("start")
    due_str = request.form.get("due")
    if start_str:
        order.start = date.fromisoformat(start_str)
    if due_str:
        order.due = date.fromisoformat(due_str)

    if "pickup_date" in request.form:
        pickup_str = request.form.get("pickup_date", "")
        order.pickup_date = date.fromisoformat(pickup_str) if pickup_str else None

    status = request.form.get("status")
    if status in STATUS_LABELS:
        order.status = status

    if "order_type_id" in request.form:
        order_type_id = request.form.get("order_type_id", "")
        order.order_type = (
            OrderType.query.filter_by(
                id=order_type_id, company_id=current_user.company_id
            ).first()
            if order_type_id.isdigit() else None
        )

    order.notes = request.form.get("notes", "").strip()
    db.session.commit()
    return_to = request.form.get("return_to") or url_for("timeline_view")
    return redirect(return_to)


# ---------------------------------------------------------------------------
# Line items. An order's value is the sum of these (Order.total), so this
# is where a price actually gets set — the order form itself only ever
# creates/edits the one line it starts with.
# ---------------------------------------------------------------------------

@app.route("/orders/<int:order_id>/lines", methods=["POST"])
@login_required
def add_order_line(order_id: int):
    order = get_order_or_404(order_id)
    description = request.form.get("description", "").strip()
    unit_price = _parse_amount(request.form.get("unit_price"))
    quantity = request.form.get("quantity", "1")
    if description and unit_price is not None:
        db.session.add(OrderLine(
            order_id=order.id,
            description=description,
            quantity=int(quantity) if quantity.isdigit() and int(quantity) > 0 else 1,
            unit_price=unit_price,
            sort_order=len(order.lines),
        ))
        db.session.commit()
    return_to = request.form.get("return_to") or url_for("timeline_view")
    return redirect(return_to)


@app.route("/orders/<int:order_id>/lines/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_order_line(order_id: int, line_id: int):
    order = get_order_or_404(order_id)
    line = OrderLine.query.filter_by(id=line_id, order_id=order.id).first()
    if line is not None:
        db.session.delete(line)
        db.session.commit()
    return_to = request.form.get("return_to") or url_for("timeline_view")
    return redirect(return_to)


@app.route("/orders/<int:order_id>/payments", methods=["POST"])
@login_required
def add_payment(order_id: int):
    order = get_order_or_404(order_id)
    amount = _parse_amount(request.form.get("amount"))
    paid_date_str = request.form.get("paid_date")
    if amount is not None and paid_date_str:
        method = request.form.get("method")
        db.session.add(Payment(
            order_id=order.id,
            amount=amount,
            paid_date=date.fromisoformat(paid_date_str),
            method=method if method in PAYMENT_METHOD_LABELS else "cash",
            reference=request.form.get("reference", "").strip() or None,
        ))
        db.session.commit()
    return_to = request.form.get("return_to") or url_for("timeline_view")
    return redirect(return_to)


@app.route("/orders/<int:order_id>/payments/<int:payment_id>/delete", methods=["POST"])
@login_required
def delete_payment(order_id: int, payment_id: int):
    order = get_order_or_404(order_id)
    payment = Payment.query.filter_by(id=payment_id, order_id=order.id).first()
    if payment is not None:
        db.session.delete(payment)
        db.session.commit()
    return_to = request.form.get("return_to") or url_for("timeline_view")
    return redirect(return_to)


# ---------------------------------------------------------------------------
# Settings — currently just the per-company "how did you hear about us"
# options shown as checkboxes on the client page. Options are never
# hard-deleted once a client references one (see SourceOption.can_delete in
# models.py) so historical answers/stats stay intact; hiding is the only way
# to retire one from the checkbox list.
# ---------------------------------------------------------------------------

def get_source_option_or_404(source_option_id: int) -> SourceOption:
    option = SourceOption.query.filter_by(
        id=source_option_id, company_id=current_user.company_id
    ).first()
    if option is None:
        abort(404)
    return option


def get_order_type_or_404(order_type_id: int) -> OrderType:
    order_type = OrderType.query.filter_by(
        id=order_type_id, company_id=current_user.company_id
    ).first()
    if order_type is None:
        abort(404)
    return order_type


def _flash_settings_notice(message: str) -> None:
    """One-shot message for the next settings page render.

    Same session-key-scoped shape as documents' _flash/take_notice and
    communications' _flash/_take_notice — this app has no app-wide flash
    convention, so each part that needs one keeps its own key rather than
    introducing Flask's flash() globally.
    """
    session["settings_notice"] = message


def _take_settings_notice() -> str | None:
    return session.pop("settings_notice", None)


def _is_duplicate_label(model, company_id: int, label: str) -> bool:
    """Case-insensitive match against every row this company has for
    `model` (active and hidden both — a hidden row is still a real one
    someone could unhide back into a naming collision)."""
    normalized = label.strip().lower()
    return any(
        row.label.strip().lower() == normalized
        for row in model.query.filter_by(company_id=company_id)
    )


@app.route("/settings")
@login_required
def settings():
    # No content of its own — just lands on the first category. Bookmarks
    # and the nav's "Settings" link both go through here.
    return redirect(url_for("settings_general"))


@app.route("/settings/general")
@login_required
def settings_general():
    return render_template(
        "settings.html",
        section="general",
        company=db.session.get(Company, current_user.company_id),
        time_zones=TIME_ZONES,
        active_view="settings",
    )


@app.route("/settings/orders")
@login_required
def settings_orders():
    order_types = (
        OrderType.query.filter_by(company_id=current_user.company_id)
        .order_by(OrderType.sort_order)
        .all()
    )
    # Same "any type at all, not just active" rule as orders_list()'s
    # has_order_types — with none, the Type column can never show on the
    # Orders list regardless of its saved visibility, so there's nothing for
    # this editor to offer a row for either.
    has_order_types = bool(order_types)
    return render_template(
        "settings.html",
        section="orders",
        company=db.session.get(Company, current_user.company_id),
        order_types=order_types,
        # Every column, not just visible ones — this editor is what turns
        # visibility back on, so a hidden column has to still render its row.
        # Type is the one exception: it's excluded entirely with no order
        # types on file, matching the Orders list's own "don't show empty" rule.
        order_columns=[
            c for c in _order_columns_for(current_user.company_id)
            if c["key"] != "type" or has_order_types
        ],
        # For the documents/_settings_types.html partial included below.
        document_types=documents_service.list_document_types(current_user.company_id),
        documents_notice=documents_routes.take_notice(),
        notice=_take_settings_notice(),
        active_view="settings",
    )


@app.route("/settings/inventory")
@login_required
def settings_inventory():
    return render_template(
        "settings.html",
        section="inventory",
        company=db.session.get(Company, current_user.company_id),
        units=inventory_service.list_units(current_user.company_id),
        available_units=inventory_service.available_catalog_units(current_user.company_id),
        inventory_types=inventory_service.list_types(current_user.company_id),
        notice=_take_settings_notice(),
        active_view="settings",
    )


@app.route("/settings/clients")
@login_required
def settings_clients():
    source_options = (
        SourceOption.query.filter_by(company_id=current_user.company_id)
        .order_by(SourceOption.sort_order)
        .all()
    )
    return render_template(
        "settings.html",
        section="clients",
        company=db.session.get(Company, current_user.company_id),
        source_options=source_options,
        notice=_take_settings_notice(),
        active_view="settings",
    )


@app.route("/settings/invoicing")
@login_required
def settings_invoicing():
    company = db.session.get(Company, current_user.company_id)
    # The letterhead belongs to the billing module; this page just edits it.
    profile = invoicing.profile_for(company.id, company.name)
    db.session.commit()  # profile_for creates one on first visit
    return render_template(
        "settings.html",
        section="invoicing",
        company=company,
        profile=profile,
        provinces=PROVINCES,
        next_number=invoicing.next_number(company.id, company.name),
        active_view="settings",
    )


@app.route("/settings/general", methods=["POST"])
@login_required
def update_preferences():
    """The company's display timezone.

    Only affects rendering — stored timestamps stay naive UTC (see the
    communications module), so switching zones re-labels history rather than
    rewriting it. Anything outside TIME_ZONES is ignored rather than stored:
    an unknown zone name would make every time on the page fall back to UTC
    with nothing to say why.
    """
    company = db.session.get(Company, current_user.company_id)
    chosen = request.form.get("timezone", "").strip()
    if chosen in dict(TIME_ZONES):
        company.timezone = chosen
        db.session.commit()
    return redirect(url_for("settings_general"))


# Order types (Custom Order / White Label / Consulting-Sampling, or whatever
# a given studio calls its own categories) — same hide-don't-delete shape and
# add/toggle/delete routes as SourceOption below, just a different table.
@app.route("/settings/order-types", methods=["POST"])
@login_required
def add_order_type():
    label = request.form.get("label", "").strip()
    if label and _is_duplicate_label(OrderType, current_user.company_id, label):
        _flash_settings_notice(f'An order type called "{label}" already exists.')
    elif label:
        max_sort_order = (
            OrderType.query.filter_by(company_id=current_user.company_id)
            .count()
        )
        db.session.add(OrderType(
            company_id=current_user.company_id,
            label=label,
            sort_order=max_sort_order,
        ))
        db.session.commit()
    return redirect(url_for("settings_orders"))


@app.route("/settings/order-types/<int:order_type_id>/toggle", methods=["POST"])
@login_required
def toggle_order_type(order_type_id: int):
    order_type = get_order_type_or_404(order_type_id)
    order_type.is_active = not order_type.is_active
    db.session.commit()
    return redirect(url_for("settings_orders"))


@app.route("/settings/order-types/<int:order_type_id>/delete", methods=["POST"])
@login_required
def delete_order_type(order_type_id: int):
    order_type = get_order_type_or_404(order_type_id)
    if order_type.can_delete:
        db.session.delete(order_type)
        db.session.commit()
    return redirect(url_for("settings_orders"))


# Orders-list column order/visibility — same drag-to-reorder pattern as
# documents.reorder_types (immediate fetch on drop), plus a per-column
# hide/show toggle matching the order-type/source-option Hide button, rather
# than a checkbox-plus-Save form: everything else in Settings acts
# immediately on click, and this shouldn't be the odd one out.
@app.route("/settings/order-columns/<key>/toggle", methods=["POST"])
@login_required
def toggle_order_column(key: str):
    if key not in ORDER_COLUMNS:
        abort(404)
    columns = _order_columns_for(current_user.company_id)
    for col in columns:
        if col["key"] == key:
            col["visible"] = not col["visible"]
    _save_order_columns(current_user.company_id, columns)
    return redirect(url_for("settings_orders"))


@app.route("/settings/order-columns/reorder", methods=["POST"])
@login_required
def reorder_order_columns():
    payload = request.get_json(silent=True) or {}
    order = [key for key in payload.get("order", []) if key in ORDER_COLUMNS]
    if not order:
        return ("", 204)
    visibility = {c["key"]: c["visible"] for c in _order_columns_for(current_user.company_id)}
    columns = [{"key": key, "visible": visibility.get(key, True)} for key in order]
    # A key the client didn't send back (shouldn't happen — every column
    # renders a draggable row) is appended rather than silently dropped.
    for key in visibility:
        if key not in order:
            columns.append({"key": key, "visible": visibility[key]})
    _save_order_columns(current_user.company_id, columns)
    return ("", 204)


@app.route("/settings/sources", methods=["POST"])
@login_required
def add_source_option():
    label = request.form.get("label", "").strip()
    if label and _is_duplicate_label(SourceOption, current_user.company_id, label):
        _flash_settings_notice(f'An option called "{label}" already exists.')
    elif label:
        max_sort_order = (
            SourceOption.query.filter_by(company_id=current_user.company_id)
            .count()
        )
        db.session.add(SourceOption(
            company_id=current_user.company_id,
            label=label,
            sort_order=max_sort_order,
        ))
        db.session.commit()
    return redirect(url_for("settings_clients"))


@app.route("/settings/sources/<int:source_option_id>/toggle", methods=["POST"])
@login_required
def toggle_source_option(source_option_id: int):
    option = get_source_option_or_404(source_option_id)
    option.is_active = not option.is_active
    db.session.commit()
    return redirect(url_for("settings_clients"))


@app.route("/settings/sources/<int:source_option_id>/set-other", methods=["POST"])
@login_required
def set_other_source_option(source_option_id: int):
    """Pair (or unpair) a free-text box with this option, on the client page.

    Handy as an "Other, please specify" catch-all, but that's just the
    common case — the mechanism is a plain "this option gets a text box
    too", usable on whichever option needs one. At most one per company —
    adding it here removes it from whatever option held it before, same
    "only one at a time" shape a radio group would give, but built on the
    existing toggle-button convention instead of a new form control.
    """
    option = get_source_option_or_404(source_option_id)
    turning_on = not option.is_other
    SourceOption.query.filter_by(
        company_id=current_user.company_id, is_other=True
    ).update({"is_other": False})
    option.is_other = turning_on
    db.session.commit()
    return redirect(url_for("settings_clients"))


@app.route("/settings/sources/reorder", methods=["POST"])
@login_required
def reorder_source_options():
    """Fired by the drag-and-drop handler in settings.html — a JSON body,
    not a form post, since there's no page navigation involved. Same shape
    as documents.reorder_types: ids not belonging to this company are
    silently skipped rather than trusted, since the request is data from a
    fetch() call, not a form the server built."""
    payload = request.get_json(silent=True) or {}
    ordered_ids = [i for i in payload.get("order", []) if isinstance(i, int)]
    options_by_id = {
        o.id: o for o in
        SourceOption.query.filter_by(company_id=current_user.company_id).all()
    }
    for index, option_id in enumerate(ordered_ids):
        option = options_by_id.get(option_id)
        if option is not None:
            option.sort_order = index
    db.session.commit()
    return ("", 204)


@app.route("/settings/sources/<int:source_option_id>/delete", methods=["POST"])
@login_required
def delete_source_option(source_option_id: int):
    option = get_source_option_or_404(source_option_id)
    if option.can_delete:
        db.session.delete(option)
        db.session.commit()
    return redirect(url_for("settings_clients"))


@app.route("/settings/company", methods=["POST"])
@login_required
def update_company_details():
    """Name, address and registration numbers — what an invoice has to say
    about who issued it. Only the name is the app's; the rest is the
    billing module's letterhead. Editing any of it leaves invoices already
    issued alone, since those carry their own frozen copy."""
    company = db.session.get(Company, current_user.company_id)
    name = request.form.get("name", "").strip()
    if name:
        company.name = name
    province = request.form.get("province", "").strip().upper()
    invoicing.update_profile(
        company.id, company.name,
        street=request.form.get("street", "").strip() or None,
        city=request.form.get("city", "").strip() or None,
        province=province if province in PROVINCES else None,
        postal_code=request.form.get("postal_code", "").strip().upper() or None,
        gst_number=request.form.get("gst_number", "").strip() or None,
        pst_number=request.form.get("pst_number", "").strip() or None,
        qst_number=request.form.get("qst_number", "").strip() or None,
        neq=request.form.get("neq", "").strip() or None,
    )
    db.session.commit()
    return redirect(url_for("settings_invoicing"))


@app.route("/settings/invoicing", methods=["POST"])
@login_required
def update_invoicing_settings():
    company = db.session.get(Company, current_user.company_id)
    prefix = request.form.get("invoice_prefix", "").strip().upper()
    # Changing the prefix starts a fresh sequence rather than renumbering
    # anything already issued — invoice numbers that have gone out to a
    # client are not ours to rewrite.
    fields = {
        "payment_instructions":
            request.form.get("payment_instructions", "").strip() or None,
    }
    if prefix:
        fields["invoice_prefix"] = prefix[:10]
    invoicing.update_profile(company.id, company.name, **fields)
    db.session.commit()
    return redirect(url_for("settings_invoicing"))


# ---------------------------------------------------------------------------
# Analytics — company-wide client and revenue stats. "Paid revenue" is
# always the sum of recorded Payment rows, never inferred from order
# status, since studios run different deposit schemes (see Payment's
# docstring in models.py). "Average value per client" is lifetime spend
# per client (sum of their orders), averaged only over clients who've
# actually ordered — not average order price.
# ---------------------------------------------------------------------------

@app.route("/analytics")
@login_required
def analytics():
    clients = Client.query.filter_by(company_id=current_user.company_id).all()
    clients_with_orders = [c for c in clients if c.orders]
    avg_value_per_client = (
        sum(c.lifetime_value for c in clients_with_orders) / len(clients_with_orders)
        if clients_with_orders else 0
    )
    top_clients = sorted(
        clients_with_orders, key=lambda c: c.lifetime_value, reverse=True
    )[:5]

    # Include hidden SourceOptions too — a hidden-but-historically-used
    # source should still show up in stats (that's the whole point of
    # hiding instead of deleting once a client references it).
    source_options = SourceOption.query.filter_by(company_id=current_user.company_id).all()
    total_clients = len(clients)
    source_breakdown = [
        (option.label, len(option.clients) / total_clients * 100)
        for option in source_options
    ] if total_clients else []
    source_breakdown = sorted(
        (pair for pair in source_breakdown if pair[1] > 0),
        key=lambda pair: pair[1], reverse=True,
    )

    payments = (
        Payment.query.join(Order).join(Client)
        .filter(Client.company_id == current_user.company_id)
        .all()
    )
    total_revenue = sum(p.amount for p in payments)
    this_year = date.today().year
    revenue_ytd = sum(p.amount for p in payments if p.paid_date.year == this_year)

    # Where the money actually came from. Ordered by amount rather than by
    # the labels dict so the dominant method reads first.
    by_method: dict[str, float] = {}
    for payment in payments:
        by_method[payment.method] = by_method.get(payment.method, 0) + payment.amount
    method_breakdown = sorted(
        ((PAYMENT_METHOD_LABELS.get(k, k), v) for k, v in by_method.items()),
        key=lambda pair: pair[1], reverse=True,
    )

    # Outstanding counts invoiced work only — an order that hasn't been
    # billed yet isn't money anyone owes.
    company_id = current_user.company_id
    documents = invoicing.documents_for(
        company_id, billing_adapter.resolver(company_id, with_urls=False),
        _seller_name(company_id),
    )
    outstanding = sum(
        doc.balance_due for doc in documents
        if doc.status != "void" and not doc.is_settled
    )
    # Tax actually collected, straight out of the frozen invoice rows —
    # what a GST/QST remittance is.
    tax_collected = invoicing.tax_collected(company_id)

    return render_template(
        "analytics.html",
        avg_value_per_client=avg_value_per_client,
        top_clients=top_clients,
        source_breakdown=source_breakdown,
        total_revenue=total_revenue,
        revenue_ytd=revenue_ytd,
        method_breakdown=method_breakdown,
        outstanding=outstanding,
        tax_collected=sorted(tax_collected, key=lambda pair: pair[1], reverse=True),
        active_view="analytics",
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
