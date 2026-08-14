"""
The demo tenant's sample clients, orders and invoices.

**Nothing in here ever runs on a production deployment.** `seed_if_empty()`
in `models.py` creates only what an empty database genuinely needs — the
company, the admin user, and the per-company option lists (`SourceOption`,
`OrderType`) that the app's forms read from. This file is the separate,
opt-in layer on top of that: fake people with fake orders, useful for the
demo deployment and for local development, misleading anywhere real.

**Nothing imports this at startup.** It runs only when someone runs
`scripts/seed_sample_data.py` by hand, and even then it refuses a company
that already has clients — it fills an empty install, it never resets a
populated one.

Fixed rates, unit catalogs and province tax tables are *not* sample data and
aren't here: they're code constants (`billing/tax.py`, `inventory/config.py`)
or bootstrap defaults in `models.py`, and a prod deployment gets all of them.

**Every date here is an offset in days from the day it's seeded**, never a
fixed calendar date — see `SAMPLE_ORDERS`. A hardcoded set goes stale: seed
it a year later and the timeline lands on an empty window with every order
in the past, which is exactly the screen the demo exists to show off.
"""

from datetime import date, timedelta

from models import (
    Client, Company, Order, OrderLine, OrderType, Payment, db,
)

# The placeholder letterhead. Address and registration numbers are in the
# right *shape* but are NOT the studio's real ones — same caveat as prices
# and lead times. Real deployments start with an empty letterhead and fill
# it in from /settings before issuing anything. BC charges GST + PST (not
# QST/NEQ, which are Quebec-specific), so qst_number/neq are left unset
# entirely rather than blanked strings — the same "blank registrations don't
# print" path the sample data has always exercised.
SAMPLE_LETTERHEAD = {
    "invoice_prefix": "BM",
    "street": "Laurel Street, Studio 3",
    "city": "Vancouver",
    "province": "BC",
    "postal_code": "V6H 3P7",
    "gst_number": "123456789 RT0001",
    "pst_number": "PST-1234-5678",
    "payment_instructions": (
        "E-transfer to payments@example.com — no security question needed.\n"
        "Cash accepted at pickup. Cheques payable to By Monsieur."
    ),
}

# Provinces are spread across QC / BC / ON on purpose: tax is charged at
# the client's province's rate, so the sample data exercises GST+QST,
# GST+PST and HST. Two clients (6, 8) have no address at all, which is
# what an order with uncalculable tax looks like — see Order.tax_status.
SAMPLE_CLIENTS = [
    {"id": 1, "first_name": "Marie", "last_name": "Alarie", "email": "m.alarie@example.com", "phone": "514-555-0142", "street": "1240 rue Saint-Denis", "city": "Montréal", "province": "QC", "postal_code": "H2X 3J5"},
    {"id": 2, "first_name": "Sarah", "last_name": "Okafor", "email": "s.okafor@example.com", "phone": "604-555-0198", "street": "780 Bute St, Apt 1104", "city": "Vancouver", "province": "BC", "postal_code": "V6E 1Y9"},
    {"id": 3, "first_name": "Ryan", "last_name": "Chen", "email": "r.chen@example.com", "phone": "778-555-0110", "street": "3355 Cambie St", "city": "Vancouver", "province": "BC", "postal_code": "V5Z 2W6"},
    {"id": 4, "first_name": "Lucas", "last_name": "Beaumont", "email": "l.beaumont@example.com", "phone": "438-555-0176", "street": "55 avenue Laurier O", "city": "Montréal", "province": "QC", "postal_code": "H2T 2N4"},
    {"id": 5, "first_name": "Anna", "last_name": "Novak", "email": "a.novak@example.com", "phone": "416-555-0133", "street": "914 Queen St W", "city": "Toronto", "province": "ON", "postal_code": "M6J 1G6"},
    {"id": 6, "first_name": "Thomas", "last_name": "Iverson", "email": "t.iverson@example.com", "phone": "604-555-0121", "street": None, "city": None, "province": None, "postal_code": None},
    {"id": 7, "first_name": "Pierre", "last_name": "Dubois", "email": "p.dubois@example.com", "phone": "514-555-0187", "street": "203 rue Ontario E", "city": "Montréal", "province": "QC", "postal_code": "H2X 1H5"},
    {"id": 8, "first_name": "Hannah", "last_name": "Solberg", "email": "h.solberg@example.com", "phone": "778-555-0165", "street": None, "city": None, "province": None, "postal_code": None},
    {"id": 9, "first_name": "Giulia", "last_name": "Marchetti", "email": "g.marchetti@example.com", "phone": "416-555-0154", "street": "62 Ossington Ave", "city": "Toronto", "province": "ON", "postal_code": "M6J 2Y7"},
    {"id": 10, "first_name": "Nadia", "last_name": "Petrova", "email": "n.petrova@example.com", "phone": "604-555-0109", "street": None, "city": None, "province": None, "postal_code": None},
]

