# Core app — business requirements & rules

This is the living spec for the core app — `app.py` + `models.py` + their
templates — on the same footing as `inventory/REQUIREMENTS.md` and
`communications/REQUIREMENTS.md`: every rule as a checkable statement, not
prose, so requirements don't only live in code (or in someone's head) and so
test coverage can be checked against something. When behavior changes on
purpose, update the rule here in the same commit — if a rule and the code
disagree, one of them is a bug.

The `docs/` files (see the root `CLAUDE.md` index) explain the *why* behind
these choices at much greater length;
this file is the checklist of *what must hold true*. Rule ids are namespaced
by area (`CO-`, `CL-`, `OT-`, `OR-`, `PM-`, `DOC-`, `TL-`, `LST-`, `MOD-`,
`SET-`, `AN-`) so they can be cited from tests without colliding with the
`T`/`I`/`M`/`O`/`C`/`A`/`U`/`V` ids `inventory/REQUIREMENTS.md` already uses.

## 0. Scope

- **This file covers the core app only** — `Company`, `User`, `Client`,
  `SourceOption`, `OrderType`, `Order`, `OrderLine`, `Payment`, `Document`,
  and the views built on them (Timeline, Orders/Clients lists, Settings,
  Analytics, the client/order modals and detail pages).
- **Not covered here** — each of these self-contained modules already has
  its own spec, kept next to its own code and tests:
  - `billing/` — invoicing, Canadian sales tax, invoice numbering/freezing;
    see `billing/REQUIREMENTS.md`, backed by `tests/test_tax.py`,
    `tests/test_invoicing.py`, `tests/test_invoice_routes.py`,
    `tests/test_addresses.py` and `tests/test_billing_boundary.py`.
  - `communications/` — see `communications/REQUIREMENTS.md`.
  - `inventory/` — see `inventory/REQUIREMENTS.md`.
  - `documents/` — file upload/storage for an order's documents; see
    `documents/REQUIREMENTS.md`.
- **Where the core app touches one of those modules** (e.g. `Order.total`
  delegating into `billing`, or the calendar view reading
  `communications.calendar_service`), this file states only the core app's
  side of the seam — what it passes in, what it trusts back — not the
  module's own internal rules.

## 1. Tenancy & auth

- **CO1.** Every model that isn't itself `Company` reaches it via
  `company_id` — directly on `User`/`Client`/`SourceOption`/`OrderType`, and
  transitively through `Client`/`Order` for `Order`/`OrderLine`/`Payment`/
  `Document`.
- **CO2.** Every query in `app.py` filters by `current_user.company_id` — a
  lookup that doesn't apply this filter is a bug, not a variant. The shared
  helpers (`get_order_or_404`, `get_client_or_404`, `timeline_window`) exist
  specifically so call sites can't skip it.
- **CO3.** An id belonging to another company 404s (`get_order_or_404` /
  `get_client_or_404`) rather than leaking a "not found, try again" flow that
  might imply the row exists elsewhere.
- **CO4.** Every view route requires an authenticated session
  (`@login_required`); `/login` and `/logout` are the only exceptions.
  `/login` redirects to `next` on success; an unauthenticated request to any
  other route redirects to `/login?next=...`.
- **CO5.** `base.html`'s top nav (`.view-switch`) renders only when
  `current_user.is_authenticated` — a logged-out visitor sees no nav, not a
  disabled one.
- **CO5a.** Below 680px, the nav's links (`#view-switch-links`) are always
  present in the markup but collapse behind a hamburger toggle button
  (`#nav-toggle`) that reveals them as a dropdown on click; above 680px the
  toggle is hidden and the links render as the same horizontal row as
  always. Both elements exist regardless of viewport — only CSS decides
  which is visible.
- **CO6.** There is exactly one seeded company today (no signup flow, no
  tenant switcher) — `CO1`–`CO3` are enforced as schema-and-query-level
  guarantees anyway, so a second company can be added later without an audit
  of query logic.

## 2. Company

- **CO7.** `Company` holds `name` and `timezone` only — the invoicing
  letterhead (address, tax registrations, invoice prefix) lives on
  `billing.models.BillingProfile`, keyed by the same `company_id`, since a
  company is called the same thing whether or not it invoices.
