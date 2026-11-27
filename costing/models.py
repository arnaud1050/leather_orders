"""
Tables: `costing_settings`, `order_time_entries`, `order_estimates`.

`TimeEntry` and `OrderEstimate` carry a plain `order_id` foreign key into the
host's `orders` table, like `inventory.OrderMaterial` — nothing on `Order`
reads back from this module, so there's no cycle to break with an adapter.
`company_id` is denormalized onto every row for the same reason inventory
does it: every query filters by company first, and a tenant filter that
depends on joining through order → client → company is one that eventually
gets skipped.

`TimeEntry.user_id` is a foreign key into `users` but no relationship: this
module may not import the host's `User`. The host hands the Costs tab a
`{user_id: name}` map to label entries with — users are deactivated, never
deleted (hard rule 8), so the id always resolves.
"""

from datetime import datetime, timezone

from models import db


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CostingSettings(db.Model):
    """A company's labour rate, overhead and default target margin (RC10).

    One row per company, created on first read (`services.settings_for`),
    the same lazy idiom as billing's `profile_for`: a company created any
    way at all has costs settings the moment something asks. Zero rate
    means "not set yet", which the Costs tab says out loud rather than
    presenting a margin with free labour in it.
    """
    __tablename__ = "costing_settings"

    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), primary_key=True)
    hourly_rate = db.Column(db.Float, nullable=False, default=0.0)
    overhead_percent = db.Column(db.Float, nullable=False, default=0.0)
    target_margin_percent = db.Column(db.Float, nullable=False, default=30.0)


class TimeEntry(db.Model):
    """Time someone spent on an order (RC1–RC5).

    `hourly_rate` is a **snapshot** of the company rate when the entry was
    logged, like `OrderMaterial.unit_price`: raising the rate in January must
    not rewrite what last year's orders cost (RC4). It's an input frozen at
    entry, not a derived total — the labour cost is still computed on every
    read (hard rule 10).
    """
    __tablename__ = "order_time_entries"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    worked_on = db.Column(db.Date, nullable=False)
    minutes = db.Column(db.Integer, nullable=False)
    note = db.Column(db.String(200))
    hourly_rate = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime, nullable=False, default=_utcnow)

    @property
    def hours(self) -> float:
        return self.minutes / 60

    @property
    def labour_cost(self) -> float:
        return round(self.minutes / 60 * self.hourly_rate, 2)


class OrderEstimate(db.Model):
    """What the pricing calculator was last given for an order (RC20).

    Kept so the suggested price stays on the page next to the actual one —
    the whole point is that underpricing stays visible, not that it flashes
    up once. Inputs only; the suggestion itself is computed on every read.
    `material_cost` None means "use the materials already on the order",
    `target_margin_percent` None means "the company default".
    """
    __tablename__ = "order_estimates"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, nullable=False, index=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, unique=True)
    hours = db.Column(db.Float, nullable=False, default=0.0)
    material_cost = db.Column(db.Float)
    target_margin_percent = db.Column(db.Float)
