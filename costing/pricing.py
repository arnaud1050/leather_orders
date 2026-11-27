"""
The arithmetic of what a job cost and what it should be priced at.

Pure functions over plain numbers, standard library only — no database, no
Flask, no idea what an order is. That's deliberate: the order page's Costs
tab calls it with an order's figures today, and a quote (planned, not built)
will call it with a quote's estimate tomorrow, without either having to
pretend to be the other. `tests/test_costing.py` holds this file to the
stdlib-only rule, the same way `billing/tax.py` is held to it.

The definitions (costing `RC*`):

* **labour** — hours × rate. On a real order each time entry carries the
  rate in force when it was logged, so the caller passes the labour dollars
  it already summed; the calculator, which looks forward, passes hours and
  today's rate.
* **overhead** — a percentage of materials + labour: rent, tools,
  consumables, spread over the work that used them.
* **cost** — materials + labour + overhead.
* **profit** — revenue − cost. **margin** — profit ÷ revenue.
* **effective hourly rate** — (revenue − materials − overhead) ÷ hours:
  what each hour actually earned once the job paid for its materials and
  its share of the shop. It equals the labour rate plus profit per hour, so
  above the rate means the job made money on top of the maker's wage.
* **suggested price** — cost ÷ (1 − target margin): the price at which the
  target margin comes out exactly.
"""

from dataclasses import dataclass

# A margin of 100% or more has no price that reaches it (cost ÷ 0), and a
# target that close to it produces prices nobody would quote. Capped, not
# refused: the calculator still answers.
MAX_TARGET_MARGIN = 95.0


def _money(value: float) -> float:
    return round(value + 0.0, 2)


def overhead_on(material_cost: float, labour_cost: float, overhead_percent: float) -> float:
    """Overhead in dollars: a share of materials + labour."""
    return _money((material_cost + labour_cost) * max(overhead_percent, 0.0) / 100)


def clean_target_margin(percent: float | None, default: float) -> float:
    """A usable target margin: the default for nothing, clamped to
    0 … MAX_TARGET_MARGIN otherwise."""
    if percent is None:
        percent = default
    return min(max(percent, 0.0), MAX_TARGET_MARGIN)


def price_for_margin(cost: float, target_margin_percent: float) -> float:
    """The price at which `cost` leaves `target_margin_percent` of it as
    profit."""
    margin = min(max(target_margin_percent, 0.0), MAX_TARGET_MARGIN) / 100
    return _money(cost / (1 - margin))


@dataclass(frozen=True)
class Breakdown:
    """What a job cost and earned. Every figure in dollars except where
    named; `margin_percent` and `effective_hourly` are None when there's no
    revenue or no hours to divide by, rather than a misleading zero."""

    revenue: float
    material_cost: float
    labour_cost: float
    overhead: float
    hours: float

    @property
    def cost(self) -> float:
        return _money(self.material_cost + self.labour_cost + self.overhead)

    @property
    def profit(self) -> float:
        return _money(self.revenue - self.cost)

    @property
    def margin_percent(self) -> float | None:
        if self.revenue <= 0:
            return None
        return round(self.profit / self.revenue * 100, 1)

    @property
    def effective_hourly(self) -> float | None:
        if self.hours <= 0:
            return None
        return _money((self.revenue - self.material_cost - self.overhead) / self.hours)

    def price_for(self, target_margin_percent: float) -> float:
        """What this job should have been priced at, from what it actually
        cost, to make `target_margin_percent`."""
        return price_for_margin(self.cost, target_margin_percent)


def breakdown(
    *, revenue: float, material_cost: float, labour_cost: float,
    hours: float, overhead_percent: float,
) -> Breakdown:
    """The costs and margin of a job already (partly) done."""
    return Breakdown(
        revenue=_money(revenue),
        material_cost=_money(material_cost),
        labour_cost=_money(labour_cost),
        overhead=overhead_on(material_cost, labour_cost, overhead_percent),
        hours=round(hours, 2),
    )


def combine(breakdowns) -> Breakdown:
    """Several jobs as one: dollars and hours summed, so the margin and the
    hourly rate are weighted by size — a $40 card holder can't outvote a
    $900 briefcase the way an average of percentages would let it."""
    rows = list(breakdowns)
    return Breakdown(
        revenue=_money(sum(b.revenue for b in rows)),
        material_cost=_money(sum(b.material_cost for b in rows)),
        labour_cost=_money(sum(b.labour_cost for b in rows)),
        overhead=_money(sum(b.overhead for b in rows)),
        hours=round(sum(b.hours for b in rows), 2),
    )


@dataclass(frozen=True)
class Suggestion:
    """The calculator's answer: a price, and what it's made of."""

    hours: float
    hourly_rate: float
    material_cost: float
    labour_cost: float
    overhead: float
    target_margin_percent: float
    price: float

    @property
    def cost(self) -> float:
        return _money(self.material_cost + self.labour_cost + self.overhead)


def suggest_price(
    *, hours: float, hourly_rate: float, material_cost: float,
    overhead_percent: float, target_margin_percent: float,
) -> Suggestion:
    """Price a job from its estimate: hours at today's rate, the materials,
    overhead on both, then the target margin on top."""
    hours = max(hours, 0.0)
    labour = _money(hours * max(hourly_rate, 0.0))
    material_cost = _money(max(material_cost, 0.0))
    overhead = overhead_on(material_cost, labour, overhead_percent)
    target = min(max(target_margin_percent, 0.0), MAX_TARGET_MARGIN)
    return Suggestion(
        hours=round(hours, 2),
        hourly_rate=_money(max(hourly_rate, 0.0)),
        material_cost=material_cost,
        labour_cost=labour,
        overhead=overhead,
        target_margin_percent=target,
        price=price_for_margin(material_cost + labour + overhead, target),
    )
