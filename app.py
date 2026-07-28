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

from models import (
    Client, Company, Document, Invoice, Order, OrderLine, OrderType, Payment,
    SourceOption, User, db, next_invoice_number, run_migrations, seed_if_empty,
)

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
    run_migrations()  # adds columns create_all can't; see models.py
    seed_if_empty(admin_password=os.environ.get("ADMIN_PASSWORD", "changeme"))

STATUS_LABELS = {
    "in_progress": "In progress",
    "ready": "Ready for pickup",
    "delivered": "Delivered",
    "rush": "Rush",
}

# How the money arrived. Square is listed alongside the two manual methods
# rather than treated specially — the invoice is the app's record either
# way (see Payment's docstring in models.py).
PAYMENT_METHOD_LABELS = {
    "cash": "Cash",
    "etransfer": "E-transfer",
    "square": "Square",
    "other": "Other",
}

# Canada Post two-letter codes, in the order Canada Post lists them. The
# code is what's stored and what prints ("Montréal, QC  H1V 1M6"); the
# full name is only for the dropdown.
PROVINCES = {
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "NT": "Northwest Territories",
    "NS": "Nova Scotia",
    "NU": "Nunavut",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "YT": "Yukon",
}

# "paid" is derived from the order's payments rather than set by hand, so
# it isn't offered as something staff can pick — see Invoice.display_status.
INVOICE_STATUS_LABELS = {
    "draft": "Draft",
    "sent": "Sent",
    "paid": "Paid",
    "void": "Void",
}
SETTABLE_INVOICE_STATUSES = ("draft", "sent", "void")


def get_order_or_404(order_id: int) -> Order:
    order = (
        Order.query.join(Client)
        .filter(Order.id == order_id, Client.company_id == current_user.company_id)
        .first()
    )
    if order is None:
        abort(404)
    return order


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


def get_invoice_or_404(invoice_id: int) -> Invoice:
    invoice = Invoice.query.filter_by(
        id=invoice_id, company_id=current_user.company_id
    ).first()
    if invoice is None:
        abort(404)
    return invoice


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
            "total": order.total,
            "start": order.start,
            "due": order.due,
            "status": order.status,
            "order_type": order.order_type,
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
    return render_template(
        "orders_list.html",
        orders=orders,
        status_labels=STATUS_LABELS,
        has_order_types=has_order_types,
        sort_by=sort_by,
        sort_dir=sort_dir,
        active_view="orders",
    )


