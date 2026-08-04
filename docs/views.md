# Views, modals & detail pages

> Part of the `leather_orders` app. See the root [CLAUDE.md](../CLAUDE.md) for
> stack, conventions and design language, and [REQUIREMENTS.md](../REQUIREMENTS.md)
> for the core app's rules as numbered, checkable statements.

## Views

**Timeline** (`/`, `/timeline/<year>/<month>/<day>`) — the app's default view. Shows
`TIMELINE_WEEKS` (currently 8) weeks at once, one row per order that overlaps the
visible window, client name pinned left (with a small star icon if
`client.is_returning`), a colored bar (reusing the same
`--status-*` colors as calendar chips) spanning `start` → `due`, clipped to the
window with an angled edge (`.timeline__bar--open-start` / `--open-end`) when the
real start/due falls outside what's visible.

- Window always snaps to a Sunday (`_sunday_on_or_before`), matching the calendar's
  Sunday-first convention.
- Prev/next moves by `TIMELINE_STEP_WEEKS` (currently 4 — half the window), so
  consecutive pages overlap rather than jumping a full 8 weeks at once. Both
  constants live at the top of the timeline section in `app.py`; change them there,
  not in the template.
- Bar positioning is done with CSS Grid: the URL param `<year>/<month>/<day>` is the
  window's start date, and each order's on-screen position is computed server-side
  as a 1-indexed `grid-column: {start} / span {n}` (in days) against a
  `--window-days` custom property set once per page. Header week labels use a
  parallel `--week-count`-column grid that lines up with it since a week is always
  1/8 of the row width regardless of total day count.
- **Status filter + sort are client-side, persisted via `localStorage`** (keys
  `timeline-hidden-statuses` and `timeline-sort`), not URL query params — deliberate,
  so they carry over automatically across prev/next navigation (each of which is a
  real page load to a different URL) without every link in the template needing to
  forward filter/sort state. Each `.timeline__row` carries `data-status` /
  `data-start` / `data-due` / `data-client-value` attributes; the inline `<script>`
  at the bottom of `timeline.html` reads/writes those two `localStorage` keys and
  applies them on load. Clicking a legend item (`#status-legend .legend__item`,
  now a `<button>`, not a plain `<span>`) toggles that status in the hidden list and
  re-runs `applyFilter()`, which also updates the visible order count
  (`#order-count`/`#order-count-label`) — so the header count reflects what's
  actually shown, not the window's full total. The sort `<select>`
  (`#timeline-sort`: start date / due date / highest paying client — the last one
  sorts by `client.lifetime_value`, not order price) re-runs `applySort()`, which
  just reorders the row `<div>`s in the DOM via `appendChild` — it doesn't touch
  each bar's horizontal position, since that's controlled independently by the
  server-computed `grid-column` inline style, not DOM order.

**Calendar** (`/calendar`, `/month/<year>/<month>`) — month grid via Python's
stdlib `calendar` module (`calendar.Calendar`), no external dependency. Shows only
**synced Google Calendar events** (`.chip--event` — see the communications module's
UI section) — orders never render here, and never did without a mailbox connected
either (`orders_by_day()` was removed from `app.py`; `month_view()` only reads
`calendar_service.events_by_day()`). Orders live on the Timeline instead. Not the
default view (see Timeline above) — the function is named `calendar_view` in
`app.py`, distinct from the `/` route.

**Events are created and edited from here**, via the same pre-rendered
`<dialog class="modal">` pattern as the timeline: a "+ New event" button in
`.legend-row`, and every `.chip--event` is a `<button>` opening that event's own
edit dialog (`events_in_view` deduplicates, so a multi-day event gets one dialog,
not one per cell). A **"Sync now"** button sits beside it — calendars only, see
"Background jobs"; the two share a `.legend__actions` group that carries the
`margin-left: auto` so they stay together at the right-hand end rather than the
first being shoved right and the second trailing after it. Points worth knowing:

- **The forms post into the communications blueprint**
  (`create_calendar_event` / `update_calendar_event` in the module's
  `routes.py`), not into `app.py`. Writing an event goes out to a provider, so
  it belongs behind the service and inside that blueprint's automatic CSRF —
  the templates therefore carry `{{ csrf_token() }}`, unlike the app's own
  forms. `month_view()` renders the result through
  `communications_routes.take_notice()`, the module's one-shot notice.