# Day offsets from the seed date, not calendar dates: `0` is the day
# `seed_sample_data()` runs, negative is that many days before. The spread is
# deliberate — the timeline is the app's landing page, and it has to open on
# something worth looking at whenever the demo is seeded:
#
#   * one order finished and delivered (both dates in the past),
#   * several straddling today (started, not yet due — the bars the timeline
#     is really for),
#   * several still to start, out to a fortnight ahead.
#
# Due dates run from 4 days back to 14 ahead, so the default multi-week
# window is never empty and never entirely historical. client_id 1 and 3
# each get a second order, so they show up as "returning" clients.
#
# **Statuses have to agree with the offsets**, and the lifecycle now makes
# that automatic rather than a thing to hand-check: a "confirmed" order
# renders as "Confirmed" while its start is in the future and "In progress"
# once it arrives (Order.display_status), so the same row is correct on both
# sides of its start date. "delivered" and "ready" still only make sense on
# an order that has actually started, so those keep past-dated starts.
#
# One "tentative" and one "cancelled" order are here to exercise the two
# inactive ends: the tentative one shows as a dashed, uncommitted bar on the
# timeline, and the cancelled one is deliberately *absent* from it while
# still appearing in /orders — which is the whole point of that stage, and
# invisible in a dataset that has none.
#
# "is_rush" is optional and defaults to False. It's a flag, not a status,
# so it sits alongside one — order 4 is rush *and* underway, which the old
# four-value vocabulary couldn't express at all.
#
# "lines" is (description, quantity, unit_price) — an order's value is the
# sum of these, there's no separate price field. "payments" is optional per
# order: orders without it have no deposit recorded yet (matches real orders
# that haven't been confirmed with a deposit), and each is
# (amount, day_offset, method, reference). Payment offsets are always <= 0 —
# money received on a day that hasn't happened yet would be nonsense, so a
# deposit on a not-yet-started order is dated a day or two back instead of
# at its start. Amounts are rough ~50% deposits, not full payment, except
# the delivered order (fully settled, as it would be by pickup). Methods are
# mixed across cash / e-transfer / Square on purpose, since reconciling those
# three against one invoice is the point.
SAMPLE_ORDERS = [
    {"id": 1, "client_id": 1, "item": "Full-grain briefcase", "start": -18, "due": -4, "status": "delivered", "notes": "Horween Chromexcel, brass hardware", "order_type": "Custom Order",
     "lines": [("Full-grain briefcase, Horween Chromexcel", 1, 760.00), ("Brass hardware upgrade", 1, 90.00)],
     "payments": [(425.00, -18, "square", "sq:9F2K-4471"), (425.00, -4, "cash", None)]},
    {"id": 2, "client_id": 2, "item": "Weekender duffel", "start": -11, "due": 10, "status": "confirmed", "notes": "Waxed canvas panels + veg-tan trim",
     "lines": [("Weekender duffel, veg-tan trim", 1, 560.00), ("Waxed canvas panels", 1, 60.00)],
     "payments": [(310.00, -11, "etransfer", "e-tfr CA8821")]},
    {"id": 3, "client_id": 3, "item": "Bifold wallet (monogram)", "start": -4, "due": 5, "status": "ready", "notes": "Hand-stitched, gold foil initials",
     "lines": [("Bifold wallet, hand-stitched", 1, 110.00), ("Gold foil monogram", 1, 30.00)],
     "payments": [(70.00, -4, "cash", None)]},
    {"id": 4, "client_id": 4, "item": "Messenger bag", "start": -1, "due": 11, "status": "confirmed", "is_rush": True, "notes": "Client travels the day after it's due", "order_type": "Custom Order",
     "lines": [("Messenger bag", 1, 430.00), ("Rush surcharge", 1, 50.00)],
     "payments": [(240.00, -1, "square", "sq:7T1B-9930")]},
    {"id": 5, "client_id": 5, "item": "Belt, 38mm", "start": 1, "due": 8, "status": "confirmed", "notes": "English bridle leather",
     "lines": [("Belt, 38mm English bridle", 1, 95.00)]},
    {"id": 6, "client_id": 6, "item": "Camera strap", "start": 0, "due": 6, "status": "ready", "notes": "Padded, nickel rivets",
     "lines": [("Camera strap, padded", 1, 95.00), ("Nickel rivets", 1, 15.00)],
     "payments": [(55.00, 0, "etransfer", "e-tfr CA9014")]},
    {"id": 7, "client_id": 7, "item": "Tote bag", "start": -2, "due": 13, "status": "confirmed", "notes": "Natural veg-tan, will patina", "order_type": "White Label",
     "lines": [("Tote bag, natural veg-tan", 1, 310.00)]},
    {"id": 8, "client_id": 1, "item": "Passport holder (x2)", "start": 3, "due": 9, "status": "confirmed", "notes": "Gift for anniversary",
     "lines": [("Passport holder", 2, 65.00)],
     "payments": [(65.00, -1, "cash", None)]},
    {"id": 9, "client_id": 8, "item": "Watch strap", "start": 5, "due": 10, "status": "confirmed", "is_rush": True, "notes": "Custom buckle from client's own",
     "lines": [("Watch strap, client's own buckle", 1, 85.00)], "order_type": "Custom Order"},
    {"id": 10, "client_id": 9, "item": "Laptop sleeve", "start": 4, "due": 12, "status": "confirmed", "notes": "13-inch, felt lining",
     "lines": [("Laptop sleeve, 13-inch", 1, 145.00), ("Felt lining", 1, 20.00)]},
    {"id": 11, "client_id": 10, "item": "Card holder", "start": 7, "due": 14, "status": "confirmed", "notes": "Minimalist, 3-slot",
     "lines": [("Card holder, 3-slot", 1, 75.00)]},
    {"id": 12, "client_id": 3, "item": "Travel journal cover", "start": -3, "due": 8, "status": "ready", "notes": "Refillable, brass corners", "order_type": "Consulting/Sampling",
     "lines": [("Travel journal cover, refillable", 1, 105.00), ("Brass corners", 1, 15.00)],
     "payments": [(60.00, -2, "etransfer", "e-tfr CA9127")]},
    # Still a conversation: no deposit, so no payment, and it holds a slot on
    # the timeline as a dashed bar rather than a committed one. Deletable,
    # unlike everything above it.
    {"id": 13, "client_id": 6, "item": "Saddle bag", "start": 9, "due": 20, "status": "tentative", "notes": "Sizing and hardware still being discussed", "order_type": "Custom Order",
     "lines": [("Saddle bag, veg-tan", 1, 390.00)]},
    # Absent from the timeline, present in /orders — the reason a cancelled
    # order exists as a stage instead of a delete. The note is the shape
    # cancel_order() writes, dated prefix and all.
    {"id": 14, "client_id": 9, "item": "Duffel, small", "start": -6, "due": 7, "status": "cancelled", "notes": "Navy waxed canvas",
     "cancelled": (-3, "Client moved abroad, deposit refunded"),
     "lines": [("Duffel, small", 1, 280.00)]},
]

