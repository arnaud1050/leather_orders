# Inventory module (`inventory/`)

> Part of the `leather_orders` app — see the root [CLAUDE.md](../CLAUDE.md) for
> stack, conventions and design language.

**This file is structure: what lives where, and why the module is shaped this
way. [REQUIREMENTS.md](REQUIREMENTS.md) is behaviour** — every rule as a
numbered, checkable statement (`UN3`, `M6`, `V5`, …) with a test-coverage map
marking the gaps. Rules are cited here rather than restated; changing behaviour
means changing REQUIREMENTS and the code in the same commit.

Raw-materials tracking (leather, lining, hardware…) and what a given order drew
from stock, built as a **self-contained module** — own tables, own migrations,
own blueprint, own templates — on the same footprint as `billing/` and
`communications/`.

**Cost-tracking only** (`R0.1`, `R0.2`, `C2`): nothing here ever touches
`Order.total`, `OrderLine`, or an invoice. The client is billed exactly as
before via the Billing tab's line items; this module exists purely so the studio
can see what a job actually cost in materials, separate from what it charged.

**The rule that keeps it modular:** like `documents/`, `OrderMaterial` /
`OrderMaterialOther` carry a plain `order_id` foreign key into this app's
`orders` table rather than needing `billing/`'s adapter indirection — there's no
circular import forcing it, because nothing on `Order` reads back from this
module the way `Order.total` reads from `billing`. So `Order` itself needs no
relationship or import added; the module is queried by `order_id` directly
(`services.list_materials_for_order`, mirroring `documents.services.list_for_order`).

## Where the rules are

| Looking for | REQUIREMENTS.md § |
|---|---|
| Cost-tracking boundary | §0 (`R0.*`) |
| Units — catalog vs. company list | §1 (`UN*`) |
| Inventory types | §2 (`T*`) |
| Items | §3 (`I*`) |
| The order-material picker | §4 (`S*`) |
| Drawing a material, stock, snapshots | §5 (`M*`) |
| One-off "Other" costs | §6 (`O*`) |
| Total material cost | §7 (`C*`) |
| Tenant isolation & auth | §8 (`A*`) |
| UI behaviour | §9 (`U*`) |
| Stock alerts | §10 (`V*`) |
| Deliberate non-requirements | §11 (`N*`) |
| The master list's configurable columns | §9, `CFG*` |

## Units — why the split

The app ships a **broad, hardcoded catalog** of every unit a small maker's
business is likely to measure stock in. This started as a leatherworker's tool,
but what a material is measured in is one of the least leather-specific things
about the model — a ceramist counts glaze by the pound and clay by the bag, a
woodworker buys lumber by the board foot, a jeweler weighs metal in grams, a
fiber artist counts yarn in skeins. So the catalog spans Count/Length/Area/
Weight/Volume rather than the two units the app started with (Each, Sqft).

What's **company-configurable** is which of those keys are *offered* in the
Add/Edit item dropdown and in what order — never the catalog itself, and never
what `InventoryItem.unit` will *accept* (`UN9`, `N6`).

```python
InventoryUnit(id, company_id, key, sort_order, is_active)  # key -> a UNIT_CATALOG entry, not free text
```

- **`UNIT_CATALOG`** (`inventory/config.py`) is a plain dict — `key -> {label,
  group, whole}` — with ~35 entries across five `group`s. `group` only decides
  how the "Add a unit" dropdown's `<optgroup>`s are organised. **`whole`**
  generalises what used to be a literal `unit === 'sqft'` check in the Materials
  tab's JS: `True` for a count-like unit (Each, Skein, Box — quantity step `1`),
  `False` for anything divisible (Sqft, Yard, Gram — step `0.01`).
  `UNIT_LABELS` and `UNIT_WHOLE` are `{key: label}` / `{key: whole}` views over
  the same dict, for templates that just need one lookup by key.
- **"Each"** (`config.DEFAULT_UNIT`) is exempt from the hide-don't-delete shape
  (`UN5`), seeded lazily by `services._ensure_default_unit` — the same "create
  it on first use" idiom `billing.profile_for()` uses for a company's
  letterhead. Its *position* is not exempt (`UN4`, `UN12`).
- **The migration backfill** (`UN14`) exists because upgrading an installation
  that's been using "Sqft" all along would otherwise open Settings → Inventory
  and see only "Each" — the unit still perfectly valid, just invisible as
  something the company had "configured", which reads as if it had been lost.

## Data model (`inventory/models.py`)

```python
InventoryPref(id, company_id, columns)                           # JSON: the master list's column layout
InventoryType(id, company_id, label, sort_order, is_active)      # e.g. Leather, Lining, Hardware
InventoryItem(id, company_id, inventory_type_id, name, unit,     # unit: any UNIT_CATALOG key
              quantity_on_hand, unit_price,                      # low_stock_threshold: 0 = band off
              low_stock_threshold, is_active,
              reference, url, notes)                             # optional, descriptive, all nullable
OrderMaterial(id, company_id, order_id, inventory_item_id,       # a material drawn onto an order
              quantity_used, item_name, unit, unit_price)        # item_name/unit/unit_price are a SNAPSHOT
OrderMaterialOther(id, company_id, order_id, description, cost)  # one-off cost, no stock effect
```

