"""
Atelier Order Book — prototype calendar view
Custom leather goods order & inventory planner (Flask)

This is a prototype: customers/orders live in memory and reset when the
server restarts. Once the shape of the data feels right, swap CUSTOMERS
and ORDERS for real database tables (SQLite via SQLAlchemy is the
natural next step) — the dict shapes below are deliberately close to
what those tables would look like.
"""

from datetime import date, timedelta
from calendar import Calendar, month_name
from flask import Flask, render_template, request, redirect, url_for, abort

app = Flask(__name__)

# ---------------------------------------------------------------------------
# In-memory sample data.
#
# CUSTOMERS is keyed by id. ORDERS reference a customer via "customer_id".
# Right after ORDERS is defined, each order dict gets a "customer" key
# attached (a reference into CUSTOMERS, not a copy) so templates can just
# write order.customer.name etc. Because it's a reference, editing a
# customer via the /customers/<id>/edit route updates it everywhere at
# once — no need to re-sync anything.
# ---------------------------------------------------------------------------
CUSTOMERS = {
    1: {"id": 1, "name": "M. Alarie", "email": "m.alarie@example.com", "phone": "514-555-0142"},
    2: {"id": 2, "name": "S. Okafor", "email": "s.okafor@example.com", "phone": "604-555-0198"},
    3: {"id": 3, "name": "R. Chen", "email": "r.chen@example.com", "phone": "778-555-0110"},
    4: {"id": 4, "name": "L. Beaumont", "email": "l.beaumont@example.com", "phone": "438-555-0176"},
    5: {"id": 5, "name": "A. Novak", "email": "a.novak@example.com", "phone": "416-555-0133"},
    6: {"id": 6, "name": "T. Iverson", "email": "t.iverson@example.com", "phone": "604-555-0121"},
    7: {"id": 7, "name": "P. Dubois", "email": "p.dubois@example.com", "phone": "514-555-0187"},
    8: {"id": 8, "name": "H. Solberg", "email": "h.solberg@example.com", "phone": "778-555-0165"},
    9: {"id": 9, "name": "G. Marchetti", "email": "g.marchetti@example.com", "phone": "416-555-0154"},
    10: {"id": 10, "name": "N. Petrova", "email": "n.petrova@example.com", "phone": "604-555-0109"},
}

# Placeholder documents — the upload/download flow isn't built yet, this
# just simulates what an order with attachments will eventually look like.
_SAMPLE_DOCUMENTS = [
    {"label": "Mockup", "filename": "mockup_v1.pdf"},
    {"label": "Invoice", "filename": "invoice_draft.pdf"},
]

# Each order represents a custom leather commission.
# "start" -> when work begins (cutting/tooling). Combined with "due", this is
#            what the timeline view draws as a bar.
# status drives the colour of its chip/bar.
#   "in_progress" -> currently being cut / tooled / stitched
#   "ready"       -> finished, awaiting pickup or shipment
#   "delivered"   -> handed off to the client
#   "rush"        -> in progress, but on a tight deadline
ORDERS = [
    {"id": 1, "customer_id": 1, "item": "Full-grain briefcase", "start": date(2026, 6, 15), "due": date(2026, 7, 3), "price": 850.00, "status": "delivered", "notes": "Horween Chromexcel, brass hardware"},
    {"id": 2, "customer_id": 2, "item": "Weekender duffel", "start": date(2026, 6, 22), "due": date(2026, 7, 9), "price": 620.00, "status": "in_progress", "notes": "Waxed canvas panels + veg-tan trim"},
    {"id": 3, "customer_id": 3, "item": "Bifold wallet (monogram)", "start": date(2026, 7, 1), "due": date(2026, 7, 9), "price": 140.00, "status": "ready", "notes": "Hand-stitched, gold foil initials"},
    {"id": 4, "customer_id": 4, "item": "Messenger bag", "start": date(2026, 6, 29), "due": date(2026, 7, 14), "price": 480.00, "status": "rush", "notes": "Client travels on the 15th"},
    {"id": 5, "customer_id": 5, "item": "Belt, 38mm", "start": date(2026, 7, 8), "due": date(2026, 7, 14), "price": 95.00, "status": "in_progress", "notes": "English bridle leather"},
    {"id": 6, "customer_id": 6, "item": "Camera strap", "start": date(2026, 7, 10), "due": date(2026, 7, 18), "price": 110.00, "status": "ready", "notes": "Padded, nickel rivets"},
    {"id": 7, "customer_id": 7, "item": "Tote bag", "start": date(2026, 7, 6), "due": date(2026, 7, 22), "price": 310.00, "status": "in_progress", "notes": "Natural veg-tan, will patina"},
    {"id": 8, "customer_id": 1, "item": "Passport holder (x2)", "start": date(2026, 7, 16), "due": date(2026, 7, 22), "price": 130.00, "status": "in_progress", "notes": "Gift for anniversary"},
    {"id": 9, "customer_id": 8, "item": "Watch strap", "start": date(2026, 7, 21), "due": date(2026, 7, 27), "price": 85.00, "status": "rush", "notes": "Custom buckle from client's own"},
    {"id": 10, "customer_id": 9, "item": "Laptop sleeve", "start": date(2026, 7, 20), "due": date(2026, 7, 30), "price": 165.00, "status": "in_progress", "notes": "13-inch, felt lining"},
    {"id": 11, "customer_id": 10, "item": "Card holder", "start": date(2026, 7, 28), "due": date(2026, 8, 4), "price": 75.00, "status": "in_progress", "notes": "Minimalist, 3-slot"},
    {"id": 12, "customer_id": 3, "item": "Travel journal cover", "start": date(2026, 7, 24), "due": date(2026, 8, 6), "price": 120.00, "status": "ready", "notes": "Refillable, brass corners"},
]

