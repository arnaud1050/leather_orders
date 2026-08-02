# Inventory module — business requirements & rules

This is the living spec for `inventory/`: every rule the module is supposed to
enforce, written as a checkable statement rather than prose. It exists so
requirements don't only live in code (or in someone's head), and so test
coverage can be checked against something. When behavior changes on purpose,
update the rule here in the same commit — if a rule and the code disagree,
one of them is a bug.

Each rule has an id (e.g. `M4`) referenced from `tests/test_inventory.py` where
practical, and from the **Test coverage map** at the bottom of this file.
`CLAUDE.md`'s "Inventory module" section explains the *why* behind these
choices; this file is the checklist of *what must hold true*.

## 0. Scope

- **R0.1 — Cost-tracking only.** This module must never create, edit, or read
  billing data. It has no relationship to `OrderLine`, `Invoice`, or tax
  calculation, and no function here may write to `Order.total`.
- **R0.2 — The client is billed exactly as before.** Nothing added to an
  order's Materials tab changes what the Billing tab shows or what an
  invoice says.

## 1. Inventory types (`InventoryType`)

- **T1.** A type belongs to exactly one company (`company_id`) and has a
  `label`, `sort_order`, and `is_active` flag.
- **T2.** Creating a type with a blank/whitespace-only label is rejected —
  no row is created.
- **T3.** Creating a type whose label exactly matches an existing type's
  label for the same company — case-insensitively, whitespace-trimmed — is
  rejected as a duplicate. This includes a match against a **hidden** type.
- **T4.** Duplicate checking is scoped per company: the same label is
  allowed in a different company.
- **T5.** A new type is assigned the next `sort_order` (a running count for
  that company), so types display in creation order by default.
- **T6.** Toggling a type flips `is_active` (hide ↔ unhide) and is scoped to
  the owning company — toggling an id belonging to another company is a
  no-op.
- **T7.** Deleting a type only succeeds when `can_delete` is true, i.e. **no
  `InventoryItem` references it**. Deleting a referenced type is a no-op;
  the type stays exactly as it was.
- **T8.** The settings page lists every type for a company — active and
  hidden both.
- **T9.** `has_types(company_id)` reports whether the company has defined
  **any** `InventoryType` at all — active or hidden. Used to decide whether
  the master list's "No Type" filter button can appear at all (see U10) —
  existence, not `is_active`, same "does the category exist" question
  `has_order_types` asks elsewhere in the app.

## 2. Inventory items (`InventoryItem`)

- **I1.** An item belongs to exactly one company, may optionally belong to
  one `InventoryType` (nullable), and has `name`, `unit`, `quantity_on_hand`,
  `unit_price`, `is_active`.