- **`InventoryType`** has **no seed defaults** (`N4`) — unlike `OrderType`'s
  arbitrary starter list, and for the same reason as document types: every
  studio's materials are different, so a new company starts empty.
- **`InventoryItem`** is **editable in place** from `/inventory` (name, type,
  unit, quantity, price) — the one place in the app with real in-place editing
  of most fields, unlike `OrderLine`/`Payment`, because stock gets replenished
  and prices change and there's no invoice-freeze concern to protect against
  re-editing.
- **`OrderMaterial` snapshots** `item_name`/`unit`/`unit_price` (`M2`, `M3`) —
  the same shape `OrderLine` already has (a price typed in, not read off a live
  product record), and for the same reason the app freezes invoice amounts:
  raising an item's price later must not quietly reprice an order placed months
  ago. The live `inventory_item_id` link still exists so the item can be found
  and restocked on edit/delete (`M6`, `M10`) — it's just never read for its
  *current* price.
- **`reference` / `url` / `notes`** (`I14` … `I17`) are the supplier-facing
  half of an item: what *they* call it, where to buy it again, and whatever
  doesn't fit a field. Deliberately inert — no validation beyond `url`'s
  scheme check, no calculation reads them, and none is snapshotted onto
  `OrderMaterial` (a material freezes name/unit/price and nothing else,
  `M2`): a reorder link is a fact about the item *today*, not about what an
  order drew last March, so freezing a copy would only produce a second one
  to go stale. `url` is normalized rather than validated (`I16`) — a bare
  domain gets `https://`, a non-http(s) scheme is dropped, because the field
  exists to become an `href`.
