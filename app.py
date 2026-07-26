"""
Atelier Order Book — prototype calendar view
Custom leather goods order & inventory planner (Flask)

This is a prototype: orders live in memory and reset when the server
restarts. Once the shape of the data feels right, swap ORDERS for a
real database (SQLite via SQLAlchemy is the natural next step).
"""

from datetime import date
from calendar import Calendar, month_name
from flask import Flask, render_template, abort

app = Flask(__name__)

# ---------------------------------------------------------------------------
# In-memory sample data. Each order represents a custom leather commission.
# status drives the colour of its chip on the calendar.
#   "in_progress" -> currently being cut / tooled / stitched
#   "ready"       -> finished, awaiting pickup or shipment
#   "delivered"   -> handed off to the client
#   "rush"        -> in progress, but on a tight deadline
# ---------------------------------------------------------------------------
ORDERS = [
    {"id": 1, "client": "M. Alarie", "item": "Full-grain briefcase", "due": date(2026, 7, 3), "status": "delivered", "notes": "Horween Chromexcel, brass hardware"},
    {"id": 2, "client": "S. Okafor", "item": "Weekender duffel", "due": date(2026, 7, 9), "status": "in_progress", "notes": "Waxed canvas panels + veg-tan trim"},
    {"id": 3, "client": "R. Chen", "item": "Bifold wallet (monogram)", "due": date(2026, 7, 9), "status": "ready", "notes": "Hand-stitched, gold foil initials"},
    {"id": 4, "client": "L. Beaumont", "item": "Messenger bag", "due": date(2026, 7, 14), "status": "rush", "notes": "Client travels on the 15th"},
    {"id": 5, "client": "A. Novak", "item": "Belt, 38mm", "due": date(2026, 7, 14), "status": "in_progress", "notes": "English bridle leather"},
    {"id": 6, "client": "T. Iverson", "item": "Camera strap", "due": date(2026, 7, 18), "status": "ready", "notes": "Padded, nickel rivets"},
    {"id": 7, "client": "P. Dubois", "item": "Tote bag", "due": date(2026, 7, 22), "status": "in_progress", "notes": "Natural veg-tan, will patina"},
    {"id": 8, "client": "M. Alarie", "item": "Passport holder (x2)", "due": date(2026, 7, 22), "status": "in_progress", "notes": "Gift for anniversary"},
    {"id": 9, "client": "H. Solberg", "item": "Watch strap", "due": date(2026, 7, 27), "status": "rush", "notes": "Custom buckle from client's own"},
    {"id": 10, "client": "G. Marchetti", "item": "Laptop sleeve", "due": date(2026, 7, 30), "status": "in_progress", "notes": "13-inch, felt lining"},
    {"id": 11, "client": "N. Petrova", "item": "Card holder", "due": date(2026, 8, 4), "status": "in_progress", "notes": "Minimalist, 3-slot"},
    {"id": 12, "client": "R. Chen", "item": "Travel journal cover", "due": date(2026, 8, 6), "status": "ready", "notes": "Refillable, brass corners"},
]

STATUS_LABELS = {
    "in_progress": "In progress",
    "ready": "Ready for pickup",
    "delivered": "Delivered",
    "rush": "Rush",
}


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
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