- **I2.** `unit` must be one of the fixed set `{"each", "sqft"}`
  (`inventory/config.py`'s `UNIT_LABELS`) — nothing else is a valid unit.
- **I3.** Creating an item requires a non-blank `name` **and** a valid
  `unit`. Either failing rejects the whole creation — no row is created.
- **I4.** An `inventory_type_id` supplied at creation that doesn't belong to
  the same company is silently dropped (the item is created with no type)
  rather than rejecting the creation.
- **I5.** New items default to `is_active = True`.
- **I6.** Editing an item is a **partial update**, unlike creation: a blank
  `name` is ignored (the existing name is kept) instead of failing the
  whole edit; an invalid `unit` is likewise ignored (existing unit kept).
  `quantity_on_hand` and `unit_price` are always overwritten with whatever
  was supplied.
- **I7.** Editing an item's `inventory_type_id` to empty clears the type;
  supplying an id belonging to another company resolves to "no type" (same
  drop-silently behavior as I4).
- **I8.** Setting (creating or editing) a **negative** `quantity_on_hand` is
  allowed — not blocked.
- **I9.** `edit_item` / `toggle_item` / `delete_item` are scoped to the given
  `company_id` — operating on another company's item id is a no-op that
  leaves the row untouched.
- **I10.** Toggling an item flips `is_active`.
- **I11.** Deleting an item only succeeds when `can_delete` is true, i.e.
  **no `OrderMaterial` references it**. Deleting a referenced item is a
  no-op.
- **I12.** The master list returns every item for a company — active and
  hidden both; sort order is a presentation concern (see §7), not a
  property of the stored data.

## 3. Selectable items (the "add material" picker on an order)

- **S1.** The items offered are: every **active** item for the company,
  union any item — active or hidden — already used as an `OrderMaterial` on
  *that specific order*.
- **S2.** A hidden item with no prior use on the order in question does not
  appear in the picker.
- **S3.** The merged, deduplicated set is ordered by (type's `sort_order`,
  item name); untyped items sort last.

## 4. Drawing a material onto an order (`OrderMaterial`)

- **M1.** Adding a material requires an `inventory_item_id` that resolves to
  a real item belonging to the **same company** as the order, and a
  `quantity_used` that is present and strictly greater than zero. Any
  failure is a silent no-op — no row created, no stock touched.
- **M2.** On success, the new row **snapshots** `item_name`, `unit`, and
  `unit_price` off the `InventoryItem` at that moment. It does not keep a
  live reference for pricing.
- **M3.** A later edit to the source item's `name` / `unit` / `unit_price`
  must **never** change what an already-created `OrderMaterial` row reports.
- **M4.** Adding a material **decrements** the source item's
  `quantity_on_hand` by `quantity_used`.
- **M5.** `quantity_on_hand` is allowed to go **negative** as a result — not
  blocked. The master `/inventory` page is the only place this is flagged
  (rendered in red).
- **M6.** Editing a material's `quantity_used` adjusts the source item's
  `quantity_on_hand` by the **delta** (old minus new) — it does not
  recompute stock from scratch.
- **M7.** Editing a material never changes its snapshotted `item_name` /
  `unit` / `unit_price` — only `quantity_used` can be edited after creation.
- **M8.** Editing a material with a missing, zero, or negative
  `quantity_used` is rejected — the existing row and stock are untouched.
- **M9.** Editing or deleting a material is scoped by `order_id`: the
  correct `material_id` under the wrong `order_id` is a no-op.
- **M10.** Deleting a material **restores** its full `quantity_used` back
  onto the source item's `quantity_on_hand`, then removes the row.
- **M11.** A material's cost = `quantity_used × unit_price`, using the
  frozen (snapshotted) unit price, never a live one.
- **M12.** The materials list for an order returns every row for that order.

## 5. One-off costs (`OrderMaterialOther`)

- **O1.** An "Other" cost is not tied to any `InventoryItem` and never
  touches stock — by construction, it has no such foreign key.
- **O2.** Adding one requires a non-blank `description` and a `cost` that is
  present (may be `0`, must not be missing/`None`). Either failing is a
  no-op.
- **O3.** Editing one requires the same (non-blank description, non-`None`
  cost); either failing leaves the existing row untouched.
- **O4.** Editing/deleting is scoped by `order_id`, same as materials (M9).
- **O5.** Deleting removes the row outright — there's nothing to restore.

## 6. Total material cost

- **C1.** The total = sum of every material's cost (M11) on that order,
  plus the sum of every "Other" cost (§5) on that order.
- **C2.** This figure is derived on every read, never stored, and is
  **never** written to `Order.total`, `OrderLine`, or an invoice (R0.1/R0.2)
  — adding, editing, or removing a material or an Other must leave the
  order's client-facing total completely unaffected.

## 7. Tenant isolation & auth

- **A1.** Every mutating route (`/inventory/...`, `/settings/inventory-types/...`,
  `/orders/<id>/materials/...`) requires an authenticated session — an
  anonymous POST redirects to `/login`.
- **A2.** `/inventory` (the master list) and `/orders/<id>/materials` (the
  Materials tab) both require login to view.
- **A3.** Type/item management routes trust `current_user.company_id` —
  every service call filters on it, so an id belonging to another company
  is inert (see T6, T7, I9, I11).
- **A4.** Order-scoped material/Other routes resolve the order through the
  host's tenant check first — an order belonging to another company
  **404s the whole request** before any service function runs, rather than
  quietly no-op-ing.
- **A5.** `/settings/inventory` lists types for the current company only.

## 8. UI behavior

- **U1.** The master list is sortable by **Type**, **Name**, or **Unit
  price** (`?sort=&dir=`), defaulting to Type ascending; sorting by Type
  breaks ties by name.
- **U2.** The list is filterable by type via legend buttons — one per type
  actually represented among the company's items — persisted client-side in
  `localStorage` (`inventory-hidden-types`), independent of any other page's
  filters.
- **U3.** "+ Add item" opens a modal to create a new item, not an inline
  form on the page. It sits on the same row as the type filters, pushed to
  the right (same layout as the Timeline/Orders "+ New order" button), and
  is always present regardless of whether any filters are showing.
- **U4.** Clicking an item's name opens its own edit modal (name / type /
  unit / quantity / price only — Hide/Delete are not in this modal).