- **CO8.** `Company.timezone` is display-only. Every stored timestamp stays
  naive UTC; changing this setting re-labels what's on file, it never moves
  stored data. An unresolvable/unlisted zone name is **rejected outright** —
  `update_preferences()` only accepts a value from the curated `TIME_ZONES`
  list, never falls through to storing something unresolvable.
- **CO9.** `run_migrations()` runs on every boot, is a no-op once a given
  change is applied, and runs safely against both a fresh and an
  already-populated database. `seed_if_empty()` only ever inserts sample data
  into a database with zero rows — it is not a reset mechanism.

## 3. Client & SourceOption

- **CL1.** `Client.name` is derived (`f"{first_name} {last_name}"`), never a
  stored column — every template reads it, nothing writes it directly.
- **CL2.** `Client.is_returning` = `len(self.orders) >= 2`, computed on every
  read. `Client.lifetime_value` = `sum(o.total for o in self.orders)`
  (`Order.total` is the tax-inclusive figure — see OR-rules below), also
  computed on every read. Neither is a stored column, so neither can drift
  out of sync with the orders behind it.
- **CL3.** `inquiry_type` and `first_message` are free-form fields meant to
  be populated by an inbound source (webhook or the communications module's
  `create_client_from_thread()`) — nothing in the core app's own new/edit
  client forms currently writes them.
- **CL4.** `SourceOption` ("how did you hear about us") is per-company,
  ordered by `sort_order`, and follows **hide, never hard-delete**:
  - **CL5.** `can_delete` is true only when `len(self.clients) == 0`.
    `/settings/clients`'s delete action is a no-op (or hidden) once any
    client is tagged with it.
  - **CL6.** Hiding sets `is_active = False`. A hidden option is removed from
    the checkbox list offered for a *new* selection on any client, but stays
    visible (marked "(hidden)") on a client already tagged with it.
  - **CL7.** Creating an option with a blank/whitespace label, or a label
    that case-insensitively duplicates an existing one **for that company**
    — including a hidden one — is rejected. The same label is allowed again
    under a different company.
  - **CL8.** Options are reorderable by drag-and-drop
    (`/settings/sources/reorder`); the request is a JSON `fetch`, and ids
    outside the current company are silently skipped rather than erroring
    the whole reorder.
- **CL9.** At most **one** `SourceOption` per company may carry
  `is_other = True` (a paired free-text box). Setting a new one on
  unsets whatever option held it before — enforced by there being exactly
  one `Client.other_source_detail` column, not one per option.
- **CL10.** `edit_client()` writes `other_source_detail` only when the
  `is_other` option is among the submitted `source_ids` on that save;
  unchecking it clears the detail rather than leaving orphaned text nobody
  can see.
- **CL11.** The client modal's edit form omits the billing address entirely;
  `edit_client()` only writes the address fields (street/city/province/
  postal) when `"street" in request.form` — a form that doesn't render a
  field must leave it untouched, not clear it. The same rule applies to any
  future field present on one edit surface but not the other.