# Only some orders are invoiced — matching reality, where an invoice gets
# raised when work is confirmed rather than the moment an order is booked.
# order 1 is fully paid (so it renders as "Paid" without the status saying
# so), 2 and 4 are sent-and-partly-paid, 12 is still a draft. Offsets again,
# with the same "never in the future" rule the payments follow: an invoice
# dated tomorrow is not a thing. Listed oldest-first so the numbers the
# billing module assigns (BM-YYYY-0001 upward) run in issue order.
SAMPLE_INVOICES = [
    {"subject_id": 1, "issued": -18, "due": -4, "status": "sent", "notes": None},
    {"subject_id": 2, "issued": -11, "due": 10, "status": "sent", "notes": "50% deposit taken on issue."},
    {"subject_id": 12, "issued": -2, "due": None, "status": "draft", "notes": None},
    {"subject_id": 4, "issued": -1, "due": 11, "status": "sent", "notes": "Rush order — balance due at pickup."},
]


def seed_sample_data(
    company_id: int | None = None,
    today: date | None = None,
) -> bool:
    """Add the demo clients/orders/invoices to a company that has none.

    **Dates are computed from `today`** (the real one unless a caller pins
    it), so the orders always straddle the day they were seeded rather than
    the day someone typed them — see `SAMPLE_ORDERS`. Seeding again next
    year produces the same shape of timeline, not an archive.

    Returns True if anything was inserted. Refuses (returns False) if the
    company already has clients — like `seed_if_empty()`, this fills an
    empty install rather than resetting a populated one. `company_id`
    defaults to the single company `seed_if_empty()` created.

    Also writes the placeholder letterhead, which a real install leaves
    blank until someone fills it in from /settings — the sample invoices
    below would print an empty header without it.
    """
    from billing.services import invoicing

    today = today or date.today()

    def day(offset):
        """A day offset from the seed date. None passes through — an
        invoice with no due date has to stay that way."""
        return None if offset is None else today + timedelta(days=offset)

    company = (
        db.session.get(Company, company_id) if company_id is not None
        else Company.query.first()
    )
    if company is None:
        return False
    if Client.query.filter_by(company_id=company.id).count() > 0:
        return False

    # The letterhead belongs to the billing module now, so it's seeded
    # through that module's API rather than as columns on Company.
    invoicing.update_profile(company.id, display_name=company.name, **SAMPLE_LETTERHEAD)

    order_types = {
        t.label: t
        for t in OrderType.query.filter_by(company_id=company.id).all()
    }

    for c in SAMPLE_CLIENTS:
        db.session.add(Client(
            id=c["id"], company_id=company.id,
            first_name=c["first_name"], last_name=c["last_name"],
            email=c["email"], phone=c["phone"],
            street=c["street"], city=c["city"],
            province=c["province"], postal_code=c["postal_code"],
        ))

    for o in SAMPLE_ORDERS:
        order_type = order_types.get(o.get("order_type"))
        # A cancelled order's reason is built here rather than written into
        # the literal above, so its date is an offset like every other date
        # in this file. Same wording cancel_order() produces.
        notes = o["notes"]
        if "cancelled" in o:
            offset, reason = o["cancelled"]
            notes = f"{notes}\n\nCancelled {day(offset).isoformat()}: {reason}"
        order = Order(
            id=o["id"], client_id=o["client_id"], item=o["item"],
            start=day(o["start"]), due=day(o["due"]),
            status=o["status"], is_rush=o.get("is_rush", False), notes=notes,
            order_type_id=order_type.id if order_type else None,
        )
        db.session.add(order)
        db.session.flush()  # assigns order.id if not already set
        for i, (description, quantity, unit_price) in enumerate(o["lines"]):
            db.session.add(OrderLine(
                order_id=order.id, description=description,
                quantity=quantity, unit_price=unit_price, sort_order=i,
            ))
        for amount, paid_offset, method, reference in o.get("payments", []):
            db.session.add(Payment(
                order_id=order.id, amount=amount, paid_date=day(paid_offset),
                method=method, reference=reference,
            ))

    db.session.flush()  # assigns order ids, needed by the adapter below

    # Raised and frozen through the billing module's own API, so the sample
    # data is never in a state the running app couldn't reach.
    from billing_adapter import billable_for

    for spec in SAMPLE_INVOICES:
        order = db.session.get(Order, spec["subject_id"])
        billable = billable_for(order)
        due_date = day(spec["due"])
        # The number comes from the billing module's own sequence, keyed off
        # the issue date's year — it can't be hardcoded here now that the
        # year moves with the seed date.
        invoice = invoicing.create_invoice(
            company.id, billable, due_date=due_date,
            display_name=company.name, today=day(spec["issued"]),
        )
        invoice.notes = spec["notes"]
        invoicing.set_status(
            company.id, invoice, spec["status"], billable,
            notes=spec["notes"] or "", due_date=due_date,
            display_name=company.name,
        )

    db.session.commit()
    return True