- **U5.** Each row's Actions column offers Hide/Unhide and Delete (the
  latter only when `can_delete`) as icon buttons with text tooltips.
- **U6.** A negative `quantity_on_hand` renders in a visually distinct
  (red) style on the master list.
- **U7.** The order page's Materials tab sits between Details and Billing,
  both in the tab navigation and in the rendered section order.
- **U8.** Adding a material groups the item picker by type (`<optgroup>`),
  switches the quantity input's step between whole numbers (each) and
  hundredths (sqft) based on the selected item, and shows a live estimated
  cost before submitting.
- **U9.** Material and Other rows use pencil (edit) / trash (delete) icon
  buttons with tooltips instead of a text "Remove" button.
- **U10.** A "No Type" filter button only appears when the company has
  **both** (a) defined at least one `InventoryType` (T9) **and** (b) at
  least one item with none. With zero types ever defined, "No Type" is not
  offered at all — every item is untyped by definition, so a filter for
  that single, all-or-nothing bucket would be pointless.
- **U11.** Hidden items (`is_active = False`) are **not visible by default**
  on the master list.
- **U12.** A "Show hidden" toggle appears whenever at least one item is
  hidden — regardless of whether any type filter buttons are showing — and
  reveals hidden items when clicked. It's persisted client-side in
  `localStorage` (`inventory-show-hidden-items`), a separate key from the
  type filter's, and combines with it: a hidden item whose type is also
  filtered out stays hidden even with hidden items shown. It's styled
  **identically** to the type filter buttons (same plain `.legend__item`
  class, same font-size/weight/color, no dimming or strikethrough) — the
  only difference is the "Hide" (eye-slash) icon the table's Actions column
  also uses, and its own label swapping between **"Show hidden"** (default)
  and **"Hide hidden"** (once clicked) to say what it currently does, rather
  than a style change signaling on/off the way the type buttons do.
- **U13.** The "Show hidden" toggle is absent entirely when no item is
  hidden — there being nothing for it to reveal.

## 9. Stock alerts (out-of-stock nav badge + Materials-tab warning)

Two separate surfaces reading the same underlying fact — an item at zero or
negative stock — at two different scopes: company-wide (the badge) and
order-specific (the warning on that order's Materials tab).

- **V1.** `out_of_stock_count(company_id)` counts every **active**
  `InventoryItem` for that company with `quantity_on_hand <= 0`. Hidden
  items are excluded — an item taken out of active use isn't something
  anyone needs to restock.
- **V2.** The count is scoped per company — an item belonging to another
  company is never counted, same tenant boundary as every other query in
  this module.
- **V3.** The nav badge next to "Inventory" in `base.html` renders only when
  the count is greater than zero, shows the **count itself** (not a static
  symbol), and is styled red (`.nav-badge--stock-alert`) — the same red
  `--day-today` token the integration-alert badge uses for "something needs
  attention", not a new fourth badge meaning.
- **V4.** The count is **derived on every page load, never stored** — there
  is no separate "resolved"/"acknowledged" flag. It clears itself the moment
  every affected item's `quantity_on_hand` is edited back above zero (a
  restock) and reappears the moment a later material draw pushes another
  item to zero or below, with no way to drift out of sync with the data and
  nothing for a user to explicitly dismiss.
- **V5.** `understocked_materials_for_order(order_id)` returns the
  `OrderMaterial` rows on that order whose **live** linked item currently has
  `quantity_on_hand <= 0` — reading the item's current quantity, never the
  material's own frozen `item_name`/`unit`/`unit_price` snapshot (M2/M3),
  since stock is a live, shared figure that every other order and restock
  can move, unlike a price that's deliberately frozen at draw time.
- **V6.** The Materials tab shows a warning banner listing each affected
  material by name (`item_name`) whenever `understocked_materials_for_order`
  returns at least one row; the banner is absent when it returns none.
- **V7.** `OrderMaterialOther` rows are never considered here — they carry no
  `inventory_item_id` at all (O1), so there is nothing live to check.