- **CL12.** A `Client` with no `province` is charged **no tax** on any of
  their orders (delegated to `billing.tax`, but the trigger — "does this
  client have a province" — is a core-app fact read off `Client`).
- **CL15.** `Client.notes` is a free-text, staff-facing field (never shown
  to the client) with no relation to an order's own `notes`. It's editable
  only on the full client page's Information tab — the timeline's quick-edit
  client modal omits it, same as the address fields — and `edit_client()`
  only writes it when `"notes" in request.form`, following the same "absent
  means untouched" rule as CL11.
- **CL16.** A `Client` whose `notes` column is `None` (never edited) must
  render as an **empty** textarea, not the literal text "None" — the
  template renders `client.notes or ''`, not a bare `client.notes`.

## 4. OrderType

- **OT1.** `OrderType` is per-company, hide-don't-delete, single-select via
  a nullable `Order.order_type_id` FK — not a join table, since an order has
  at most one type (unlike `Client.sources`, a genuine many-to-many).
- **OT2.** `can_delete` is true only when `len(self.orders) == 0`.
- **OT3.** Creating a type with a blank label, or a label that
  case-insensitively duplicates an existing one for the company — including
  a hidden one — is rejected. The same label is allowed again under a
  different company.
- **OT4.** The order-type `<select>` appears on `new_order.html` /
  `order_page.html` **only when the company has at least one `OrderType`**
  (active or hidden) — a company that's never defined one gets no dropdown
  and no "Uncategorized" placeholder.
- **OT5.** `new_order()` offers only **active** types (a brand-new order
  can't already be tagged with a hidden one); `order_page()` offers active
  types **union** whatever type the order already has, mirroring `Client`'s
  source-checkbox pattern, so a hidden type an order already carries stays
  selectable there.
- **OT6.** `edit_order()` only touches `order_type_id` when
  `"order_type_id" in request.form` — the timeline's quick-edit order modal
  deliberately omits the field, so saving from that modal must not clear an
  order's type.
- **OT7.** The **Type** column on `/orders` renders only when the company
  has ever created an `OrderType` (existence, not `is_active` — a
  hidden-but-still-referenced type keeps the column showing for the orders
  that reference it), and only when that column is also marked `visible` in
  the company's saved column preferences (see LST-rules).

## 5. Order — core fields, status, dates

- **OR1.** `Order.status` is one of exactly `in_progress` / `ready` /
  `delivered` / `rush`. The string is used directly as a CSS class suffix in
  three places (`chip--{status}`, `dot--{status}`, `timeline__bar--{status}`)
  — adding a status requires matching CSS plus a `STATUS_LABELS` entry plus
  (currently, since it's hardcoded rather than looped) a `timeline.html`
  legend entry.
- **OR2.** `start` / `due` are real `Date` columns edited via native
  `<input type="date">` in both the order modal and the full order page.
- **OR3.** `pickup_date` is a separate, optional `Date` column — never
  derived from `due` or `status`. It is editable only on the full order
  page's Details tab (not in the timeline's quick-edit modal), and
  `edit_order()` guards on `"pickup_date" in request.form` before touching
  it, same convention as `order_type_id` (OT6).
- **OR4.** `Order.notes` and the (still-simulated) document list are
  editable/visible only on the full order page, never in the timeline modal.

## 6. Order lines, total, and payments

- **OR5.** An order's value is the sum of its `OrderLine`s
  (`quantity × unit_price`), not a stored column. `Order.total` is
  **tax-inclusive** (delegating to `billing` for the tax calculation) and is
  what every consumer reads: the timeline sort, `Client.lifetime_value`, the
  orders list, the invoice list.
- **OR6.** The new-order form takes a single **Price** field and turns it
  into the order's first `OrderLine` on creation; splitting an order into
  multiple lines happens afterward on the full order page's Billing tab.
- **OR7.** Editing line items is add/remove only — no in-place edit of an
  existing line's description/quantity/price (a known limitation, not an
  oversight; see "Explicit non-requirements").
- **OR8.** `Order.is_settled` compares `balance_due` against a small epsilon
  (`< 0.005`), not exact zero, so floating-point rounding never leaves a
  fully-paid order reading as outstanding.
- **PM1.** `Payment` is a one-to-many off `Order`: `amount` + `paid_date`
  only, with no fixed deposit/balance schema baked in — different studios
  run different payment plans. `Order.amount_paid` =
  `sum(p.amount for p in self.payments)`; `Order.balance_due` =
  `price - amount_paid` (computed on every read, same as CL2).
- **PM2.** Payments are recorded/removed only on the full order page's
  Billing tab (`add_payment()` / `delete_payment()`), never from the
  timeline's order modal — same "quick edit → modal, more room needed →
  page" split as the rest of order editing.
- **PM3.** `delete_payment()` is scoped by `order_id` — the correct
  `payment_id` under the wrong `order_id` must not succeed.
- **PM4.** `Payment.method` is one of `cash` / `etransfer` / `square` /
  `other` (`PAYMENT_METHOD_LABELS`), and `reference` is free-text tying a
  row back to a bank/Square statement. Neither is required to be filled for
  cash/other, but the field exists to be filled when it's known.
- **PM5.** Adding or deleting a payment redirects back to the Billing tab
  itself (`request.path` at the time the form was rendered), not to the
  page's own `return_to` (the "Back to timeline"/wherever-you-came-from
  link) — the two are different destinations and only look interchangeable
  because the Billing tab happens to render both. Submitting the outer
  `return_to` bounced the page away from Billing after every delete; Line
  items' add/delete forms already did this correctly and Payments' forms
  were brought in line with them.

## 7. Documents

