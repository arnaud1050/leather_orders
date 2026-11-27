"""
The module's public API. Every function takes `company_id` first, or an
`order_id` the host has already tenant-checked (`get_order_or_404`), and
filters on it — `tests/test_costing.py` checks that a foreign row is never
reached.

What this module does *not* know, by design: what an order earned (the host
asks billing) or what its materials cost (the host asks inventory). Those
come in as plain numbers to `order_costs()` and `estimate_suggestion()`, so
costing never imports a sibling module (hard rule 4).
"""

from dataclasses import dataclass
from datetime import date

from models import db

from costing import pricing
from costing.models import CostingSettings, OrderEstimate, TimeEntry

# Generous on purpose: an entry is a session of work, and a long day at the
# bench is still one entry. More than a day's worth in one go is a typo.
MAX_ENTRY_MINUTES = 24 * 60
NOTE_MAX = 200
# A rate or a percentage a form can't sensibly mean. Refused, not clamped:
# "1e9" is a typo, and saving it would make every margin nonsense.
MAX_RATE = 10_000.0
MAX_OVERHEAD = 1_000.0
MAX_HOURS = 10_000.0


# ---------------------------------------------------------------------------
# Settings — Settings → Costs
# ---------------------------------------------------------------------------

DEFAULT_TARGET_MARGIN = 30.0


def settings_for(company_id: int) -> CostingSettings:
    """The company's costs settings (RC10). A company that has never saved
    any reads the defaults — no rate, no overhead, a 30% target — from an
    unsaved row, so a page that only reads never writes; the row is stored
    the first time someone saves."""
    row = db.session.get(CostingSettings, company_id)
    if row is None:
        row = CostingSettings(
            company_id=company_id, hourly_rate=0.0,
            overhead_percent=0.0, target_margin_percent=DEFAULT_TARGET_MARGIN,
        )
    return row


def update_settings(
    company_id: int, *, hourly_rate: float | None = None,
    overhead_percent: float | None = None,
    target_margin_percent: float | None = None,
) -> CostingSettings:
    """Write whichever values were given; None leaves a value alone (the
    caller's "field not on the form" case, hard rule 9). Raises ValueError
    for a value out of range, before writing anything."""
    if hourly_rate is not None and not 0 <= hourly_rate <= MAX_RATE:
        raise ValueError("The hourly rate has to be between $0 and $10,000.")
    if overhead_percent is not None and not 0 <= overhead_percent <= MAX_OVERHEAD:
        raise ValueError("Overhead has to be between 0% and 1000%.")
    if target_margin_percent is not None and not 0 <= target_margin_percent <= pricing.MAX_TARGET_MARGIN:
        raise ValueError("The target margin has to be between 0% and 95%.")
    row = settings_for(company_id)
    if row not in db.session:
        db.session.add(row)
    if hourly_rate is not None:
        row.hourly_rate = round(hourly_rate, 2)
    if overhead_percent is not None:
        row.overhead_percent = round(overhead_percent, 2)
    if target_margin_percent is not None:
        row.target_margin_percent = round(target_margin_percent, 2)
    db.session.flush()
    return row


# ---------------------------------------------------------------------------
# Time entries — the Costs tab's Time section
# ---------------------------------------------------------------------------

def list_entries(order_id: int) -> list[TimeEntry]:
    """Newest work first, then newest logged first within a day."""
    return (
        TimeEntry.query.filter_by(order_id=order_id)
        .order_by(TimeEntry.worked_on.desc(), TimeEntry.id.desc())
        .all()
    )


def add_entry(
    company_id: int, order_id: int, *, user_id: int | None, worked_on: date,
    minutes: int, note: str | None = None,
) -> TimeEntry:
    """Log time on an order at today's company rate (RC3, RC4). Raises
    ValueError for a duration that isn't one."""
    if minutes <= 0:
        raise ValueError("Enter how long you worked — hours, minutes, or both.")
    if minutes > MAX_ENTRY_MINUTES:
        raise ValueError("One entry can't be more than 24 hours. Log each day separately.")
    note = " ".join((note or "").split())[:NOTE_MAX] or None
    entry = TimeEntry(
        company_id=company_id, order_id=order_id, user_id=user_id,
        worked_on=worked_on, minutes=minutes, note=note,
        hourly_rate=settings_for(company_id).hourly_rate,
    )
    db.session.add(entry)
    db.session.flush()
    return entry


def delete_entry(order_id: int, entry_id: int) -> bool:
    """Scoped by order: the right entry id under the wrong order does
    nothing (RC5)."""
    entry = TimeEntry.query.filter_by(id=entry_id, order_id=order_id).first()
    if entry is None:
        return False
    db.session.delete(entry)
    db.session.flush()
    return True