- **V8.** Both surfaces are read-only signals with no independent state: they
  say what's currently true, not what happened historically, so neither can
  disagree with `/inventory`'s own per-row red-quantity flag (U6).

## 10. Explicit non-requirements

These are deliberate omissions, not oversights — listed so nobody "fixes"
them without checking first. See CLAUDE.md's "Known gaps" for the reasoning.

- **N1.** No bulk/CSV import for inventory items.
- **N2.** No inventory-**value** reporting (total stock value in dollars),
  and no *threshold*-based low-stock alerting (e.g. "warn under 5 units on
  hand"). What exists is a hard zero-or-negative signal only — the per-row
  red flag on `/inventory` (U6), the nav badge, and the Materials-tab
  warning (both §9) — not a configurable reorder point.
- **N3.** A material's item/unit/snapshotted price cannot be changed after
  creation — only `quantity_used` (M7). Changing the item means deleting
  and re-adding.
- **N4.** No seed/default data for `InventoryType`/`InventoryItem` — every
  company starts empty.
- **N5.** No CSRF token layer on this module's forms — same posture as every
  other mutating route in `app.py` (relies on `SESSION_COOKIE_SAMESITE=Lax`).

---

## Test coverage map

Rule id → covering test(s) in `tests/test_inventory.py`. **"— gap —"** means
the rule is real and currently believed true, but nothing in the suite would
catch a regression of it; that's a to-do, not a shrug.

| Rule | Test(s) |
| --- | --- |
| T1 | *(implicit — model shape)* |
| T2 | `test_add_type_rejects_a_blank_label` |
| T3 | `test_add_type_rejects_an_exact_duplicate`, `test_add_type_rejects_a_case_insensitive_duplicate`, `test_add_type_rejects_a_duplicate_of_a_hidden_type` |
| T4 | `test_add_type_allows_the_same_label_in_another_company` |
| T5 | `test_add_type_assigns_next_sort_order` |
| T6 | `test_toggle_type_flips_is_active`, `test_toggle_type_is_scoped_to_the_tenant` |
| T7 | `test_delete_type_removes_it_when_unused`, `test_delete_type_is_blocked_once_referenced` |
| T8 | `test_settings_inventory_page_lists_types` |
| T9 | `test_has_types_is_false_when_none_defined`, `test_has_types_is_true_once_a_type_exists`, `test_has_types_is_true_even_for_a_hidden_type`, `test_has_types_is_scoped_to_the_tenant` |
| I1 | *(implicit — model shape)* |
| I2 | `test_add_item_rejects_an_invalid_unit` |
| I3 | `test_add_item_rejects_a_blank_name`, `test_add_item_rejects_an_invalid_unit` |
| I4 | `test_add_item_with_a_foreign_type_id_falls_back_to_none` |
| I5 | `test_add_item_creates_it_with_given_fields` |
| I6 | — gap — (blank-name-on-edit and invalid-unit-on-edit are not separately asserted) |
| I7 | — gap — (foreign type id on **edit** specifically; only creation is covered by I4) |
| I8 | `test_edit_item_allows_setting_quantity_negative` |
| I9 | `test_edit_item_is_scoped_to_the_tenant`, `test_item_management_routes_are_scoped_to_the_tenant` |
| I10 | `test_toggle_item_flips_is_active`, `test_toggle_item_route_flips_is_active` |
| I11 | `test_delete_item_removes_it_when_unused`, `test_delete_item_is_blocked_once_referenced` |
| I12 | `test_inventory_list_sorts_by_name` (indirectly, via the route) |
| S1 | `test_selectable_items_includes_a_hidden_item_already_used_on_this_order` |
| S2 | `test_selectable_items_excludes_hidden_items_by_default` |
| S3 | — gap — (merge order itself isn't asserted, only membership) |
| M1 | `test_add_material_rejects_a_zero_or_negative_quantity`, `test_add_material_rejects_a_missing_quantity`, `test_add_material_rejects_an_unknown_item`, `test_add_material_rejects_another_tenants_item` |
| M2 | `test_add_material_decrements_stock_and_snapshots_the_item` |
| M3 | `test_add_material_snapshot_survives_a_later_price_change` |
| M4 | `test_add_material_decrements_stock_and_snapshots_the_item` |
| M5 | `test_add_material_allows_going_negative` |
| M6 | `test_edit_material_adjusts_stock_by_the_delta` |
| M7 | `test_edit_material_does_not_touch_the_snapshot_price` |
| M8 | `test_edit_material_rejects_a_zero_or_negative_quantity` |
| M9 | `test_edit_material_is_scoped_to_the_order` |
| M10 | `test_delete_material_restores_stock_and_removes_the_row` |
| M11 | `test_add_material_decrements_stock_and_snapshots_the_item` (asserts `.total`) |
| M12 | — gap — (ordering of `list_materials_for_order` isn't asserted) |
| O1 | *(by construction — no FK exists to assert against)* |
| O2 | `test_add_other_creates_a_cost_row`, `test_add_other_rejects_blank_description_or_missing_cost` |
| O3 | `test_edit_other_updates_fields` (happy path only) — gap: rejection on blank/`None` isn't tested |
| O4 | `test_edit_other_is_scoped_to_the_order` |
| O5 | `test_delete_other_removes_the_row` |
| C1 | `test_total_material_cost_sums_materials_and_others` |
| C2 | `test_total_material_cost_never_touches_order_total`, `test_materials_tab_never_changes_order_total` |
| A1 | `test_inventory_management_routes_require_login`, `test_order_material_routes_require_login`, `test_inventory_list_requires_login` |
| A2 | *(same as A1)* |
| A3 | `test_item_management_routes_are_scoped_to_the_tenant`, `test_toggle_type_is_scoped_to_the_tenant` |
| A4 | `test_add_material_404s_for_another_tenants_order`, `test_edit_material_404s_for_another_tenants_order`, `test_delete_material_404s_for_another_tenants_order`, `test_order_materials_page_404s_for_another_tenants_order` |
| A5 | `test_settings_inventory_page_lists_types` |
| U1 | `test_inventory_list_sorts_by_name`, `test_inventory_list_sorts_by_price` — gap: sorting by **Type** has no route test |
| U2 | — gap — (client-side `localStorage` filter; manually verified in browser only) |
| U3 | — gap — (modal-vs-inline is markup/JS; the underlying POST is covered by `test_add_item_route_creates_an_item`) |
| U4 | — gap — (client-side; manually verified in browser only) |
| U5 | — gap — (icon/tooltip markup isn't asserted; manually verified in browser only) |
| U6 | — gap — (CSS class presence isn't asserted; manually verified in browser only) |
| U7 | — gap — (tab order isn't asserted by a route test) |
| U8 | — gap — (client-side JS; manually verified in browser only) |
| U9 | — gap — (icon/tooltip markup isn't asserted; manually verified in browser only) |
| U10 | `test_filter_types_excludes_no_type_when_company_has_no_types_defined`, `test_filter_types_includes_no_type_when_a_type_exists_and_an_item_is_untyped` |
| U11 | `test_inventory_list_marks_hidden_items_with_data_active_false` (asserts the data attribute the client-side default-hide reads — the actual hiding is JS, manually verified in browser only) |
| U12 | `test_show_hidden_toggle_appears_when_a_hidden_item_exists` (presence only — the reveal-on-click behavior itself is JS, manually verified in browser only) |
| U13 | `test_show_hidden_toggle_absent_when_nothing_is_hidden` |
| V1 | `test_out_of_stock_count_counts_zero_and_negative_active_items`, `test_out_of_stock_count_excludes_hidden_items` |
| V2 | `test_out_of_stock_count_is_scoped_to_the_tenant` |
| V3 | `test_stock_alert_badge_appears_when_an_item_is_out_of_stock`, `test_stock_alert_badge_absent_when_nothing_is_out_of_stock` |
| V4 | `test_stock_alert_badge_clears_after_restocking` |
| V5 | `test_understocked_materials_for_order_reads_the_live_quantity` |
| V6 | `test_materials_tab_shows_warning_when_understocked`, `test_materials_tab_has_no_warning_when_fully_stocked` |
| V7 | — gap — (asserted by construction: `OrderMaterialOther` has no `inventory_item_id` to check against) |
| V8 | — gap — (cross-surface consistency isn't separately asserted, only each surface's own test) |

Everything marked "manually verified in browser only" was exercised by hand
during development (see the session that built this module) but has no
regression protection — a future change to that JS/markup could silently
break it and the suite would stay green. Closing the "— gap —" rows is the
obvious next step if this module gets touched again.
