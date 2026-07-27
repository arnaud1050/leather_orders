"""
Atelier Order Book — prototype calendar/timeline view
Custom leather goods order & inventory planner (Flask)

This is a prototype: data lives in a local SQLite database (see models.py)
and is scoped per Company, in anticipation of this eventually becoming a
multi-tenant SaaS product. Today only one company ("By Monsieur") is
seeded, but every query already filters by company_id so adding a second
tenant later is additive.
"""

import os
from datetime import date, timedelta
from calendar import Calendar, month_name

from flask import Flask, render_template, request, redirect, url_for, abort
from flask_login import (
    LoginManager, current_user, login_required, login_user, logout_user,
)

from models import Client, Company, Document, Order, User, db, seed_if_empty

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(DATA_DIR, "atelier.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Set a real SECRET_KEY via the environment in production/Docker — this
# fallback is only safe for local dev.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-not-secure")

db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


with app.app_context():
    db.create_all()
    seed_if_empty(admin_password=os.environ.get("ADMIN_PASSWORD", "changeme"))

STATUS_LABELS = {
    "in_progress": "In progress",
    "ready": "Ready for pickup",
    "delivered": "Delivered",
    "rush": "Rush",
}

# Matches the "How did you hear about BY MONSIEUR" checkboxes on the
# bymonsieur.ca contact form. A client's selections are stored as a single
# comma-separated string in Client.source (see edit_client()).
SOURCE_OPTIONS = [
    "Google Search",
    "Word of Mouth",
    "Craft Market / Open Studio",
    "Instagram",
    "Facebook",
    "LinkedIn",
    "Other",
]


def get_order_or_404(order_id: int) -> Order:
    order = (
        Order.query.join(Client)
        .filter(Order.id == order_id, Client.company_id == current_user.company_id)
        .first()
    )
    if order is None:
        abort(404)
    return order


def get_client_or_404(client_id: int) -> Client:
    client = Client.query.filter_by(
        id=client_id, company_id=current_user.company_id
    ).first()
    if client is None:
        abort(404)
    return client


def orders_by_day(year: int, month: int) -> dict[int, list[Order]]:
    """Map day-of-month -> list of orders due that day, for the given month."""
    grouped: dict[int, list[Order]] = {}
    orders = (
        Order.query.join(Client)
        .filter(Client.company_id == current_user.company_id)
        .all()
    )
    for order in orders:
        if order.due.year == year and order.due.month == month:
            grouped.setdefault(order.due.day, []).append(order)
    return grouped


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
    grouped = orders_by_day(year, month)
    today = date.today()

    # weeks_data: list of weeks, each week a list of (day_number, [orders]) or None for padding
    weeks_data = []
    for week in weeks:
        week_data = []
        for day in week:
            if day == 0:
                week_data.append(None)
            else:
                week_data.append({
                    "day": day,
                    "orders": grouped.get(day, []),
                    "is_today": date(year, month, day) == today,
                })
        weeks_data.append(week_data)

    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)

    month_total = sum(len(v) for v in grouped.values())

    return render_template(
        "calendar.html",
        weeks=weeks_data,
        month_name=month_name[month],
        year=year,
        month=month,
        prev_month=prev_month, prev_year=prev_year,
        next_month=next_month, next_year=next_year,
        status_labels=STATUS_LABELS,
        month_total=month_total,
        today_year=today.year, today_month=today.month,
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
            "price": order.price,
            "start": order.start,
            "due": order.due,
            "status": order.status,
            "notes": order.notes,
            "documents": order.documents,
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
# Client & order detail pages. Reachable directly, or via "view full
# profile / open full order page" links inside the timeline's modals.
# `return_to` carries the visitor back to whichever timeline window they
# came from, instead of always bouncing to today's window.
# ---------------------------------------------------------------------------

@app.route("/clients/<int:client_id>")
@login_required
def client_page(client_id: int):
    client = get_client_or_404(client_id)
    return_to = request.args.get("return_to") or url_for("timeline_view")
    selected_sources = [s.strip() for s in (client.source or "").split(",") if s.strip()]
    return render_template(
        "client_page.html",
        client=client,
        orders=client.orders,
        return_to=return_to,
        source_options=SOURCE_OPTIONS,
        selected_sources=selected_sources,
        active_view=None,
    )


@app.route("/clients/<int:client_id>/edit", methods=["POST"])
@login_required
def edit_client(client_id: int):
    client = get_client_or_404(client_id)
    client.first_name = request.form.get("first_name", "").strip() or client.first_name
    client.last_name = request.form.get("last_name", "").strip() or client.last_name
    client.email = request.form.get("email", "").strip()
    client.phone = request.form.get("phone", "").strip()
    selected_sources = [s for s in request.form.getlist("source") if s in SOURCE_OPTIONS]
    client.source = ", ".join(selected_sources)
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
                phone=request.form.get("new_phone", "").strip(),
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
        order = Order(
            client_id=client.id,
            item=request.form.get("item", "").strip(),
            start=date.fromisoformat(request.form.get("start")),
            due=date.fromisoformat(request.form.get("due")),
            price=float(request.form.get("price") or 0),
            status=status if status in STATUS_LABELS else "in_progress",
            notes=request.form.get("notes", "").strip(),
        )
        db.session.add(order)
        db.session.commit()
        return redirect(return_to)

    return render_template(
        "new_order.html",
        clients=clients,
        status_labels=STATUS_LABELS,
        return_to=return_to,
        today=date.today(),
        active_view=None,
    )


@app.route("/orders/<int:order_id>")
@login_required
def order_page(order_id: int):
    order = get_order_or_404(order_id)
    return_to = request.args.get("return_to") or url_for("timeline_view")
    return render_template(
        "order_page.html",
        order=order,
        status_labels=STATUS_LABELS,
        return_to=return_to,
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

    price_str = request.form.get("price")
    if price_str:
        try:
            order.price = float(price_str)
        except ValueError:
            pass

    status = request.form.get("status")
    if status in STATUS_LABELS:
        order.status = status

    order.notes = request.form.get("notes", "").strip()
    db.session.commit()
    return_to = request.form.get("return_to") or url_for("timeline_view")
    return redirect(return_to)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
