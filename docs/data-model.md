# Data model & migrations

> Part of the `leather_orders` app. See the root [CLAUDE.md](../CLAUDE.md) for
> stack, conventions and design language, and [REQUIREMENTS.md](../REQUIREMENTS.md)
> for the core app's rules as numbered, checkable statements.

## Data model (SQLite via SQLAlchemy, `models.py`)

`db = SQLAlchemy()` is defined in `models.py` and initialized against `app` in
`app.py` (`db.init_app(app)`). Tables are created and bootstrapped automatically on
startup (`db.create_all()` + `seed_if_empty()` — which returns immediately once a
company exists, so it's safe to import repeatedly).

**`seed_if_empty()` creates the tenant, not a dataset.** One `Company` ("By
Monsieur", renameable from /settings), one admin `User`, and the two
company-configurable option lists (`_DEFAULT_SOURCE_OPTIONS`,
`_DEFAULT_ORDER_TYPES`) — no clients, orders or invoices, and an *empty* billing
letterhead. A production deployment is meant to start genuinely empty, so the first
real order entered is order #1. The demo dataset — ten sample clients, twelve
orders, four invoices and a placeholder letterhead — lives in `sample_data.py` and
is loaded only by running `scripts/seed_sample_data.py` by hand (see **Sample
data** below).

**`create_company()` does the actual work, and is the one provisioning path.**
`seed_if_empty()` calls it for the first tenant; the platform admin area
(`/admin`, see [admin/CLAUDE.md](../admin/CLAUDE.md)) calls it for every one
after. That's the point of it being a function rather than inline code — a
starter list added here can't reach one caller and miss the other, so the tenth
studio gets byte-for-byte what the first one got, with no exceptions — the
user it creates is always a **tenant user**.

**Platform staff are a different kind of account and have no company.**
`users.company_id` is nullable for exactly that reason, and it's the only
nullable `company_id` in the schema. A null there never means "no tenant
filter" — it means the user isn't allowed on a tenant route at all, which
`app.py`'s `_keep_staff_out_of_tenant_routes` enforces in one place.
`ensure_platform_admin()` creates that account, guarded on whether any staff
exist rather than on whether the database is empty; see
[admin/CLAUDE.md](../admin/CLAUDE.md) for why those aren't the same question.

Fixed reference data the app can't work without is **not seeded at all**: province
tax rates (`billing/tax.py`) and the inventory unit catalog (`inventory/config.py`)
are code constants, present in every deployment. Per-company rows that are fixed
rather than configurable are created lazily on first use instead — the billing
letterhead (`invoicing.profile_for()`) and the "Each" unit
(`inventory.services._ensure_default_unit()`) — so a company created any other way
than through `seed_if_empty()` gets them too.

```python
Company(id, name, timezone, is_active,                          # tenant boundary; letterhead moved
        order_columns)                                          # to billing.BillingProfile, see below
User(id, company_id, email, full_name, password_hash,           # email is the login identity, and
     signature, is_active, is_platform_admin)                   # the only globally unique column
                                                                # company_id is NULL for platform
                                                                # staff — the one nullable one
Client(id, company_id, first_name, last_name, email, phone,     # .name -> "first last"
       street, city, province, postal_code,                     # province decides tax
       inquiry_type, first_message,                             # lead-capture fields, see below
       other_source_detail, notes)                              # see below; notes are staff-facing
SourceOption(id, company_id, label, sort_order, is_active,      # company-configurable, see below
             is_other)                                          # at most one per company, see below
client_sources                                                  # Client <-> SourceOption join table
OrderType(id, company_id, label, sort_order, is_active)         # company-configurable, optional per order
Order(id, client_id, item, start, due, pickup_date, status,     # .total from its lines, no price column
      order_type_id, notes)
OrderLine(id, order_id, description, quantity, unit_price, sort_order)
Payment(id, order_id, amount, paid_date, method, reference)     # one order can have many, see below
```

**That's all of `models.py`.** Everything else lives in the module that owns
it, and is documented there — `Invoice` / `InvoiceTaxLine` / `BillingProfile` in
[billing/](../billing/CLAUDE.md) (note `Invoice.subject_id`, not `order_id`),
`Document` / `DocumentType` in [documents/](../documents/CLAUDE.md),
`InventoryItem` and friends in [inventory/](../inventory/CLAUDE.md), and the
mail/calendar tables in [communications/](../communications/CLAUDE.md).
The invoice rules below still describe behaviour the core app depends on
(`Order.total` delegates into billing), which is why they're documented here.

(Model/table renamed from `Customer`/`customers` to `Client`/`clients`, and
`Order.customer_id` to `Order.client_id`, to match how the business actually talks
about the people it makes things for. If you're grepping old commits or docs for
"customer", that's why it won't match current code.)

**Multi-tenancy:** everything hangs off `Company` via `company_id` (`User` and
`Client` directly, `Order`/`Document` transitively through `Client`/`Order`).
This is schema-only prep for a possible future SaaS product where each studio is its
own company with one or more logins — there's no tenant switcher or signup flow, and
today exactly one company is created on first boot ("By Monsieur" unless
renamed from /settings). Every query in `app.py` filters
by `current_user.company_id` (see `get_order_or_404` / `get_client_or_404` /
`orders_by_day` / `timeline_window`) so a second tenant can be added later without
touching query logic elsewhere — just don't add a query that skips this filter.

**Relationships replace the old manual reference-wiring:** `Order.client` /
`Client.orders` / `Company.users` / `Company.clients` are real SQLAlchemy
relationships, so templates write `order.client.name` exactly like before — no
template changes were needed when this moved off in-memory dicts.

`Client.name` is a `hybrid_property` (`f"{first_name} {last_name}"`), not a
column — read-only, used for display everywhere. Edit forms (client modal, full
client page) always show separate **First name** / **Last name** fields and write
to `first_name`/`last_name`.

**Returning clients & lifetime value:** `Client.is_returning` (a plain `@property`,
`len(self.orders) >= 2`) and `Client.lifetime_value` (`sum(o.total for o in
self.orders if o.status != "cancelled")`) are computed on the fly from the `orders`
relationship — no stored columns, so they're always correct without needing to
update a flag whenever orders change. **`lifetime_value` skips cancelled orders**
(CL2a) — an order that was called off was never business done, and this figure
ranks Analytics' top clients and the timeline's highest-paying-client sort.
`is_returning` still counts them: that a client came back and asked a second time
is true whether or not that second order went ahead. `is_returning` drives a small star icon next to the client's name
(`.timeline__star`) — on the timeline, and next to the client column on `/orders`
and `/clients` — plus a "Returning" pill on the full client page
(`.pill--returning`). Each of those three tables carries a small
`.timeline__legend-note` key ("Returning client") below the table itself instead
of up in `.legend-row` with the status filter buttons (timeline/orders) — it isn't
a filter, clicking it does nothing, and putting it up there read as though it were
one. `lifetime_value` is shown at the top of the client page
(`.client-stats`), and each order in that page's order list also shows its own
price now.

**Hiding a client — `Client.is_hidden`:** the strictest reading of "hide,
don't delete". A `Client` has **no** `can_delete` and no delete route, and
couldn't sensibly have one: `Order`, `Invoice` and `EmailThread` all reference
a client, so a deleted row would leave every one of them pointing at nobody.
Hiding is one boolean and `toggle_client_hidden()` in `app.py` is the whole
of it.

**Its scope is the roster and only the roster** (`CL18`) — `/clients` and the
new-order client picker. It touches no order, no invoice, no payment, no email
thread, and **not one figure on `/analytics`**: revenue, lifetime value, top
clients and the source breakdown all read a hidden client exactly as before.
The argument is the one behind `CL2a` (cancelled orders leaving
`lifetime_value`) and `AN4` (hidden `SourceOption`s staying in the breakdown)
turned the other way — an order is time the studio spent and money it was
owed, and tidying a contact list doesn't get to retract that. It's also why
there is deliberately **no option** to hide the orders too: "hidden" would
then mean two different things and `/analytics` would answer differently
depending on which one a user picked months ago.

**It undoes itself.** New *incoming* mail matched to a hidden client puts them
back on the roster (`CL21`), counted as `SyncResult.clients_resurfaced` and
handled in `email_sync._store_message` right beside the lead inbox's own
resurface — same gating, same reasoning (`L-15`, `L-16`). No `R-6` exemption
to mirror, since no sender rule can hide a client in the first place.

**Lead-capture fields on `Client`:** `inquiry_type` (matches the "About" dropdown on
the bymonsieur.ca contact form) and `first_message` (the inquiry text) are blank for
clients added manually through the app, and are meant to be populated by an inbound
webhook when the contact form gets wired up (see [docs/roadmap.md](roadmap.md) — `/api/leads` doesn't
exist yet as of this writing, just the columns to receive it).

**"How did you hear about us?" — `SourceOption` + `/settings/clients`:** this is
company-configurable, not hardcoded, matching the checkboxes on the bymonsieur.ca
contact form (seeded defaults: Google Search, Word of Mouth, Craft Market / Open
Studio, Instagram, Facebook, LinkedIn, Other — see `_DEFAULT_SOURCE_OPTIONS` in
`models.py`). `Client` ↔ `SourceOption` is a real many-to-many (`client_sources`
join table) rather than a comma-separated string, specifically so per-option stats
("how many clients came from Instagram") can be queried reliably and so
`SourceOption.can_delete` (`len(self.clients) == 0`) can enforce the next rule:
**an option is never hard-deleted once any client references it** — the
`/settings/clients` page (`settings.html`) only offers a "Delete" button when
`can_delete` is true;
otherwise "Hide" (`is_active = False`) is the only option. Hiding removes it from
the checkbox list offered on *new* selections (`client_page()` in `app.py` unions
active options with whatever a specific client already has, so a hidden option a
client is already tagged with still displays on their page, marked "(hidden)", just
isn't offered to other clients). This preserves historical answers/stats — see the
docstring on `SourceOption` in `models.py`. **Reorderable by drag-and-drop**
(`reorder_source_options()` in `app.py`, `/settings/sources/reorder`) — same
native HTML5 drag-and-drop pattern as `documents.reorder_types`
(`draggable="true"` list items, a grip handle, `.settings-source-list__item--dragging`
/ `.settings-source-list__grip` CSS, a JSON `fetch` fired on `dragend`, ids
outside the company silently skipped since the request is data from a
`fetch()` call rather than a form the server built), just inlined in
`settings.html`'s clients section instead of a separate partial since this
list isn't shared with any other page the way `_settings_types.html` is.

**One `SourceOption` per company can carry a free-text box —
`SourceOption.is_other` / `Client.other_source_detail`:** "Other, please
specify" is the obvious use, but the mechanism is really "pair a text box
with this checkbox", not a fixed "Other" label match — a studio could put it
on any option that needs more detail than a checkbox gives. Toggled from
`/settings/clients` (`set_other_source_option()` in `app.py`), which unsets
whatever option held it before setting a new one — **at most one per
company**, because there's only one `other_source_detail` column on
`Client`, not one per option. Supporting more than one at a time would need
the detail moved onto `client_sources` (or a new table) keyed per
`(client, source_option)` pair; not built, since nothing has asked for it
yet. On the client page (`client_page.html`), the box (id
`source-other-detail`) is rendered `hidden` unless that option is already
checked, and a small inline `<script>` toggles it on the checkbox's
`change` event (`id="source-other-checkbox"` on the matching `<input>`).
`edit_client()` only writes `other_source_detail` when the is_other option
is among the submitted `source_ids` — unchecking it clears the detail too,
rather than leaving stale text nobody can see. **The same fallback applies
to mail read automatically** — `_apply_details()` in
`communications/services/email_service.py` still matches an incoming
form's "how did you hear about us" text against existing `SourceOption`
labels first (see "Field mapping" under the communications module below);
only when nothing matches does it fall back to the company's is_other
option (if any), tagging the client with it and writing the raw text into
`other_source_detail` — still only filling a blank, same "never overwrite
what a person typed" rule as every other mapped field.

**Order types — `OrderType` + `/settings/orders`:** same company-configurable,
hide-don't-delete shape as `SourceOption` (`OrderType.can_delete` is
`len(self.orders) == 0`; `add_order_type()` / `toggle_order_type()` /
`delete_order_type()` in `app.py` back the "Order types" section of
`settings.html`, which is a copy of the source-options section reusing the same
`.settings-source-list` CSS), but **single-select and optional**, not a many-to-many
checkbox group: `Order.order_type_id` is a plain nullable FK, not a join table,
because an order has at most one type. Seeded defaults (`_DEFAULT_ORDER_TYPES` in
`models.py`): Custom Order, White Label, Consulting/Sampling — arbitrary starting
examples, not tied to anything on the bymonsieur.ca site the way `SourceOption`'s
defaults are, and fully replaceable per company. **The dropdown only appears at all
once a company has at least one `OrderType`** — `new_order()` and `order_page()` in
`app.py` pass an `order_types` list to the template, and both `new_order.html` and
`order_page.html` wrap the `<select>` in `{% if order_types %}`; a company that never
adds one just doesn't get order-type UI, no "Uncategorized" placeholder needed. The
full order page uses the same active-options-∪-current-selection pattern as the
client page's source checkboxes (so a hidden type an order already has stays
selectable there); `new_order()` only offers active types, since a brand-new order
can't already be tagged with a hidden one. `edit_order()` guards on `"order_type_id"
in request.form` before touching it — same "a form that doesn't render a field means
leave it alone, not clear it" rule as the client address fields — since the
timeline's quick-edit order modal deliberately does **not** include order type
(matching the modal/page split: `Total` is already read-only there for the same
reason). Where it does show: inline in the timeline bar's own label
(`.timeline__bar-type`, e.g. "Weekender duffel · Custom Order") — deliberately on
the bar, not next to the client's name, since the type belongs to the order, not
the client; putting it in the (fixed-width) name column was tried first and made
that column cramped regardless of how wide it was, whereas the bar's width already
scales with the order's own duration. No per-type color — types are open-ended/
custom, unlike the four fixed order statuses, so there's no fixed color mapping to
give them. Also shown as a sortable **Type** column on `/orders`
(`ORDER_SORT_KEYS["type"]`) — that column (header and cells both) only renders when
`has_order_types` is true, i.e. the company has created at least one `OrderType`
ever, active or hidden; `orders_list()` checks existence, not `is_active`, since a
hidden-but-still-referenced type is exactly the case the column needs to keep
showing for. A company that's never touched the feature sees no Type column at all,
matching the dropdown's own "don't show it until there's something to show" rule.

`start`/`due` on `Order` are real `Date` columns, edited via `<input type="date">`
(native browser calendar picker) in both the order modal and the full order page.
Sample data still has hand-picked, plausible lead times per item, **not real numbers
from the client yet** — same caveat for `price`. `pickup_date` is a separate,
optional `Date` column (nullable, no default) for when the client actually collects
the finished piece, since that can slip past `due` (the promised date) — it isn't
derived from `due` or `status` because a "ready"/"delivered" order doesn't always
mean picked up on the day it was marked that way. Editable only on the full order
page's Details tab, directly below Due date (`order_page.html`) — not exposed in
the timeline's quick-edit order modal, same "quick edit → modal, more room needed →
page" split as the rest of order editing (see [docs/views.md](views.md)).
`edit_order()` in `app.py` guards on `"pickup_date" in request.form` before touching
it, same convention as `order_type_id`. **Order documents are real files**, owned
by the self-contained `documents/` module (`Document` / `DocumentType`, tables
`order_documents` / `document_types`) — not the old placeholder rows, which the
module's migration drops outright. See
[documents/CLAUDE.md](../documents/CLAUDE.md).

**Payments (`Payment`, one-to-many off `Order`):** deliberately generic — just an
`amount` + a `paid_date`, no fixed deposit/balance split baked into the schema,
since different studios run different payment plans (Joe's is roughly 50% deposit +
balance at pickup, per the bymonsieur.ca contact page, but nothing enforces that
ratio — see the docstring on `Payment` in `models.py`). `price` on `Order` stays the
quoted/agreed amount; actual money received is tracked separately via
`Order.amount_paid` (`@property`, `sum(p.amount for p in self.payments)`) and
`Order.balance_due` (`price - amount_paid`) — same computed-property pattern as
`Client.lifetime_value`. Recorded on the full order page only (`order_page.html`'s
"Payments" section — add via a small inline form, remove via a per-row trash-icon
button, same `.doc-list__actions`/`icon-btn--danger` component the Line items
section and the inventory module's Materials tab use — see "Delete conventions"
under Design language). Both routes, `add_payment()` / `delete_payment()` in
`app.py`, redirect to whatever `return_to` the form submitted — but that form
field is the **tab's own path** (`request.path` at render time), not the page's
`return_to` context var (the "Back to timeline" link's destination): the two
looked interchangeable since the Billing tab renders both, and the Payments
forms used to submit the latter by mistake, which bounced the page away to
wherever the order was opened from every time a payment was added or deleted.
Line items' add/delete forms got this right from the start; Payments' were
brought in line with them (PM5 in `REQUIREMENTS.md`). Not exposed in the
timeline's order modal — same "quick edit → modal, more room needed → full
page" split as the rest of order editing.

`status` is one of: `tentative`, `confirmed`, `ready`, `delivered`, `cancelled` —
a one-way lifecycle, with `ALLOWED_TRANSITIONS` in `app.py` as its only definition
(see `REQUIREMENTS.md` OR1a). Two inactive ends around an active middle:
`tentative` is a conversation that hasn't been committed to, `delivered` and
`cancelled` are both over. `Order.is_active` derives the middle.

**`in_progress` is a label, not a stored value.** `Order.display_status` returns it
for a `confirmed` order whose `start` has arrived, and every list, pill, dot and
timeline bar renders `display_status` while forms post back the raw `status`. This
is the same "derived for display" shape as `Order.invoice_status`, and it exists so
an order can't sit at "confirmed" three weeks into the work because nobody advanced
a dropdown. There is deliberately **no** `started_at` column: the planned start date
is something the studio keeps current (moving a bar *is* rescheduling), so deriving
from it can't go stale the way a second date would.

**Deleting an order is the app's one hard delete** (`Order.can_delete`, OR1e) and
it is deliberately hard to reach: tentative only, and only with no invoice, no
payment and **no materials drawn from stock**. That last one is the interesting
constraint — deleting can't know whether the leather an order drew went back on
the shelf or was consumed on a prototype, so rather than guess it refuses, and
the user clears the materials on the Materials tab first (`delete_material()`
restocks that row, which is the conscious per-material choice). Cancelling is
the exit that leaves materials and their stock untouched. One-off "Other" costs
carry no stock and so don't block. `Document` and `OrderMaterialOther` hang off
a plain `order_id` with no cascade, so `delete_order()` clears each through its
module's services — documents especially, since those are bytes on disk.

**Rush is a separate boolean** (`Order.is_rush`), not a status — it used to be one,
which meant an order could never be both rush and ready for pickup. It's settable
only while `is_active`, cleared on the way out of the active stages, and renders as
a marker layered over the stage colour rather than a colour of its own.

The status string is still used directly as a CSS class suffix
(`chip--{{ status }}`, `dot--{{ status }}`, `timeline__bar--{{ status }}`), so
adding one means matching CSS in `style.css` (a `--chip-color` custom property per
status), a `STATUS_LABELS` entry (`app.py`), an `ALLOWED_TRANSITIONS` entry, and —
for the timeline legend specifically — a `timeline.html` legend entry, which is
still hardcoded rather than looping `STATUS_LABELS` like `calendar.html` does. It
stays hardcoded on purpose now: the legend filters on `display_status` and omits
`cancelled` (never on this page) and `rush` (no longer a status), so it isn't the
same list `STATUS_LABELS` holds.

**Line items (`OrderLine`) and `Order.total`:** an order's value is the sum of its
lines (`description` × `quantity` × `unit_price`), not a stored column — `Order.price`
used to exist and was removed, so `order.total` is what templates read. Computed for
the same reason as `lifetime_value`/`amount_paid`: it can't drift out of sync with
the lines an invoice is actually built from. `Order.item` stays as the order's short
name (timeline bar label, page title); the lines are the billing breakdown. The
new-order form still takes a single **Price** and turns it into the order's first
line — splitting it up happens on the full order page, matching the same
"quick form here, detail over there" split as payments. **`Order.is_settled`** is
float-tolerant (`balance_due < 0.005`) so a cent of rounding doesn't leave an order
looking permanently unpaid.

**What an order *cost* in materials is a separate concern from what it's
billed** — tracked by the self-contained `inventory/` module (own section
below), not by anything on `Order`/`OrderLine`. Nothing there ever writes to
`Order.total`; it's cost-tracking only, surfaced on the order page's own
Materials tab.

**Sales tax — `PROVINCE_TAXES` / `taxes_for()` in `models.py`:** tax is charged at
the **client's** province's rate (destination-based, which is how place-of-supply
works for goods shipped to a customer), and prices are **tax-exclusive** — a line's
`unit_price` is pre-tax and tax is added on top, so nothing already entered changed
value when this landed. Two rules decide what actually gets charged:

1. The client's `province` picks the row in `PROVINCE_TAXES`. **A client with no
   province is charged nothing** — `Order.tax_status` reports which of
   `no_client_province` / `unknown_province` / `not_registered` applies, and the
   order and invoice pages surface it as a `.warning-note` rather than silently
   billing zero.
2. A tax is only charged when the company holds the matching registration
   (`TaxRule.registration_field`). A studio under the small-supplier threshold has
   no `gst_number` and charges no GST; one that never registered in BC charges no BC
   PST. That falls out of the data instead of needing a separate "do we charge tax"
   switch. HST hangs off `gst_number`, since it's collected under the federal
   GST/HST registration.

**The rates in `PROVINCE_TAXES` were verified against the CRA's published table on
2026-07-30** ("Charge and collect the GST/HST — which rate to charge"), including
Nova Scotia at **14%** (reduced from 15% on 2025-04-01). They are still not tax
advice, and rates do change — but `tests/test_tax.py` pins every one of them against
a second, independently written copy of the CRA table, so a typo in `PROVINCE_TAXES`
fails the suite instead of quietly mis-billing someone. Correct rates in that table
*and* in the test, from the CRA rather than from each other.

`Order.subtotal` is the pre-tax sum of lines; `Order.tax_lines` / `tax_total` /
`total` add tax. **`Order.total` is now tax-inclusive**, which flows into
`balance_due`, `Client.lifetime_value`, the timeline sort and the invoice list.

**Invoice amounts are frozen at issue, exactly like the issuer details.** `freeze()`
writes `issued_subtotal` plus one `InvoiceTaxLine` per tax, and
`Invoice.is_frozen` (i.e. `issued_subtotal is not None`) is the marker. Once frozen,
`Order.total` reports what was billed, so editing line items afterwards **cannot**
change a number the client has already been given. `InvoiceTaxLine` is a real table
rather than JSON specifically so tax collected can be summed straight out of the
database, which is what a GST/QST remittance needs.

**Payment `method` + `reference`:** `method` is one of `cash` / `etransfer` /
`square` / `other` (labels in `PAYMENT_METHOD_LABELS`, `app.py`), and `reference` is
the matching stub — an e-transfer confirmation code, a Square payment id — so a row
can be traced back to a bank or Square statement. Square is listed alongside the two
manual methods rather than treated specially: the app owns the invoice record either
way, and the method only records how the money arrived.

**Invoices:** one per order (`Invoice.order_id` is unique), owned by the company, and
**the app owns the number** — `next_invoice_number()` in `models.py` builds
`{Company.invoice_prefix}-{year}-{0001}` from the highest existing number for that
company and year (the highest existing number, not a count, so **voiding** one never
causes a later invoice to reuse a number — the row stays. Note the limit, pinned in
`tests/test_invoicing.py`: *deleting* the most recent invoice does hand its number
back, since there's then no higher number to read. Harmless for a draft nobody has
seen; fix it before exposing invoice deletion in the UI). A unique constraint on `(company_id, number)` is the real
guard: two simultaneous requests collide there rather than silently issuing the same
number twice. `Invoice.status` only stores the states the app can't work out for
itself — `draft` / `sent` / `void` (`SETTABLE_INVOICE_STATUSES` in `app.py`).
**Paid-ness is derived, never stored**: `Invoice.display_status` returns `"paid"`
when the order's payments cover its total, so it can't disagree with the payment
rows. `INVOICE_STATUS_LABELS` includes `paid` for display but it's deliberately not
offered in the status `<select>`. `Invoice.is_outstanding` (issued, not void, still
owed money) is what the outstanding totals on `/invoices` and `/analytics` sum.

**Company details on the invoice, and why they're snapshotted:** the letterhead
(name, address, GST/HST, QST, NEQ, payment instructions) lives on `Company`, edited
at `/settings`, but an invoice **must not read them live** — reprinting an invoice
has to match what the client actually received, even if the studio has since moved
or re-registered. So `Invoice` carries `issuer_*` columns holding a frozen copy, and
templates read **`invoice.issuer`** (an `IssuerDetails` dataclass), never
`invoice.company`. The rule the property implements:

- **Draft** → reads live off `Company`. A draft hasn't been issued to anyone, so
  fixing a typo in the GST number should propagate to every draft.
- **Anything past draft** → reads the frozen copy.
- `freeze_issuer()` is called **only on the draft → issued transition**
  (`set_invoice_status()` compares the status before and after). Calling it on an
  already-issued invoice would rewrite history, which is the whole thing the
  snapshot prevents — so re-saving a "sent" invoice deliberately doesn't re-copy.

**The company address is structured** (`street` / `city` / `province` /
`postal_code`) so the province can be a dropdown and the postal code normalised,
but `Company.formatted_address` renders it to a single string ("street\nCity, PROV␣␣Postal",
two spaces before the postal code per Canada Post) and **that's what gets frozen**
onto an invoice as one `issuer_address` column. A snapshot only has to reproduce
what was printed, so mirroring four columns onto every invoice would buy nothing.
`province` stores the two-letter code; `PROVINCES` in `app.py` maps codes to full
names for the dropdown, and `update_company_details()` rejects anything not in it.

Registration numbers are named Canadian columns rather than a generic label/value
list, so they can be labelled correctly on the document; `IssuerDetails.registrations`
returns the `(label, value)` pairs that are actually set, ordered **GST/HST → PST/RST
→ QST → NEQ** (tax accounts first, NEQ last since it identifies the enterprise rather
than a tax account). `pst_number` is one field covering BC PST, Saskatchewan PST and
Manitoba RST — a seller is realistically registered in at most one of them, and Quebec
sellers use QST instead. If someone ever needs two at once, that's the signal to move
registrations to a label/value list rather than adding `pst_number_2`. `payment_instructions` is
free text printed under **How to pay**, and only when there's still a balance owing
and the invoice isn't void — it exists because cash and e-transfer have no hosted
payment page to send anyone to.

**Sample data (dev/demo only):** `sample_data.py` holds the demo clients, orders,
invoices and placeholder letterhead that `seed_if_empty()` used to insert.
Nothing imports it at startup — load it deliberately:

```bash
python scripts/seed_sample_data.py
```

It writes to whatever database the app itself would use, so set `DATABASE_URL` in
the environment first (**before** the script imports `app` — hard rule 13) to point
it at a throwaway file rather than your real one. It refuses to run if the company
already has clients: it fills an empty install, it never resets a populated one.

**The dates are computed, not written down.** Every `start`, `due`, payment date and
invoice issue date in `sample_data.py` is stored as a *day offset* from the day the
seed runs (`seed_sample_data(today=...)` pins it, for tests). The spread is fixed
and deliberate: one order already delivered, several straddling today, several not
yet started, everything inside ±30 days. The timeline is the landing page, so a
demo seeded a year from now has to open on current and upcoming work rather than an
empty window with everything in the past — which is exactly what the old hardcoded
August-2026 dates would have produced. Two consequences worth knowing if you edit
the set: no payment or invoice may be dated after the seed day, and an order that
hasn't started yet can't be `delivered` or `ready` (`CO9d`, `CO9e`). Invoice
numbers come from billing's own per-year sequence for the same reason — the year
moves.

**Starting over during development:** stop the server, delete `data/atelier.db`,
restart (which re-bootstraps an empty tenant), then run the script above if you
want the sample data back.

## Migrations

There's no Alembic here, and `db.create_all()` only ever adds missing *tables*, never
missing columns — so `run_migrations()` in `models.py` (called from `app.py` at
startup, between `create_all()` and `seed_if_empty()`) applies schema changes to
existing SQLite files by hand. Every step is a no-op once applied, so it's safe on
every boot and on a fresh database.

- **`_ADDED_COLUMNS`** is a list of `(table, column, DDL)` for columns added after
  their table first shipped (`companies.invoice_prefix`, `clients.address`,
  `payments.method`, `payments.reference`, `orders.order_type_id`,
  `companies.timezone`, `orders.pickup_date`, `companies.order_columns`,
  `source_options.is_other`, `clients.other_source_detail`, `clients.notes`,
  `users.signature`, `orders.is_rush`, `clients.is_hidden`). Its DDL spells the default zone as a literal rather
  than interpolating `DEFAULT_TIMEZONE` — a migration records what shipped and
  must not change if that constant does. Adding another
  column to an existing table means appending here — otherwise it'll work on your
  machine (fresh DB) and fail on any deployment with an older file. Brand-new
  *tables* (like `order_types` itself) don't need an entry — `db.create_all()`
  already creates any table it hasn't seen before, migrations only cover columns
  added to a table that already existed. Note this means an *existing* database
  gets the `order_types` table but no rows in it — `_DEFAULT_ORDER_TYPES` is only
  written when `seed_if_empty()` creates a brand new company, same as
  `_DEFAULT_SOURCE_OPTIONS` always has; a studio already running the app adds its
  own types from `/settings`.
- **`_migrate_free_text_address(table)`** runs for each of `_SPLIT_ADDRESS_TABLES`
  (`companies`, `clients`) and moves an old single `address` text column into
  `street`/`city`/`province`/`postal_code`, then drops it. Best effort: a last line
  matching `City, PROV  Postal` (`_ADDRESS_TAIL`) is split out properly, anything
  else lands whole in `street` and reads visibly wrong in the UI until re-entered.
  Guessing harder was deliberately avoided — filing a client under the wrong
  province now silently changes the tax they're charged.
- **`_backfill_invoice_issuers()`** freezes invoices issued before there was
  anything to freeze, and the two halves differ on purpose. *Issuer details*:
  today's company settings are the only approximation available, so it writes those
  rather than let `Invoice.issuer` keep falling back to live values. *Money*: it
  freezes `issued_subtotal` but writes **no tax rows** — those invoices were issued
  before tax was calculated at all, so zero tax is what their clients actually
  received, and inventing tax now would change amounts already billed.
- **`_migrate_order_price_to_lines()`** converts each legacy `orders.price` into a
  single `OrderLine`, then `DROP COLUMN`s it. The drop is part of the migration, not
  cleanup: the column was `NOT NULL` with no default and nothing writes it any more,
  so leaving it would make every new order fail to insert. (Needs SQLite 3.35+ for
  `DROP COLUMN`; the alpine images and any current Python are well past that.)
- Column state is snapshotted once up front rather than re-reflected between steps —
  SQLAlchemy's `Inspector` caches reflection results anyway, and no step depends on
  an earlier one.