@app.route("/clients")
@login_required
def clients_list():
    clients = Client.query.filter_by(company_id=current_user.company_id).all()
    sort_by, sort_dir = _sort_args(CLIENT_SORT_KEYS, "name")
    clients.sort(key=CLIENT_SORT_KEYS[sort_by], reverse=(sort_dir == "desc"))
    return render_template(
        "clients_list.html",
        clients=clients,
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
            phone=request.form.get("phone", "").strip(),
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
    return render_template(
        "client_page.html",
        client=client,
        orders=client.orders,
        return_to=return_to,
        back_label=back_label(return_to),
        source_options=source_options,
        provinces=PROVINCES,
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
    return render_template(
        "order_page.html",
        order=order,
        status_labels=STATUS_LABELS,
        payment_method_labels=PAYMENT_METHOD_LABELS,
        invoice_status_labels=INVOICE_STATUS_LABELS,
        order_types=order_types,
        return_to=return_to,
        back_label=back_label(return_to),
        today=date.today(),
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
# Invoices. The app owns the invoice record and its number for every sale,
# regardless of how the money eventually arrives (cash, e-transfer, or
# Square) — that shared sequence is what makes the three reconcilable
# against each other. Payments stay attached to the order rather than the
# invoice, since an order can be part-paid before it's ever invoiced.
# ---------------------------------------------------------------------------

@app.route("/invoices")
@login_required
def invoices():
    all_invoices = (
        Invoice.query.filter_by(company_id=current_user.company_id)
        .order_by(Invoice.issued_date.desc(), Invoice.number.desc())
        .all()
    )
    outstanding = sum(inv.order.balance_due for inv in all_invoices if inv.is_outstanding)

    # Orders with no invoice yet — the actual to-do list this page exists
    # for, shown under the invoices themselves.
    uninvoiced = (
        Order.query.join(Client)
        .filter(Client.company_id == current_user.company_id, Order.invoice == None)  # noqa: E711
        .order_by(Order.due)
        .all()
    )

    return render_template(
        "invoices.html",
        invoices=all_invoices,
        uninvoiced=uninvoiced,
        outstanding=outstanding,
        invoice_status_labels=INVOICE_STATUS_LABELS,
        active_view="invoices",
    )


@app.route("/invoices/<int:invoice_id>")
@login_required
def invoice_page(invoice_id: int):
    invoice = get_invoice_or_404(invoice_id)
    return_to = request.args.get("return_to") or url_for("invoices")
    return render_template(
        "invoice_page.html",
        invoice=invoice,
        order=invoice.order,
        issuer=invoice.issuer,
        return_to=return_to,
        back_label=back_label(return_to),
        invoice_status_labels=INVOICE_STATUS_LABELS,
        payment_method_labels=PAYMENT_METHOD_LABELS,
        settable_statuses=SETTABLE_INVOICE_STATUSES,
        active_view=None,
    )


@app.route("/orders/<int:order_id>/invoice", methods=["POST"])
@login_required
def create_invoice(order_id: int):
    order = get_order_or_404(order_id)
    if order.invoice is not None:
        # Already invoiced — treat a double submit as a no-op rather than
        # burning a second number on the same order.
        return redirect(url_for("invoice_page", invoice_id=order.invoice.id))

    company = db.session.get(Company, current_user.company_id)
    due_date_str = request.form.get("due_date")
    invoice = Invoice(
        company_id=company.id,
        order_id=order.id,
        number=next_invoice_number(company),
        issued_date=date.today(),
        due_date=date.fromisoformat(due_date_str) if due_date_str else order.due,
        status="draft",
    )
    db.session.add(invoice)
    db.session.commit()
    return redirect(url_for("invoice_page", invoice_id=invoice.id))


@app.route("/invoices/<int:invoice_id>/status", methods=["POST"])
@login_required
def set_invoice_status(invoice_id: int):
    invoice = get_invoice_or_404(invoice_id)
    was_draft = invoice.status == "draft"
    status = request.form.get("status")
    if status in SETTABLE_INVOICE_STATUSES:
        invoice.status = status
    # Only on the way out of draft: an invoice that's already issued must
    # keep the company details it was issued with, so re-saving a "sent"
    # invoice mustn't re-copy today's settings over them.
    if was_draft and invoice.status != "draft":
        invoice.freeze()
    invoice.notes = request.form.get("notes", invoice.notes or "").strip() or None
    due_date_str = request.form.get("due_date")
    invoice.due_date = date.fromisoformat(due_date_str) if due_date_str else None
    db.session.commit()
    return_to = request.form.get("return_to") or url_for("invoice_page", invoice_id=invoice.id)
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


@app.route("/settings")
@login_required
def settings():
    source_options = (
        SourceOption.query.filter_by(company_id=current_user.company_id)
        .order_by(SourceOption.sort_order)
        .all()
    )
    order_types = (
        OrderType.query.filter_by(company_id=current_user.company_id)
        .order_by(OrderType.sort_order)
        .all()
    )
    company = db.session.get(Company, current_user.company_id)
    return render_template(
        "settings.html",
        source_options=source_options,
        order_types=order_types,
        company=company,
        provinces=PROVINCES,
        next_number=next_invoice_number(company),
        active_view="settings",
    )


@app.route("/settings/company", methods=["POST"])
@login_required
def update_company_details():
    """Name, address and registration numbers — what an invoice has to say
    about who issued it. Editing these doesn't touch invoices already
    issued; those carry their own frozen copy (see Invoice.issuer)."""
    company = db.session.get(Company, current_user.company_id)
    name = request.form.get("name", "").strip()
    if name:
        company.name = name
    company.street = request.form.get("street", "").strip() or None
    company.city = request.form.get("city", "").strip() or None
    province = request.form.get("province", "").strip().upper()
    company.province = province if province in PROVINCES else None
    company.postal_code = request.form.get("postal_code", "").strip().upper() or None
    company.gst_number = request.form.get("gst_number", "").strip() or None
    company.pst_number = request.form.get("pst_number", "").strip() or None
    company.qst_number = request.form.get("qst_number", "").strip() or None
    company.neq = request.form.get("neq", "").strip() or None
    db.session.commit()
    return redirect(url_for("settings"))


@app.route("/settings/invoicing", methods=["POST"])
@login_required
def update_invoicing_settings():
    company = db.session.get(Company, current_user.company_id)
    prefix = request.form.get("invoice_prefix", "").strip().upper()
    # Changing the prefix starts a fresh sequence rather than renumbering
    # anything already issued — invoice numbers that have gone out to a
    # client are not ours to rewrite.
    if prefix:
        company.invoice_prefix = prefix[:10]
    company.payment_instructions = request.form.get("payment_instructions", "").strip() or None
    db.session.commit()
    return redirect(url_for("settings"))


@app.route("/settings/sources", methods=["POST"])
@login_required
def add_source_option():
    label = request.form.get("label", "").strip()
    if label:
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
    return redirect(url_for("settings"))


@app.route("/settings/sources/<int:source_option_id>/toggle", methods=["POST"])
@login_required
def toggle_source_option(source_option_id: int):
    option = get_source_option_or_404(source_option_id)
    option.is_active = not option.is_active
    db.session.commit()
    return redirect(url_for("settings"))


@app.route("/settings/sources/<int:source_option_id>/delete", methods=["POST"])
@login_required
def delete_source_option(source_option_id: int):
    option = get_source_option_or_404(source_option_id)
    if option.can_delete:
        db.session.delete(option)
        db.session.commit()
    return redirect(url_for("settings"))


# Order types (Custom Order / White Label / Consulting-Sampling, or whatever
# a given studio calls its own categories) — same hide-don't-delete shape and
# add/toggle/delete routes as SourceOption above, just a different table.
@app.route("/settings/order-types", methods=["POST"])
@login_required
def add_order_type():
    label = request.form.get("label", "").strip()
    if label:
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
    return redirect(url_for("settings"))


@app.route("/settings/order-types/<int:order_type_id>/toggle", methods=["POST"])
@login_required
def toggle_order_type(order_type_id: int):
    order_type = get_order_type_or_404(order_type_id)
    order_type.is_active = not order_type.is_active
    db.session.commit()
    return redirect(url_for("settings"))


@app.route("/settings/order-types/<int:order_type_id>/delete", methods=["POST"])
@login_required
def delete_order_type(order_type_id: int):
    order_type = get_order_type_or_404(order_type_id)
    if order_type.can_delete:
        db.session.delete(order_type)
        db.session.commit()
    return redirect(url_for("settings"))


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
    company_invoices = Invoice.query.filter_by(company_id=current_user.company_id).all()
    outstanding = sum(inv.order.balance_due for inv in company_invoices if inv.is_outstanding)

    return render_template(
        "analytics.html",
        avg_value_per_client=avg_value_per_client,
        top_clients=top_clients,
        source_breakdown=source_breakdown,
        total_revenue=total_revenue,
        revenue_ytd=revenue_ytd,
        method_breakdown=method_breakdown,
        outstanding=outstanding,
        active_view="analytics",
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