- **Times in the form are the company's local wall clock**, converted by
  `_to_utc()` (the inverse of the `local_datetime` filter). Everything below
  that layer is naive UTC. Getting this wrong doesn't *look* wrong — it books
  the appointment a few hours out — so it's covered by a test asserting the
  stored UTC value, not just that a row appeared.
- **No event UI at all when `has_calendar` is false.** A studio with no
  connected calendar gets no button, rather than one whose only outcome is an
  error.
- **The linked client is invited automatically; the text field is for
  *additional* guests** (labelled "Also invite"). The studio picks a name, not
  an address — `calendar_service.guests_for()` reads `Client.email` off the
  record and puts them first, deduplicating case-insensitively against
  anything also typed (Google rejects a duplicate address, and the client's
  own is the obvious thing to type into the extras box). A client from another
  tenant contributes nobody: the id arrives in a form field anyone can edit,
  so it must not become a way to have us mail another studio's roster.
- **Attaching a guest and *emailing* them are two different acts.** This is
  the one to keep straight. `insert` defaults to `sendUpdates=none`, so the
  old "Invite" field attached an address and **sent no mail at all** — the
  label promised something that never happened. Now `notify` is read from
  **which submit button was pressed** (`_wants_invite()` on the `send_invite`
  field), never inferred from whether guests exist, and reaches Google via
  `_send_updates()` as `all`/`none`. Inferring would mean linking a client to
  a private reminder quietly mails them. Two buttons, and **"Add event" comes
  first in the DOM on purpose** — Enter submits the first submit button in a
  form, so the keyboard falls on the one that sends nothing. The invite button
  is hidden by the dialog's inline script when there's nobody to invite, and a
  small `.modal__hint` under the client select names the address the
  invitation would reach — or says the client has no email on file, since
  otherwise "send invite" is a button whose only outcome is silence.
- **The confirmation says whether mail went out** (`_event_notice()`):
  "No invitations sent." / "Invitation sent to …". A confirmation that doesn't
  mention the mail is one you have to open Gmail to verify.
- **Guests *are* editable now**, because `CalendarEvent.attendees` mirrors
  them (comma-separated `Text`, same shape as `EmailMessage.recipients`; the
  sync already read attendees to match a client and threw them away). Google's
  `patch` **replaces** the attendee array rather than merging, so the rule is:
  a form that changes guests posts the **complete** list, and one that doesn't
  omits the key entirely — `update_event()` only forwards `attendees` when the
  caller passed it, which is what keeps an ordinary title edit from touching
  who is invited. Two supporting properties: `guest_list` hides the studio's
  own mailbox (Google lists the organiser among the attendees), and
  `extra_guests` also hides the linked client's address, since they're invited
  by *being* the linked client and listing it twice would keep them invited
  after an unlink. The organiser is re-appended server-side after an edit —
  but **only if it was already there**, since adding it unconditionally would
  invite the studio to its own reminder.
- **There's no delete.** Nothing asked for one, and the same trash-vs-delete
  question as email would need answering first.
- The client link (`CalendarEvent.client_id`) is applied locally and never
  forwarded to the provider — `update_event()` pops it out of `**fields`, using
  a sentinel so "don't touch" stays distinguishable from "clear it".

**Orders list** (`/orders`) / **Clients list** (`/clients`) — full rosters, one row
per order / client, in a `.invoice-table` (same class as the invoice list's table —
generic enough to reuse rather than naming a near-duplicate class after invoices
specifically). Unlike the timeline (windowed to a few weeks), these show everything
at once. **Sorting is server-side**: clicking a column header (`sort_link()` macro,
defined locally in each template) is a real link to `?sort=<key>&dir=asc|desc`,
toggling direction on repeat clicks of the same column (chevron-up/down SVG, matching
the "inline SVG over text glyphs" convention rather than `↑`/`↓` characters).
`orders_list()` / `clients_list()` in `app.py` fetch everything for the company, then
`list.sort()` in Python against `ORDER_SORT_KEYS` / `CLIENT_SORT_KEYS` — sorting
happens in Python rather than SQL because several sort keys are computed properties
(`order.total`, `client.lifetime_value`), not real columns. Fine at this table's
scale; revisit if the row count ever gets large. Orders sort by `due` by default
(item / client / status / start / due / total / paid / balance available); clients
sort by `name` (orders / lifetime value also available — "returning" was considered
but dropped since there's no dedicated column for it to sort, only an inline star
next to the name). Both link out to the client/order detail pages with the usual
`return_to`.