- **DOC1.** Document upload/storage is owned by the self-contained
  `documents/` module (own tables, storage, thumbnails, migrations,
  blueprint) — the core app only renders the Details-tab partial it
  provides and links to it the same way `order_materials()` links to
  `inventory/`. See `documents/REQUIREMENTS.md` for the module's own rules
  (per-file/per-company size caps, opaque on-disk filenames, path
  containment checks, document types).

## 8. Timeline view

- **TL1.** `/` (and `/timeline/<year>/<month>/<day>`) is the default landing
  view. It shows `TIMELINE_WEEKS` (8) weeks at a time, one row per order
  overlapping the visible window.
- **TL2.** The visible window always starts on a Sunday
  (`_sunday_on_or_before`), matching the calendar view's Sunday-first
  convention.
- **TL3.** Prev/next moves by `TIMELINE_STEP_WEEKS` (4) — half the window —
  so consecutive pages overlap rather than jumping the full window at once.
- **TL4.** Each order's bar position is computed **server-side** as a
  1-indexed `grid-column: {start} / span {n}` (days) against a
  `--window-days` custom property. A bar whose real start/due falls outside
  the visible window is drawn with an angled clipped edge
  (`timeline__bar--open-start` / `--open-end`) rather than snapping to the
  window boundary silently.
- **TL5.** Every order bar's visible label is `"{item}"`, or
  `"{item} · {order type label}"` when the order has an `OrderType` — the
  type is shown on the bar itself, not next to the client's name, since the
  type belongs to the order, not the client. The bar's `title` (hover
  tooltip) attribute mirrors the same text.
- **TL6.** A returning client (`Client.is_returning`) shows a small star icon
  next to their name in the timeline's client column.
- **TL7.** Status filtering and sorting are **client-side**, persisted via
  `localStorage` (`timeline-hidden-statuses` / `timeline-sort`) — not URL
  query params — specifically so they survive prev/next navigation (each a
  real page load to a different URL) without every link needing to forward
  state.
- **TL8.** Clicking a legend status button toggles that status out of the
  visible set and updates the displayed order count
  (`#order-count`/`#order-count-label`) to reflect what's actually shown,
  not the window's unfiltered total.
- **TL9.** The sort `<select>` (start date / due date / highest paying
  client — the last sorting by `client.lifetime_value`, not order price)
  reorders the row elements in the DOM only; it never touches a bar's
  horizontal position, which stays governed by its server-computed
  `grid-column`.
- **TL10.** Every order and client dialog (quick-edit modal) currently on
  screen is pre-rendered into the page by `timeline_window()` —
  `clients_in_view` is deduplicated so a client with multiple visible orders
  gets exactly one client dialog.
- **TL11.** The order modal's **Total is read-only** — no line-item editor
  fits a dialog; editing lines requires the full order page.
- **TL12.** A "+ New order" button sits in the timeline's legend row,
  carrying the current window forward via `return_to` so saving lands back
  on the same window rather than resetting to today.

## 9. Orders list & Clients list