def delete_for_order(order_id: int) -> None:
    """Everything this module holds about an order, for the host's hard
    delete — `Order` has no relationship to cascade through."""
    TimeEntry.query.filter_by(order_id=order_id).delete()
    OrderEstimate.query.filter_by(order_id=order_id).delete()
    db.session.flush()


# ---------------------------------------------------------------------------
# What an order cost — the Margin section
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OrderCosts:
    """An order's breakdown, plus what it should have been priced at."""

    breakdown: pricing.Breakdown
    minutes: int
    target_margin_percent: float
    hourly_rate: float

    @property
    def has_time(self) -> bool:
        return self.minutes > 0

    @property
    def should_have_charged(self) -> float:
        return self.breakdown.price_for(self.target_margin_percent)

    @property
    def under_target(self) -> bool:
        """The price came in below what the target margin needed."""
        return self.breakdown.revenue + 0.005 < self.should_have_charged


def _labour(entries) -> tuple[int, float]:
    minutes = sum(entry.minutes for entry in entries)
    labour = round(sum(entry.labour_cost for entry in entries), 2)
    return minutes, labour


def order_costs(company_id: int, order_id: int, *, revenue: float, material_cost: float) -> OrderCosts:
    """The Margin section's figures (RC6–RC9). `revenue` is the order's
    pre-tax price — the frozen invoice figure once it's issued — and
    `material_cost` inventory's total; the host supplies both."""
    settings = settings_for(company_id)
    minutes, labour = _labour(list_entries(order_id))
    return OrderCosts(
        breakdown=pricing.breakdown(
            revenue=revenue, material_cost=material_cost, labour_cost=labour,
            hours=minutes / 60, overhead_percent=settings.overhead_percent,
        ),
        minutes=minutes,
        target_margin_percent=settings.target_margin_percent,
        hourly_rate=settings.hourly_rate,
    )


def labour_by_order(company_id: int) -> dict[int, tuple[int, float]]:
    """{order_id: (minutes, labour dollars)} for every order of the company
    with time logged — one query, for /analytics (RC30)."""
    totals: dict[int, list] = {}
    for entry in TimeEntry.query.filter_by(company_id=company_id):
        minutes, labour = totals.setdefault(entry.order_id, [0, 0.0])
        totals[entry.order_id] = [minutes + entry.minutes, labour + entry.labour_cost]
    return {order_id: (m, round(c, 2)) for order_id, (m, c) in totals.items()}


# ---------------------------------------------------------------------------
# The pricing calculator — the Pricing section
# ---------------------------------------------------------------------------

def estimate_for(order_id: int) -> OrderEstimate | None:
    return OrderEstimate.query.filter_by(order_id=order_id).first()


def save_estimate(
    company_id: int, order_id: int, *, hours: float,
    material_cost: float | None, target_margin_percent: float | None,
) -> OrderEstimate:
    """Keep the calculator's inputs for this order (RC20). Raises ValueError
    for hours or a margin out of range."""
    if not 0 <= hours <= MAX_HOURS:
        raise ValueError("Estimated hours have to be between 0 and 10,000.")
    if material_cost is not None and material_cost < 0:
        raise ValueError("The materials estimate can't be negative.")
    if target_margin_percent is not None and not 0 <= target_margin_percent <= pricing.MAX_TARGET_MARGIN:
        raise ValueError("The target margin has to be between 0% and 95%.")
    row = estimate_for(order_id)
    if row is None:
        row = OrderEstimate(company_id=company_id, order_id=order_id)
        db.session.add(row)
    row.hours = round(hours, 2)
    row.material_cost = None if material_cost is None else round(material_cost, 2)
    row.target_margin_percent = (
        None if target_margin_percent is None else round(target_margin_percent, 2))
    db.session.flush()
    return row


def estimate_suggestion(company_id: int, order_id: int, *, material_cost_on_order: float) -> pricing.Suggestion | None:
    """The calculator's answer for an order, or None before it's been given
    an estimate. Uses today's rate and overhead: it prices work ahead
    (RC21)."""
    estimate = estimate_for(order_id)
    if estimate is None:
        return None
    settings = settings_for(company_id)
    return pricing.suggest_price(
        hours=estimate.hours,
        hourly_rate=settings.hourly_rate,
        material_cost=(material_cost_on_order if estimate.material_cost is None
                       else estimate.material_cost),
        overhead_percent=settings.overhead_percent,
        target_margin_percent=pricing.clean_target_margin(
            estimate.target_margin_percent, settings.target_margin_percent),
    )
