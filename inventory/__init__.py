"""
Inventory management, as a self-contained module.

Raw materials (leather, lining, hardware...) the studio has on hand, what
each cost, and what a given order actually consumed of them — pulled into an
order from the new Materials tab, decrementing stock as it goes. This is a
**cost-tracking** feature only: the total material cost computed here never
touches `Order.total`, `OrderLine`, or an invoice. The client is still billed
exactly as before, via the existing Billing tab's line items.

Same shape as `documents/`, which this module is deliberately modeled on:
its own tables, its own migrations, its own blueprint, its own templates,
and the rest of the app only ever calls `inventory.services`. `Order.id` is
a real foreign key here too (no adapter indirection needed, same reasoning
`documents/__init__.py` gives for its own `Document.order_id`) — nothing in
root `models.py` needs to change; `Order` carries no relationship back to
this module's tables, they're just queried by `order_id` directly (see
`services.list_materials_for_order`, mirroring
`documents.services.list_for_order`).

Layers, and what each is for:

- **`config.py`** — the fixed vocabularies this module declares rather than
  stores: the unit catalog (`UNIT_CATALOG`) and the master list's column set
  (`INVENTORY_COLUMNS`). A company configures a *subset and order* of each;
  neither list itself is per-company.
- **`models.py`** — `InventoryUnit`, `InventoryPref`, `InventoryType`,
  `InventoryItem`, `OrderMaterial`, `OrderMaterialOther`.
- **`migrations.py`** — this module's own column migrations (the item's
  low-stock threshold, and its reference/url/notes fields — every table here
  is new enough that `db.create_all()` still covers the tables themselves),
  plus the `InventoryUnit` data backfill.
- **`services.py`** — the public API. Every function takes `company_id` (or
  an already tenant-checked `order_id`) first.
- **`routes.py`** — the Flask blueprint, registered with a host-supplied
  `resolve_order` hook, same pattern as `documents.routes.register`.
"""

from inventory import models  # noqa: F401 — registers the tables with db.create_all()
