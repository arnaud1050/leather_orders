"""
Real costs per order, as a self-contained module.

What an order really cost — materials, the maker's time at their own rate,
a share of overhead — against what it was priced at, and what it *should*
have been priced at to make the studio's target margin. Built because
makers chronically underprice, and a margin nobody computes is a margin
nobody notices is negative.

Same shape as `inventory/`: own tables, own migrations, own blueprint, own
templates, and the rest of the app only calls `costing.services` (and the
pure `costing.pricing`). It never imports a sibling module — the host reads
revenue from billing and materials from inventory and hands both in as
numbers — and it never imports a host model, only `db`
(`tests/test_costing.py` enforces both).

Layers:

- **`pricing.py`** — the arithmetic, standard library only, reusable by a
  future quote without an order in sight.
- **`models.py`** — `CostingSettings`, `TimeEntry`, `OrderEstimate`.
- **`migrations.py`** — this module's column migrations (none yet).
- **`services.py`** — the public API.
- **`routes.py`** — the blueprint, registered with the host's
  `resolve_order` hook.

Costs are internal. Nothing here may reach Showcase's website serializer or
catalog mode, which never import this module (`RC40`).
"""

from costing import models  # noqa: F401 — registers the tables with db.create_all()