- **Going negative is allowed, not blocked** (`M5`, `I8`) — this matches the
  app's warn-don't-block posture elsewhere (tax status, editing an issued
  order's lines). `/inventory` renders it in red
  (`.inventory-qty--negative`, reusing the `--day-today` token).

Every table here is new enough that `db.create_all()` covers it;
`inventory/migrations.py` carries the unit backfill (`UN14`) plus the
`ADDED_COLUMNS` entries for columns added to `inventory_items` after it first
shipped (hard rule 12): `low_stock_threshold` (`I13`), then
`reference`/`url`/`notes` (`I14`). It's called from `app.py` right after the
other modules' migrations. `inventory_prefs` needs no backfill at all — a
company with no row reads as the default layout (`CFG3`).

## UI

Behaviour is `U*` and `V*`; this is the markup map.

- **Settings → Inventory** (`/settings/inventory`) — **Units first, Inventory
  types second, list columns third**: the unit a material is measured in
  decides how its quantity behaves everywhere else, so it comes before the
  type a material belongs to; both define what an item *is*, so both come
  before a section that only decides how the table draws it.
  Units (`templates/inventory/_settings_units.html`) is drag-reorderable, the
  same native HTML5 pattern as `documents.reorder_types` and the Orders-list
  columns editor (`draggable="true"` items, a grip handle, a JSON `fetch` on
  `dragend`). Inventory types (`_settings_types.html`) is the plain
  hide-don't-delete `.settings-source-list` shape, no drag-reorder.
  **Inventory list columns** (`_settings_columns.html`, `CFG*`) is a
  deliberate copy of the Orders-list columns editor in `settings.html` —
  same markup, same drag handler, same immediate-`fetch()`-on-drop, same
  per-row Hide/Show form. It's the same control over a second table, and a
  second visual idiom for it would be one to learn for no reason. The one
  difference is the **Name** row, which carries an "(always shown)" tag
  instead of a Hide button (`CFG5`) — borrowed straight from the "Each"
  row's "(always available)" treatment two sections above it, trailing
  actions slot and all, so every row keeps the same three-child flex layout.
- **Master list** (`/inventory`, `inventory.inventory_list()`) — a top-level
  view in `.view-switch` right after Orders, the same "module owns a
  first-class page" precedent as `billing.invoice_list()` for `/invoices`.
  Sorting is server-side (`INVENTORY_SORT_KEYS` in `routes.py`), same
  convention as `orders_list()`/`clients_list()` and for the same reason: a
  type is a joined label, not a column SQL can sort on cheaply.

  **The table's columns are per-company** (`CFG*`) — order and visibility
  both, driven by `services.visible_columns()` off the same
  `config.INVENTORY_COLUMNS` declaration the Settings editor renders. Each
  entry names the `INVENTORY_SORT_KEYS` key its header links to, or `None`,
  which is what preserved the page's original hand-written split where
  Type/Name/Unit price carried a sort link and Unit/Quantity didn't. The
  **Actions** column isn't in the dict and isn't configurable (`CFG9`): it's
  the row's controls, not one of the item's fields. Notes render clamped to
  one ellipsized line (`.inventory-notes`, full text on `title`) — a note
  can run to a paragraph, and the modal is where a long one is actually read.

  **The Add/Edit modal's Unit `<select>`** is built from `services.list_units()`
  filtered to active-∪-currently-selected, plus a **defensive fallback
  `<option>`** labelled from the global `UNIT_CATALOG` rather than any
  `InventoryUnit` row. That fallback covers an item whose unit isn't tracked as
  a company row at all — the state every existing item was in before the
  backfill ran, and the state every service-level test creates directly.
  Without it, editing such an item would silently reset its unit to whatever
  the `<select>` happens to render first.

  **The "Show hidden" toggle** (`U11` … `U13`) is styled *identically* to the
  type filter buttons — plain `.legend__item`, wrapped in its own `.legend` div
  purely so it inherits that div's font-size/letter-spacing. Deliberately **not**
  `.legend__item--off`'s dimming/strikethrough, which reads as "this filter is
  suppressing something" and would be backwards here; its label swaps between
  "Show hidden" and "Hide hidden" instead. Its eye-slash icon is sized by a
  `.legend__item svg` rule, since a plain `.legend__item` carries no icon sizing
  of its own the way `.icon-btn` does.

  **Actions column** icons are shared macros in
  `templates/inventory/_icons.html` (pencil/trash/eye/eye-slash) so this page
  and the Materials tab draw them identically — the same `.icon-btn` component
  `documents/_explorer.html` introduced.
- **Order page → Materials tab** — the route `order_materials()` lives in
  `app.py` (alongside `order_billing()`) and renders a module-owned partial,
  `templates/inventory/_order_materials.html`: the same "app.py owns the tab
  route, the module owns what's inside it" split `documents/_explorer.html` has
  on the Details tab. See [docs/views.md](../docs/views.md) for the tab order.

  **The live cost estimate** (`U8`): each `<option>` carries a server-computed
  `data-step` (`config.UNIT_WHOLE[item.unit]`) plus `data-price`, and a small
  inline script reads both off the selected option to set the quantity input's
  step and show a "≈ $X.XX" estimate before submitting — the closest honest
  reading of "recalculate the total dynamically" without a JS framework.
  Computing the step server-side from `UNIT_WHOLE` is what lets any catalog unit
  work without the JS knowing unit keys at all.

## Stock alerts

**Two severity tiers over live stock, each at two scopes** (company-wide badge
+ order-scoped warning). Rules are `V1` … `V15`:

- **Red — out of stock** (`quantity_on_hand <= 0`): `out_of_stock_count` and
  `understocked_materials_for_order`. The urgent tier.
- **Amber — low stock** (`0 < quantity_on_hand <= low_stock_threshold`, i.e.
  `InventoryItem.is_low_stock`): `low_stock_count` and
  `low_stock_materials_for_order`. The softer "restock soon" tier, driven by
  the per-item `low_stock_threshold` (`I13`; `0` = tier off). The two bands are
  **disjoint by construction** (`> 0` excludes `<= 0`), so an item is in at
  most one and the counts never double-report it.

The wiring:

- **The nav badges** come from `_inject_nav_badge`, one
  `@bp.app_context_processor` in `routes.py` injecting *two* lazy callables
  (`out_of_stock_count`, `low_stock_count`) so each COUNT only runs on
  templates that render it, both forgiving of a logged-out request or a
  not-yet-migrated table/column — wired like communications' badges. Red
  `.nav-badge--stock-alert` (`--day-today`) and amber `.nav-badge--low-stock`
  (`--status-ready`) both show a **count** and sit side by side (red first)
  when both apply. Amber is a **deliberate fourth badge weight** — see root
  CLAUDE.md hard rule 7 and [docs/design.md](../docs/design.md); it's a
  genuinely milder severity than the red it sits beside, not a promoted count.
- **The Materials-tab warnings** render in `_order_materials.html` as
  `.warning-note`s — the same class the Billing tab's tax-status note uses. The
  out-of-stock one takes the red `.warning-note--alert` variant to match its
  red badge; the low-stock one keeps the plain amber `.warning-note`, so the
  two tiers read distinctly and a material appears in at most one.
- **The master list** (`inventory_list.html`) mirrors the same two tiers on
  each row's quantity cell: `.inventory-qty--negative` (red) when oversold,
  `.inventory-qty--low` (amber) when `is_low_stock`.

## Routes

All mutating routes live in `inventory/routes.py`'s blueprint, registered as
`inventory_routes.register(app, resolve_order=get_order_or_404)` in `app.py`.
The order-scoped ones (`/orders/<id>/materials/...`) use that hook for tenant
checking, exactly like `documents.routes.register` (`A4`); the item/type routes
(`/inventory/...`, `/settings/inventory-types/...`) filter by
`current_user.company_id` directly, since there's no order in the picture.

The two `GET` routes that render `order_page.html` / `settings.html`
(`order_materials()`, `settings_inventory()`) stay in `app.py`, matching how
`settings_orders()` / `order_billing()` are app-owned even though the partials
they include are module-owned.