for _order in ORDERS:
    _order["customer"] = CUSTOMERS[_order["customer_id"]]
    _order["documents"] = list(_SAMPLE_DOCUMENTS)  # placeholder, same fake docs on every order for now
del _order

STATUS_LABELS = {
    "in_progress": "In progress",
    "ready": "Ready for pickup",
    "delivered": "Delivered",
    "rush": "Rush",
}


def get_order_or_404(order_id: int) -> dict:
    order = next((o for o in ORDERS if o["id"] == order_id), None)
    if order is None:
        abort(404)
    return order


def get_customer_or_404(customer_id: int) -> dict:
    customer = CUSTOMERS.get(customer_id)
    if customer is None:
        abort(404)
    return customer


def orders_by_day(year: int, month: int) -> dict[int, list[dict]]:
    """Map day-of-month -> list of orders due that day, for the given month."""
    grouped: dict[int, list[dict]] = {}
    for order in ORDERS:
        if order["due"].year == year and order["due"].month == month:
            grouped.setdefault(order["due"].day, []).append(order)
    return grouped


@app.route("/")
def index():
    today = date.today()
    return month_view(today.year, today.month)


@app.route("/month/<int:year>/<int:month>")
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
# customer name on the left, a bar spanning start -> due for each order.
# Clicking a customer name or a bar opens a quick-view/edit modal (see
# timeline.html); each modal links out to the full customer/order page.
# ---------------------------------------------------------------------------

TIMELINE_WEEKS = 8       # weeks visible at once
TIMELINE_STEP_WEEKS = 4  # how far prev/next moves (half the window, so views overlap)


def _sunday_on_or_before(d: date) -> date:
    """Snap a date back to the most recent Sunday, matching the calendar's
    Sunday-first week convention (Calendar(firstweekday=6) in month_view)."""
    offset = (d.weekday() + 1) % 7  # Python: Monday=0 ... Sunday=6
    return d - timedelta(days=offset)


@app.route("/timeline")
def timeline_view():
    start = _sunday_on_or_before(date.today())
    return timeline_window(start.year, start.month, start.day)


@app.route("/timeline/<int:year>/<int:month>/<int:day>")
def timeline_window(year: int, month: int, day: int):
    try:
        requested = date(year, month, day)
    except ValueError:
        abort(404)

    window_start = _sunday_on_or_before(requested)
    window_days = TIMELINE_WEEKS * 7
    window_end = window_start + timedelta(days=window_days - 1)  # inclusive

    week_headers = [window_start + timedelta(days=w * 7) for w in range(TIMELINE_WEEKS)]

    rows = []
    customers_seen: dict[int, dict] = {}  # preserves first-appearance order, deduped
    for order in sorted(ORDERS, key=lambda o: o["start"]):
        # Skip orders that don't overlap the visible window at all
        if order["due"] < window_start or order["start"] > window_end:
            continue

        clipped_start = max(order["start"], window_start)
        clipped_end = min(order["due"], window_end)
        col_start = (clipped_start - window_start).days + 1  # 1-indexed CSS grid column
        span = (clipped_end - clipped_start).days + 1

        rows.append({
            "id": order["id"],
            "customer": order["customer"],
            "item": order["item"],
            "price": order["price"],
            "start": order["start"],
            "due": order["due"],
            "status": order["status"],
            "notes": order["notes"],
            "documents": order["documents"],
            "col_start": col_start,
            "span": span,
            "truncated_start": order["start"] < window_start,
            "truncated_end": order["due"] > window_end,
        })
        customers_seen.setdefault(order["customer"]["id"], order["customer"])

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
        customers_in_view=list(customers_seen.values()),
        prev_start=prev_start,
        next_start=next_start,
        active_view="timeline",
    )


# ---------------------------------------------------------------------------
# Customer & order detail pages. Reachable directly, or via "view full
# profile / open full order page" links inside the timeline's modals.
# `return_to` carries the visitor back to whichever timeline window they
# came from, instead of always bouncing to today's window.
# ---------------------------------------------------------------------------

@app.route("/customers/<int:customer_id>")
def customer_page(customer_id: int):
    customer = get_customer_or_404(customer_id)
    customer_orders = [o for o in ORDERS if o["customer_id"] == customer_id]
    return_to = request.args.get("return_to") or url_for("timeline_view")
    return render_template(
        "customer_page.html",
        customer=customer,
        orders=customer_orders,
        return_to=return_to,
        active_view=None,
    )


@app.route("/customers/<int:customer_id>/edit", methods=["POST"])
def edit_customer(customer_id: int):
    customer = get_customer_or_404(customer_id)
    customer["name"] = request.form.get("name", "").strip() or customer["name"]
    customer["email"] = request.form.get("email", "").strip()
    customer["phone"] = request.form.get("phone", "").strip()
    return_to = request.form.get("return_to") or url_for("timeline_view")
    return redirect(return_to)


@app.route("/orders/<int:order_id>")
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


if __name__ == "__main__":
    app.run(debug=True, port=5000)