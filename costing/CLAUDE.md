# Costing module (`costing/`)

> Part of the `leather_orders` app — see the root [CLAUDE.md](../CLAUDE.md) for
> stack, conventions and design language.

**This file is structure: why the module is shaped this way and what lives
where. [REQUIREMENTS.md](REQUIREMENTS.md) is behaviour** (`RC*`), with a
test-coverage map. Changing behaviour means changing REQUIREMENTS and the
code in the same commit.

Real costs per order: time logged, a labour rate, overhead, the margin that
leaves, and a calculator that suggests a price. Built because a product
review ranked it the top selling point: makers chronically underprice, and
the app already knew half the answer (inventory tracks material cost per
order) without ever putting it next to the price.

## The seam: three modules that don't know each other

An order's margin needs three numbers from three owners:

| Number | Owner | How the host gets it |
| --- | --- | --- |
| Revenue (pre-tax, frozen once invoiced) | billing | `Order.pre_tax_total` → `_amounts.subtotal` |
| Material cost | inventory | `inventory_service.total_material_cost(order_id)` |
| Labour, overhead, margin | costing | `costing_service.order_costs(..., revenue=, material_cost=)` |

`app.py`'s `order_costs()` and `_margin_figures()` are where they meet. Costing
takes the other two as plain floats, so it never imports a sibling (hard rule
4), and `pricing.py` takes plain floats too, so a quote can call it with an
estimate and no order at all. That's the whole reason the arithmetic is its
own stdlib-only file rather than methods on a model.

**Revenue is billing's subtotal, never the lines re-added.** Once an invoice
is out, `issued_subtotal` is what the client was charged; lines edited
afterwards (N5) would otherwise move a margin on work already billed. The
property lives on `Order` (host) because it's billing's answer about an
order, like `Order.total`.

## Rates: snapshot one, read the others live

The user chose this when the feature was designed (2026-11-27):

- **The hourly rate is snapshotted on each `TimeEntry`** (`RC4`), the
  `OrderMaterial.unit_price` pattern. Raising the rate in January doesn't
  make last year's orders look worse. The snapshot is an input, not a stored
  total, so hard rule 10 still holds: labour is minutes × rate on every read.
- **Overhead and the target margin are read live** (`RC8`, `RCN4`). They're
  assumptions about the shop, not facts about a session of work, and there's
  no natural row to freeze overhead on (it applies to materials too).
  Settings → Costs says so in plain words.
- **The calculator uses today's rate** (`RC21`): it prices work ahead.

## Data model (`costing/models.py`)

```python
CostingSettings(company_id PK, hourly_rate, overhead_percent,       # no row = defaults (RC10)
                target_margin_percent)
TimeEntry(id, company_id, order_id, user_id, worked_on, minutes,    # hourly_rate is the snapshot
          note, hourly_rate, created_at)
OrderEstimate(id, company_id, order_id UNIQUE, hours,               # the calculator's inputs;
              material_cost, target_margin_percent)                 # None = use the default
```

`order_id` is a real foreign key, as in inventory: nothing on `Order` reads
back from here, so there's no cycle. `user_id` is a foreign key with no
relationship (the module can't import `User`); the host passes a
`{user_id: name}` map to label entries. `company_id` is denormalized on every
row so every query filters by company directly.

**`settings_for()` never writes.** A company with no row gets an unsaved
default object; `update_settings()` adds it. Lazily *creating* on read (the
billing `profile_for` idiom) was the first version, but a GET that leaves an
uncommitted insert makes the usage writer skip the request and rolls back at
teardown, so a read stays a read.

All three tables are new, so `db.create_all()` covers them;
`costing/migrations.py` exists, empty, so the first column added later lands
there (hard rule 12).

## UI

- **Order page → Costs tab** (`/orders/<id>/costs`), between Materials and
  Billing: materials are a cost, Billing is what's charged, and Costs is
  where the two meet. The GET route is app.py's (like `order_materials()`),
  the partial and every form are the module's
  (`templates/costing/_order_costs.html`). Three sections, each with its own
  notice slot where it has forms (`time`, `pricing`): **Time**, **Margin**,
  **Pricing**. Underpricing is the amber `.warning-note`: it's a standing
  condition, which is exactly what amber is for (hard rule 7), and it adds no
  badge.
- **"Add as a line item"** posts to app.py's `add_suggested_line()`, not to
  this blueprint: an `OrderLine` is a host model. It only ever adds a line
  (`RC23`); the amount is the difference when lines exist, so the total lands
  on the suggestion instead of doubling it.
- **Settings → Costs** (`/settings/costs`): one form, framed by app.py's
  `settings_costs()`, posting to `costing.update_settings`.
- **Analytics → Margins**: three cards in `ANALYTICS_SECTIONS["margins"]`,
  draggable like the rest; the figures come from `_margin_figures()` in
  app.py, which reads `costing_service.labour_by_order()` (one query) and
  only considers orders with time logged (`RC31`).

## Not here, on purpose

- Showcase, its website serializer and catalog mode never see this module
  (`RC40`, `test_showcase_never_reads_costs`).
- No timer (`RCN1`), no per-type rate (`RCN2`), no quote document (`RCN3`);
  see REQUIREMENTS §7.