The **Orders list also has a status filter**, same click-a-legend-item-to-hide
pattern as the timeline (see below) but with its own `localStorage` key
(`orders-list-hidden-statuses`, independent of the timeline's
`timeline-hidden-statuses` — the two pages don't share filter state), scoped inline
in `orders_list.html` rather than the shared timeline script. Hiding a status also
recomputes the **order count** and **balance due** stat cards at the top of the page
(from each row's `data-balance`), so those numbers always describe what's actually
showing, not the full unfiltered roster. The Clients list has no equivalent filter —
there's no status-like column to filter by there.

**Orders list columns are reorderable and hideable**, per company, from
Settings > Orders > "Orders list columns" (`settings.html`, `section ==
'orders'`). `ORDER_COLUMNS` in `app.py` is the canonical dict of the 9
possible columns (key → `(label, is_numeric)`) — the same 9 keys as
`ORDER_SORT_KEYS`. A company's chosen order/visibility is one JSON blob on
`Company.order_columns` (`[{"key", "visible"}, ...]` in display order), not
a table like `SourceOption`/`OrderType`: this is a fixed, known set of
columns, not an open-ended list a user names, so there's no per-row
hide-not-delete state to track. `_order_columns_for(company_id)` merges the
saved blob against `ORDER_COLUMNS` — a key missing from a company's saved
blob (a column added to the app after they last saved) is appended at the
end, visible; a key in the blob but no longer in `ORDER_COLUMNS` (one
removed from the app) is silently dropped rather than erroring. `orders_list()`
filters to `visible` columns before rendering, with the same `has_order_types`
gate as before applied on top of the Type column's own visibility flag — hiding
Type in settings hides it even when order types exist, and it stays hidden
with no types regardless of the flag. `orders_list.html` loops `columns`
for both the header row (still going through the existing `sort_link()`
macro, so sorting keeps working column-by-column) and each body row (an
`{% if col.key == ... %}` chain per column, since each one renders
differently — a link, a pill, a formatted dollar figure — not a generic
`getattr`).

Two routes back the editor, deliberately reusing two different existing
interaction shapes rather than a third: **`/settings/order-columns/reorder`**
is a JSON `fetch` fired on `dragend`, identical in structure to
`documents.reorder_types` (native HTML5 drag-and-drop, `draggable="true"`
list items, a grip handle, same `.settings-source-list__item--dragging` /
`.settings-source-list__grip` CSS) — reordering saves the instant a row is
dropped, no separate Save button. **`/settings/order-columns/<key>/toggle`**
is a plain POST + redirect, matching the `toggle_order_type` /
`toggle_source_option` "Hide" button pattern, rather than a checkbox-plus-Save
form — everything else in Settings acts immediately on click, and a
checkbox would be the only field on the page that didn't.

**Inventory** (`/inventory`) — the master list of stocked materials, owned by
the self-contained `inventory/` module (see [inventory/CLAUDE.md](../inventory/CLAUDE.md)) rather
than living in `app.py` like the two lists above it — same "module owns a
first-class page" precedent as `billing.invoice_list()` for `/invoices`.
Sortable by Type, Name, or Unit price; filterable by type (a "No Type" bucket
too, once the company has any type defined); hidden items are off by default
behind a "Show hidden" toggle. Each row's Actions column carries Hide/Unhide +
Delete icon buttons. "+ Add item" opens a modal rather than an inline form.

**Invoices** (`/invoices`, `/invoices/<id>`) — `invoices()` lists every invoice for
the company in a `.invoice-table` (number, client, order, issued, total, paid,
balance, status pill) with an **Outstanding** figure at the top, followed by a
"Not invoiced yet" list of orders with no `Invoice` — that second list is the actual
to-do the page exists for. Client names and order names are links on both lists,
carrying `return_to` like everything else. `invoice_page()` renders one invoice: everything inside
`.invoice-doc` is the printable document, and the controls below it are marked
`.no-print`, so the "Print / save as PDF" button (`window.print()`) produces the
document alone — see the `@media print` block at the bottom of `style.css`. The
client's `address` is rendered with `white-space: pre-line` rather than converting
newlines to `<br>`, which keeps the line breaks without needing `|safe` on
user-entered text. Creating an invoice is a POST to `/orders/<id>/invoice`
(`create_invoice()`); a double submit on an already-invoiced order is a no-op rather
than burning a second number. `set_invoice_status()` backs the status/due date/notes
form.

**Settings** (`/settings`) — as this grew past a couple of sections it was split
into categories with their own sub-nav (`.settings-nav`, styled as underlined tabs,
distinct from the top-level `.view-switch`), each a real route rather than a
same-page anchor/JS tab-switch. The nav, and the order the categories were split
in, is **General → Orders → Inventory → Clients → Invoicing → Integrations**:

- `/settings` (`settings()`) is content-free — it just redirects to
  `settings_general`, so the nav's "Settings" link and any bookmark still work.
- **`/settings/general`** (`settings_general()`) — the company's **time zone**
  only (`update_preferences()`). The smallest, most foundational category, so it
  leads the nav.

  **Time zone** is `Company.timezone`, an IANA name defaulting to
  `models.DEFAULT_TIMEZONE` (`America/Vancouver` — the studio's own zone; a
  time only means something read in the zone you're in). It is **display
  only**: every stored timestamp stays naive UTC, so changing this re-labels
  what's on file rather than moving it. The dropdown comes from `TIME_ZONES` in
  `app.py` — a curated list, west-to-east Canada then the few other places the
  studio deals with, deliberately *not* `zoneinfo.available_timezones()` (600
  unordered entries, every one a way to mislabel your own mail). Anything not
  in that list is **ignored rather than stored**, since an unresolvable zone
  name would silently push every time on every page back to UTC. Rendering
  goes through the `local_datetime` Jinja filter — see the communications
  module's UI section, which owns it. `tzdata` is in `requirements.txt`
  because a stock Windows Python has no IANA database at all and raises
  `ZoneInfoNotFoundError` for every one of these names.
- **`/settings/orders`** (`settings_orders()`) — management of `OrderType`s
  (`add_order_type()` / `toggle_order_type()` / `delete_order_type()`,
  hide-don't-delete shape — see [docs/data-model.md](data-model.md)).
- **`/settings/inventory`** (`settings_inventory()`, `app.py`) — management of
  `InventoryType`s, same hide-don't-delete shape, but the add/toggle/delete
  routes it posts to live in the module's own blueprint
  (`inventory.add_type()` etc.) rather than in `app.py` — see the Inventory
  module's own [CLAUDE.md](../inventory/CLAUDE.md) for why. Placed next to Orders rather than Clients,
  since materials belong with what an order consumes.
- **`/settings/clients`** (`settings_clients()`) — management of `SourceOption`s
  (`add_source_option()` / `toggle_source_option()` / `delete_source_option()`),
  the "how did you hear about us" checkboxes — same hide-don't-delete shape.
  Orders and Clients used to be one "Order preferences" category; split once each
  side had its own settings-source-list section, so a page named for one entity
  isn't the place someone reaches for to edit the other.
- **`/settings/invoicing`** (`settings_invoicing()`) — **Company details**
  (`update_company_details()` — name, structured address with a province dropdown,
  GST/HST, PST/RST, QST, NEQ; the invoice letterhead. City/province/postal share a
  line via `.field-row`, which wraps rather than squeezing) and **Invoicing**
  (`update_invoicing_settings()` — number prefix, which also shows what the next
  number will be, plus payment instructions). Grouped together because both feed
  the invoice letterhead — company info without invoicing context (or vice versa)
  didn't make sense as separate categories. Last in the nav since it's the
  category touched least often, once initially filled in.

All categories render the same `settings.html`, switched on a `section` context
var ("general" / "orders" / "clients" / "invoicing") — same pattern as
`active_view`, just page-local rather than top-nav-level; the `{% if/elif %}`
chain in the template is ordered to match the nav, not alphabetically or by
when a section was added. Note `update_preferences()` is a `POST` at the same
path (`/settings/general`) as the `GET` `settings_general()` page, and
`update_invoicing_settings()` likewise at `/settings/invoicing` as the `GET`
`settings_invoicing()` page — that's intentional (each form posts back to its
own page's URL), not a collision; Flask dispatches on method, and each pair is
two separate view functions/endpoints. If you add another category, follow the
same shape: a new `GET` route + section in `settings.html`, a new link in
`.settings-nav` in the position it should sort, and any POST actions
redirecting back to that new route.

**Analytics** (`/analytics`) — company-wide read-only stats, `analytics()` in
`app.py`, two sections of bordered `.stat-card`s (`.analytics-grid`) laid out with
`analytics.html`:
- *Clients*: avg. value per client, top 5 paying clients, client sources breakdown
  (percentage of all clients tagged with each `SourceOption` — **includes hidden
  options**, since a hidden-but-historically-used source should still show up here;
  that's the whole reason hiding exists instead of deleting — 0% entries are filtered
  out, and the rest sorted **highest percentage first**, so the dominant source reads
  first the same way `method_breakdown` under Revenue does).
- *Revenue* (heading is a placeholder, easy to rename — just an `<h2>` in the
  template): total revenue to date and revenue YTD (`Payment.paid_date.year ==
  date.today().year`), both computed from recorded `Payment` rows only — **not**
  inferred from order `status` or `price`, so an order sitting at "in progress"
  with a deposit paid still contributes its deposit to revenue, and a "delivered"
  order with no payment recorded (data entry hasn't caught up) contributes nothing.
  "Avg. value per client" = average `Client.lifetime_value` across clients who have
  at least one order (not average order price, and not diluted by leads with zero
  orders). Also here: **revenue by payment method** (sorted by amount, so the
  dominant method reads first) and **outstanding on issued invoices** — the latter
  counts invoiced work only, since an order that hasn't been billed isn't money
  anyone owes yet.

**View switch**: a small nav in `base.html` (`.view-switch`) links between the top-level
views, driven by an `active_view` context var ("timeline" / "calendar" / "orders" /
"inventory" / "clients" / "invoices" / "analytics" / "settings") that every route passes to
`render_template`. If you add another view, follow the same pattern.
Client/order detail pages pass `active_view=None` since they're not really either
tab — the switch just won't highlight anything while on those pages.

**Mobile nav collapse**: below 680px (the same breakpoint the calendar/timeline
responsive rules further down `style.css` already use), `.view-switch`'s links
collapse behind a hamburger toggle button (`#nav-toggle`, right-aligned via
`margin-left: auto` rather than `justify-content`, so it stays pinned to the edge
regardless of how many nav badges are inflating the links' width) that reveals them
as an absolutely-positioned dropdown panel (`#view-switch-links`). Both elements are
always present in the markup — only CSS decides which is visible at a given width —
so `base.html` has no server-side "is this a mobile request" branch. A small inline
`<script>` right after `</nav>` toggles an `is-open` class plus `aria-expanded` on
click, and closes the menu again when any link inside it is clicked, since every nav
link is a real page load rather than an SPA route (opening it back up on the next
page is what the `aria-expanded="false"` default and the fresh page load both already
do without extra code). Above the breakpoint the toggle is `display: none` and
`.view-switch__links` renders exactly as `.view-switch` itself always did (a plain
horizontal flex row) — no desktop behavior change.

Both the timeline's and `/orders`' status-filter buttons are preceded by a plain
`.legend__label` reading "Filter by" — added so the clickable status pills read as
a filter control rather than as a legend/key (which is also why "Returning client",
not itself a filter, was moved out from beside them; see above). On the timeline
specifically, `.timeline__sort`'s "Sort by" control carries its own extra
`margin-left` on top of `.legend-row`'s normal gap, so it doesn't read as part of
the status-filter group next to it.

**Creating an order:** a "+ New order"/"+ Add order" button — same `new_order()`
route and template regardless of which page it's clicked from — sits in
`.legend-row` on three pages: the timeline (same line as the status legend, pushed
right via `.legend__new-order`'s `margin-left: auto`; tried in the header first and
moved down), `/orders` (same row as *its* status legend), and always-visible even
when `/orders` has no rows yet (the button sits outside the `{% if orders %}` block,
the legend inside it — no point filtering statuses that don't exist yet). It links
to `/orders/new` (GET shows the form / POST creates), carrying `return_to` so saving
lands back on wherever it was opened from — the timeline window, or the orders list.
The client field is a `<select>` with a `+ Add new client` option pinned right after
the placeholder (before the alphabetical client list); choosing it reveals a
`<fieldset>` (first/last name, email, phone) via a small inline `<script>` that also
toggles `required` on the new-name fields. Server-side, `client_id == "new"` in the
POST branches to creating a `Client` first (flushed to get its id) before creating
the `Order` — see `new_order()` in `app.py`.

**Creating a client:** `/clients` has its own "+ Add client" button (same
`.legend-row` / `.legend__new-order` treatment, just without a status legend next to
it — there's no per-client filter). Unlike the inline client creation above, this is
a standalone route, `new_client()` in `app.py` → `new_client.html`: just first/last
name, email, phone (address and sources are added afterwards from the full client
page, same "quick create, fill in detail later" split as a new order's single line
item). This exists specifically for a client with no order yet — someone who's just
inquired — where creating one via the order form would mean a throwaway order just
to get a `Client` row.

## Modals + detail pages (client / order)

From the timeline, clicking a client name (now a `<button>`, not a `<div>`) or an
order bar opens a native `<dialog>` modal — no JS framework, just
`dialog.showModal()` / `dialog.close()` wired up with a small `<script>` block at the
bottom of `timeline.html`. Each modal has a link out to a full page:

- **Client modal → `/clients/<id>`**: the modal itself is a `<form>` that POSTs
  straight to `/clients/<id>/edit` (first name, last name, email, phone), so you
  can edit without leaving the timeline. The full client page has the same
  editable form, plus lifetime value (now shown inline in the header, right of the
  name — `.detail-header--with-stat` / `.client-stats--inline`) and a "Returning"
  pill (if applicable). The client's address is structured (street / city /
  province dropdown / postal code) like the company's, and the page warns when no
  province is set, since that means their orders are billed with no sales tax. The
  edit form uses `detail-form settings-form` (420px, not the 360px default) and
  puts **Province on its own full-width row** rather than sharing one with City and
  Postal code (which now pair up on their own row instead) — a 3-up row left too
  little width for a full province name like "Newfoundland and Labrador" to render
  without clipping.
  **The client page itself has two tabs**, same sub-nav pattern as Settings
  (`.settings-nav`, reused as-is rather than a new class): "Information"
  (`client_page()`, the bare `/clients/<id>` URL — kept there rather than moved to
  e.g. `/clients/<id>/info` so every existing link to a client's page keeps
  working unchanged) and "Orders" (`client_orders()` at `/clients/<id>/orders`).
  Both render the same `client_page.html`, switched on a `section` context var
  ("info" / "orders"), and both tab links carry `return_to` through
  (`url_for(..., return_to=return_to)`) so switching tabs doesn't lose the
  timeline window you arrived from.

  **The Orders tab is a sortable, filterable table**, same `.invoice-table
  invoice-table--no-mono` look and interaction as `/orders` — clickable column
  headers doing a real `?sort=&dir=` page load (`sort_link()` macro, this one
  local to `client_page.html` rather than `orders_list.html`'s, since the target
  route differs), and the same click-a-legend-item status filter persisted to
  its own `localStorage` key (`client-orders-hidden-statuses`, independent of
  `/orders`'s `orders-list-hidden-statuses`). Columns are **Item / Status /
  Start / Due / Total / Paid / Balance / Invoice** — a fixed set
  (`CLIENT_ORDER_SORT_KEYS` in `app.py`), not reorderable/hideable per company
  the way `/orders`'s `ORDER_COLUMNS` are (see "Orders list columns are
  reorderable and hideable" above): this is one client's own roster, not a
  company-wide list a studio would want to reshape, so building the same
  customization machinery for it would be solving a problem nobody has here.
  **Invoice** is a column `/orders` itself doesn't carry — it links to the
  order's invoice number when one exists, an em dash otherwise, since knowing
  whether *this client's* order got billed reads naturally next to their own
  orders in a way it wouldn't on the company-wide list (which already has its
  own `/invoices` page for that). Client and Type are dropped, for the
  reciprocal reason: the page is already scoped to one client, and this client
  and the timeline bar are where order type reads best (see OT-rules above).
  Status still renders as the labeled `.pill.pill--{status}` used in the Status
  column on `/orders` — not the bare `.dot--{status}` marker, which has no
  label of its own and only reads correctly sitting next to a legend, like the
  one above this very table.
- **Order modal → `/orders/<id>`**: also a `<form>`, POSTs to `/orders/<id>/edit`
  (item, start date, due date, status — dates via native `<input type="date">`
  calendar pickers). The order **total is read-only here** (`.modal__readout`), since
  it's the sum of line items and there's no room for a line editor in a dialog. Both
  editing paths share the `edit_order` route in `app.py`.
  **The full order page has three tabs**, same `.settings-nav` sub-nav pattern as the
  client page, ordered **Details → Materials → Billing**: "Details" (`order_page()`,
  the bare `/orders/<id>` URL, kept there for the same "don't churn existing links"
  reason as the client page — the editable form plus notes, and the document
  explorer owned by the `documents/` module), "Materials" (`order_materials()` at `/orders/<id>/materials` — the
  self-contained `inventory/` module's cost-tracking tab — see [inventory/CLAUDE.md](../inventory/CLAUDE.md)), and
  "Billing" (`order_billing()` at `/orders/<id>/billing` — line items, the invoice
  section, and the payments section, i.e. everything money-related grouped together,
  see [docs/data-model.md](data-model.md)). All three render `order_page.html`, switched on a `section`
  context var ("details" / "materials" / "billing"), and all tab links carry
  `return_to` through.

The **client modal deliberately omits the billing address and the Notes field**
(both are only on the full client page). `edit_client()` therefore checks
`if "street" in request.form` / `if "notes" in request.form` before writing either
— a field the form didn't render must mean "not shown", not "cleared". Any future
field that's on one form but not the other needs the same treatment.

**`Client.notes`** is a free-text `Text` column, staff-facing only (never shown to
the client), with no relation to an order's own `notes` — same rendering pattern as
`Order.notes` (a plain `<textarea>` on the full page's edit form), but there's no
timeline modal for clients to build a quick-edit/full-page split around in the first
place, so it just lives on the one edit form the client page already has. Renders
`{{ client.notes or '' }}`, not bare `{{ client.notes }}` — Jinja prints `None` as
the literal string "None", and a brand-new client's `notes` column starts out `None`
(nothing sets it on creation), so the bare form would show every never-edited
client's notes box pre-filled with the word "None".

**Why modals *and* pages, not just one:** both edit forms are quick enough to fit a
dialog, but order details have more surface area (and will grow once real document
upload lands), which doesn't fit a small dialog well — the full order page adds
notes and the document list on top of what the modal exposes. Quick edits → modal,
anything with more real estate or file handling → page.

**How modals are pre-rendered:** `timeline_window()` in `app.py` builds a
deduplicated `clients_in_view` list (each client appears once even if they have
multiple orders in the visible window) and passes it alongside `rows`. `timeline.html`
loops both to render one `<dialog>` per client and one per order *currently on
screen* — not the whole database. This keeps the page light at prototype scale but
won't scale indefinitely; if the order count grows a lot, revisit pre-rendering every
dialog inline vs. fetching modal content on demand.

**`return_to` pattern:** every link from a modal to a full page appends
`?return_to={{ request.path }}` (the current timeline window's URL), and both the
client and order edit forms include it as a hidden field. Routes read
`request.args.get("return_to")` / `request.form.get("return_to")` and redirect there
after saving, so editing a client or order and clicking "Save" — or just clicking
the back link on a detail page — returns to the *same* timeline window instead of
resetting to today's date. If you add more cross-page links, carry this parameter
through the same way.

The back link's **wording** comes from `back_label()` in `app.py`, which maps the
`return_to` path to "Back to timeline" / "Back to invoices" / etc. It used to be
hardcoded to "Back to timeline", which stopped being true once client and order
pages became reachable from `/invoices`. Routes pass `back_label=back_label(return_to)`
explicitly, matching how `status_labels` and friends are passed.


## Core app templates

Module-owned templates live under each module's own `templates/` directory and
are documented in that module's `CLAUDE.md`.

```
templates/
  base.html                 # shared <head>, view-switch nav (hidden when logged out), footer
  _settings_nav.html        # /settings sub-nav (+ integration alert badge), shared with the module
  _client_nav.html          # client page sub-nav, ditto
  _clients_nav.html         # Clients/Leads sub-nav + the waiting-leads badge
  login.html                # sign-in form (extends base.html)
  calendar.html             # month view (extends base.html)
  timeline.html             # Gantt-style multi-week view + modals (extends base.html)
  client_page.html          # full client profile, editable (extends base.html)
  order_page.html           # full order detail, editable (extends base.html)
  new_order.html            # create-order form, incl. inline "add new client" (extends base.html)
  new_client.html           # standalone create-client form (extends base.html)
  orders_list.html          # every order, sortable (extends base.html)
  clients_list.html         # every client, sortable (extends base.html)
  invoices.html             # invoice list + "not invoiced yet" orders (extends base.html)
  invoice_page.html         # one printable invoice + its settings form (extends base.html)
  settings.html             # company settings (invoicing prefix + SourceOptions, extends base.html)
  analytics.html            # company-wide client/revenue stats (extends base.html)
```