- **LST1.** Both `/orders` and `/clients` show **every** row for the company
  at once (unlike the timeline's windowed view).
- **LST2.** Sorting is **server-side** via `?sort=<key>&dir=asc|desc`,
  computed in Python (`ORDER_SORT_KEYS` / `CLIENT_SORT_KEYS`) rather than
  SQL, because several sort keys are computed properties
  (`order.total`, `client.lifetime_value`), not real columns. Clicking the
  same column header a second time reverses direction.
- **LST3.** `/orders` defaults to sorting by `due`; `/clients` defaults to
  sorting by `name`.
- **LST4.** `/orders` has its own status filter (click-a-legend-item),
  persisted under its own `localStorage` key
  (`orders-list-hidden-statuses`), independent of the timeline's. Hiding a
  status recomputes the **order count** and **balance due** stat cards from
  the still-visible rows, not the full unfiltered roster.
- **LST5.** `/clients` has no status-style filter — there's no column with
  that shape on that page.
- **LST6.** Orders-list columns are **reorderable and hideable**, per
  company, from `/settings/orders`:
  - **LST7.** `ORDER_COLUMNS` is the canonical, fixed set of possible
    columns (key → label + is_numeric). A company's chosen
    order/visibility is one JSON blob on `Company.order_columns`.
  - **LST8.** `_order_columns_for(company_id)` merges the saved blob against
    `ORDER_COLUMNS`: a key present in code but missing from a company's
    saved blob (a column added after they last saved) is appended at the
    end, **visible**; a key present in the saved blob but no longer in
    `ORDER_COLUMNS` is silently dropped.
  - **LST9.** `orders_list()` renders only `visible` columns, and the Type
    column is additionally gated by `has_order_types` (OT7) on top of its
    own visibility flag — hiding it in settings hides it regardless of
    whether types exist, and it stays hidden with no types regardless of
    the flag.
  - **LST10.** `/settings/order-columns/reorder` persists the new order
    the instant a dragged row is dropped (no separate Save button).
    `/settings/order-columns/<key>/toggle` flips visibility and redirects,
    matching the existing hide-toggle pattern elsewhere in Settings.
  - **LST11.** Toggling an unknown column key 404s. Reordering silently
    ignores ids that don't belong to a known column; reordering with an
    empty submitted order is a no-op that leaves the saved order untouched;
    a reorder that omits a currently-known key appends it at the end.
  - **LST12.** Column preferences are scoped per company — one company's
    saved order/visibility never affects another's.

## 10. Creating orders and clients

- **OR9.** "+ New order" / "+ Add order" is the same route/template
  (`new_order()`) regardless of which page (timeline, `/orders`) it's
  opened from, and always carries `return_to` so saving returns to the
  originating page.
- **OR10.** The `/orders` "+ Add order" button is visible even when the
  orders list is empty (it sits outside the `{% if orders %}` block) — the
  status legend does not, since there's nothing to filter yet.
- **OR11.** The order form's client field offers `+ Add new client` pinned
  right after the placeholder (before the alphabetical list). Choosing it
  reveals inline first/last name + email + phone fields and makes them
  `required`; server-side, `client_id == "new"` creates the `Client` first
  (flushed for its id) before creating the `Order`.
- **CL13.** `/clients` has its own standalone "+ Add client" button/route
  (`new_client()`) — first/last name, email, phone only. This exists
  specifically for a client with no order yet (an inquiry), so creating one
  doesn't require a throwaway order.

## 11. Modals + detail pages

- **MOD1.** From the timeline, clicking a client name or an order bar opens
  a pre-rendered `<dialog>` — no fetch, no JS framework — with a link out to
  the corresponding full page.
- **MOD2.** The client modal is itself a form posting straight to
  `/clients/<id>/edit` (first/last name, email, phone) — editable without
  leaving the timeline.
- **MOD3.** The full client page has two tabs (Information / Orders), same
  `.settings-nav` sub-nav shape as Settings; both stay at their existing
  URLs (`/clients/<id>`, `/clients/<id>/orders`) and both carry `return_to`
  through tab switches.
- **CL14.** Each order on the client page's Orders tab shows its status as
  the same labeled `.pill.pill--{status}` component the Orders page's
  Status column uses — not the bare `.dot--{status}` marker (that one has no
  label of its own and only reads correctly next to a legend, like the
  timeline's).
- **CL15.** The client page's Orders tab (`/clients/<id>/orders`) renders
  that client's orders as a sortable, filterable table, matching `/orders`'s
  style and interaction (not its per-company column customization):
  - Sorting is server-side via `?sort=&dir=`, against a **fixed** set of
    keys (`CLIENT_ORDER_SORT_KEYS`) — Item / Status / Start / Due / Total /
    Paid / Balance / Invoice — unlike `/orders`'s reorderable/hideable
    columns (LST6–LST12), since this is one client's own roster, not a
    company-wide list a studio would want to reshape.
  - The status filter is the same click-a-legend-item pattern as `/orders`,
    persisted under its own `localStorage` key
    (`client-orders-hidden-statuses`), independent of `/orders`'s.
  - **Invoice** is a column `/orders` doesn't have — the order's invoice
    number, linked, or an em dash when it has none.
- **MOD4.** The full order page has three tabs — **Details → Materials →
  Billing** — same sub-nav shape; the bare `/orders/<id>` URL stays on
  Details for backward-link compatibility.
- **MOD5.** Every cross-page link from a modal or list appends
  `?return_to={{ request.path }}`; both edit forms carry it as a hidden
  field; the corresponding routes redirect there after saving rather than
  resetting to a default view.
- **MOD6.** The back-link wording on a detail page is computed by
  `back_label(return_to)`, mapping the `return_to` path to a human label
  ("Back to timeline" / "Back to invoices" / etc.) rather than being
  hardcoded to one destination.

## 12. Settings

- **SET1.** `/settings` itself is content-free — it redirects to
  `/settings/general` so existing bookmarks/links to `/settings` keep
  working.
- **SET2.** Categories are real routes, not same-page tabs:
  **General → Orders → Inventory → Clients → Invoicing → Integrations**
  (the last two owned by `billing`/`communications` respectively). All
  render the same `settings.html`, switched on a `section` context var.
- **SET3.** `/settings/general` exposes only the company's time zone
  (CO8).
- **SET4.** `/settings/orders` manages `OrderType`s (§4 above).
- **SET5.** `/settings/clients` manages `SourceOption`s (§3 above),
  including the reorder and is-other toggle actions.

## 13. Analytics

- **AN1.** `/analytics` is company-wide and **read-only** — no route on this
  page ever writes data.
- **AN2.** "Avg. value per client" = average `Client.lifetime_value` across
  clients who have **at least one order** — not diluted by leads with zero
  orders, and not the same as average order price.
- **AN3.** "Top 5 paying clients" ranks by `lifetime_value` descending,
  capped at 5.
- **AN4.** Client source breakdown is percentage-of-all-clients per
  `SourceOption`, **including hidden options** (a hidden-but-historically-
  used source must still show up in stats — the entire reason hiding exists
  instead of deleting), with 0% entries filtered out and the remainder
  sorted highest-percentage-first.
- **AN5.** Revenue figures (total to date, YTD) are computed **only from
  recorded `Payment` rows** — never inferred from `Order.status` or
  `Order.total`. An order sitting at "in progress" with a deposit paid
  contributes the deposit; a "delivered" order with no payment recorded
  contributes nothing.
- **AN6.** Revenue YTD filters `Payment.paid_date.year == date.today().year`.
- **AN7.** Revenue-by-payment-method is sorted by amount, dominant method
  first.
- **AN8.** "Outstanding on issued invoices" counts **invoiced work only**
  (delegating to `billing` for what counts as issued/outstanding) — an
  order that hasn't been billed yet isn't money anyone is owed.

## 14. Explicit non-requirements

Deliberate omissions, not oversights — listed so nobody "fixes" them without
checking first. See `docs/roadmap.md` for the full reasoning behind
each.

- **N1.** No in-place edit of an existing `OrderLine` — remove and re-add
  only (OR7).
- **N2.** No password-reset / change-password UI; no way to add a user
  short of a Python shell.
- **N3.** No signup flow or tenant switcher — `Company` is schema-ready for
  multiple tenants (CO1–CO3) but only one is ever seeded.
- **N4.** No tax-collected report on `/analytics` — `InvoiceTaxLine` exists
  specifically to make this queryable later, but nothing sums it yet.
- **N5.** Nothing blocks editing an issued order's line items after
  invoicing — the invoice total itself is safely frozen (`billing`'s
  concern), but the order page can then show lines that don't add up to
  what was billed. The page notes this; blocking it outright is a known
  follow-up.
- **N6.** `start` dates and `price` values in seed/sample data are
  estimates, not real client-provided numbers.

---

## Test coverage map

Rule id → covering test(s), mostly in `tests/test_core_app.py` (added
specifically to close this table's gaps) unless another file is named.
**"— gap —"** means the rule is real and currently believed true but still
has no regression test.

| Rule | Test(s) |
| --- | --- |
| CO1–CO2 | — gap — (structural; exercised indirectly by every CO3-tagged test below, not asserted on its own) |
| CO3 | `test_get_order_or_404_is_scoped_to_the_tenant`, `test_get_client_or_404_is_scoped_to_the_tenant` |
| CO4 | `test_core_get_routes_require_login`, `test_core_get_routes_require_login_for_a_specific_order_and_client`, `test_core_post_routes_require_login` |
| CO5 | `test_nav_hides_for_a_logged_out_visitor` |
| CO5a | `test_nav_includes_the_mobile_hamburger_toggle` (markup presence/order only — the CSS collapse and the open/close click behavior itself are client-side and unassertable by a route test, same limitation as TL7–TL9) |
| CO6 | — gap — (single-tenant-seed is a deployment fact, not asserted by a test) |
| CO7 | — gap — (asserted implicitly by every billing test reading `BillingProfile` instead of `Company`, but not stated as a `Company`-shape rule directly) |
| CO8 | — gap — |
| CO9 | `tests/test_migrations.py` (covers the modules' own migrations; core-app `run_migrations()` itself has no dedicated test) |
| CL1 | `test_client_name_is_first_plus_last` |
| CL2 | `test_client_is_returning_only_with_two_or_more_orders`, `test_client_lifetime_value_sums_order_totals` |
| CL3 | — gap — |
| CL4–CL8 | `test_add_source_option_rejects_a_case_insensitive_duplicate`, `test_add_source_option_rejects_a_duplicate_of_a_hidden_option`, `test_reorder_source_options_sets_sort_order_from_position`, `test_reorder_source_options_ignores_ids_from_another_tenant`, `test_reorder_source_options_reflects_on_the_settings_page`, `test_reorder_source_options_requires_login` (`tests/test_settings_options.py`) |
| CL9–CL10 | `test_set_other_marks_the_option`, `test_set_other_toggles_off_on_a_second_click`, `test_only_one_option_can_be_other_at_a_time`, `test_set_other_is_tenant_scoped`, `test_saving_the_other_detail`, `test_the_detail_is_cleared_when_other_is_unchecked` (`tests/test_other_source.py`) |
| CL11 | `test_edit_client_without_address_field_leaves_address_untouched`, `test_edit_client_with_address_field_updates_it`, `test_edit_client_with_an_invalid_province_clears_it` |
| CL12 | covered indirectly by `tests/test_tax.py`'s client-province gating tests (billing side); `test_order_total_with_no_tax_is_the_sum_of_its_lines` covers the core-app trigger (no province ⇒ no tax) from `Order`'s side |
| CL15 | `test_edit_client_without_notes_field_leaves_notes_untouched`, `test_edit_client_with_notes_field_updates_it` |
| CL16 | `test_client_page_renders_blank_notes_not_the_word_none` |
| OT1–OT3 | `test_add_order_type_rejects_a_case_insensitive_duplicate`, `test_add_order_type_rejects_a_duplicate_of_a_hidden_type`, `test_add_order_type_allows_the_same_label_in_another_company` (`tests/test_settings_options.py`) |
| OT4 | `test_new_order_form_omits_type_dropdown_without_any_order_type`, `test_new_order_form_shows_type_dropdown_once_a_type_exists` |
| OT5 | `test_new_order_only_offers_active_types`, `test_order_page_offers_a_hidden_type_the_order_already_has` |
| OT6 | `test_edit_order_without_type_field_leaves_type_untouched`, `test_edit_order_with_blank_type_field_clears_it` |
| OT7 | `test_orders_list_hides_type_column_with_no_order_types`, `test_type_column_shows_once_a_type_exists_and_is_used`, `test_hiding_type_column_still_hides_it_even_when_order_types_exist` (`tests/test_order_columns.py`), plus `test_orders_list_type_column_only_shows_with_at_least_one_order_type` |
| OR1 | `test_edit_order_rejects_an_unknown_status` |
| OR2 | — gap — (native `<input type="date">` rendering isn't asserted; the underlying `edit_order`/`new_order` date handling is covered by other OR tests) |
| OR3 | `test_edit_order_pickup_date_untouched_when_field_absent`, `test_edit_order_pickup_date_cleared_when_field_blank`, `test_edit_order_pickup_date_can_be_set` |
| OR4 | — gap — |
| OR5 | `test_order_total_with_no_tax_is_the_sum_of_its_lines` (tax-inclusive delegation itself is `tests/test_invoicing.py`'s territory) |
| OR6 | `test_new_order_creates_a_single_line_from_price` |
| OR7 | *(non-requirement — see N1)* |
| OR8 | `test_is_settled_tolerates_a_cent_of_rounding` |
| PM1 | `test_add_payment_creates_a_row_and_updates_balance` |
| PM2 | — gap — (the modal-omits-payments UI split isn't asserted, only the route behavior) |
| PM3 | `test_delete_payment_is_scoped_to_the_order` |
| PM4 | `test_add_payment_defaults_an_invalid_method_to_cash`, `test_add_payment_rejects_missing_or_invalid_fields`, `test_delete_payment_removes_it_and_recomputes_balance` |
| PM5 | `test_billing_tab_forms_carry_their_own_page_as_return_to` |
| DOC1 | see `documents/REQUIREMENTS.md` (`tests/test_documents.py`) — this file only asserts the seam, not the module's internals |
| TL1 | `test_timeline_next_and_prev_step_by_half_the_window` (implicitly, via the 8-week window math) |
| TL2 | `test_sunday_on_or_before_snaps_back_to_the_most_recent_sunday`, `test_timeline_window_always_starts_on_a_sunday` |
| TL3 | `test_timeline_next_and_prev_step_by_half_the_window` |
| TL4 | `test_timeline_clips_a_bar_that_starts_before_the_window` (the open-start/open-end class only; `col_start`/`span` grid math itself isn't independently asserted) |
| TL5 | `test_timeline_order_bar_label_and_tooltip_include_the_order_type`, `test_timeline_order_bar_tooltip_omits_type_when_order_has_none` |
| TL6 | `test_timeline_returning_client_star_shown_only_once_returning` |
| TL7–TL9 | — gap — (client-side `localStorage` filter/sort; manually verified in browser only) |
| TL10 | `test_timeline_dedupes_client_dialogs_across_multiple_orders` |
| TL11 | — gap — (asserted visually only — the modal's Total field being read-only is markup, not behavior a route test can check) |
| TL12 | — gap — (button presence/`return_to` isn't separately asserted from the timeline page; see OR9–OR11 for the route itself) |
| LST1 | — gap — (both lists showing every row, as opposed to a windowed subset, is implicit in every other LST test) |
| LST2–LST3 | `test_orders_list_default_sort_is_due_ascending`, `test_orders_list_sort_by_total_desc`, `test_clients_list_default_sort_is_by_name`, `test_clients_list_sort_by_lifetime_value` |
| LST4–LST5 | — gap — (client-side status filter; manually verified in browser only) |
| LST6–LST12 | `tests/test_order_columns.py` (all tests in that file) |
| OR9 | `test_new_order_carries_return_to_through_to_the_redirect` |
| OR10 | `test_new_order_button_present_when_orders_list_is_empty` |
| OR11 | `test_new_order_inline_client_creation_creates_both_rows`, `test_new_order_inline_client_creation_requires_first_and_last_name` |
| CL13 | `test_new_client_route_creates_a_client_with_minimal_fields`, `test_new_client_route_requires_first_and_last_name` |
| MOD1–MOD4 | — gap — (dialog/tab markup and layout; not asserted by a route test) |
| CL14 | `test_orders_tab_shows_the_labeled_status_pill`, `test_orders_tab_status_dot_only_appears_in_the_legend` (`tests/test_client_orders_tab.py`) |
| CL15 | `test_orders_tab_is_a_table_with_the_expected_columns`, `test_orders_tab_shows_dash_for_an_uninvoiced_order`, `test_orders_tab_sorts_by_the_requested_column` (`tests/test_client_orders_tab.py`); the status filter itself is — gap — same as LST4's client-side limitation |
| MOD5 | `test_edit_client_redirects_to_return_to`, `test_edit_order_redirects_to_return_to` |
| MOD6 | `test_back_label_variants` |
| SET1 | `test_settings_root_redirects_to_general` |
| SET2–SET3 | — gap — |
| SET4 | see OT1–OT3 |
| SET5 | see CL4–CL8 |
| AN1 | `test_analytics_avg_value_excludes_clients_with_no_orders` |
| AN2 | `test_analytics_avg_value_excludes_clients_with_no_orders` |
| AN3 | `test_analytics_top_clients_ranks_by_lifetime_value` |
| AN4 | `test_analytics_source_breakdown_includes_hidden_options_and_excludes_zero_percent` |
| AN5–AN6 | `test_analytics_revenue_counts_recorded_payments_not_order_total`, `test_analytics_revenue_ytd_filters_to_the_current_year` |
| AN7 | `test_analytics_method_breakdown_sorted_by_amount_descending` |
| AN8 | — gap — (outstanding-on-issued-invoices is exercised from the billing side by `tests/test_invoicing.py`, not asserted from `/analytics` itself) |

Everything still marked "— gap —" is either client-side JS/markup behavior
(no route test can see it — same limitation `inventory/REQUIREMENTS.md`
notes for its own UI rules) or a rule this pass didn't reach; closing those
is the obvious next step if this file gets revisited.
