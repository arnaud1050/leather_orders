# Known gaps / natural next steps

> Part of the `leather_orders` app — see the root [CLAUDE.md](../CLAUDE.md) for orientation.

- **Postgres/MySQL**: currently SQLite (`sqlite:///data/atelier.db`). Moving to a
  networked database is a `SQLALCHEMY_DATABASE_URI` change plus installing the right
  driver (`psycopg2`/`pymysql`) — the ORM models and query code in `app.py` don't
  need to change as long as SQLite-only features are avoided (none are currently
  used).
- **Multi-tenancy**: *first iteration built* — a platform admin (`/admin`)
  provisions companies, adds users to them, deactivates either, and can
  impersonate a tenant user for support. Email replaced usernames as the login
  identity to make it possible, and platform staff sit **outside** every
  tenant: no company, no timeline, `/admin` only. `/admin` also carries the
  installation's first genuinely platform-wide setting — a maintenance/
  announcement banner (`/admin/settings`), optionally scheduled to appear
  and disappear by itself, shown on every page including
  signed-out ones. See [admin/CLAUDE.md](../admin/CLAUDE.md).
  Still unbuilt, in rough order of likely need: **roles within a company**
  (owner vs. member — today every tenant user can do everything), an **audit
  log of admin actions** (probably by generalising `communications/`'s existing
  audit table rather than starting a second one), **self-serve signup**, and
  **billing/plans**. A tenant switcher remains deliberately absent — a user
  belongs to one company, and impersonation covers the support case. The
  password-policy minimum (`MIN_PASSWORD_LENGTH`, currently duplicated as two
  hardcoded `8`s in `app.py` and `admin/services.py`) is the next obvious
  candidate for `/admin/settings` — same shape as the announcement, smaller
  than the roles/audit-log items above.
- **Password management**: *partly built*. Changing your own password is at
  `/settings/account`; a platform admin adds users and resets anyone's password
  from `/admin`. Still missing: **reset by email**, which is blocked on the app
  having no address of its own to send from — the Gmail accounts under
  Email/Calendar are the studio's client correspondence, not app mail. Until
  that changes, a platform admin sets an initial password and hands it over out
  of band.
- **Communications, Phase 2**: not built, and the architecture is shaped for
  each of them — Gmail push notifications (`sync_account` already takes an
  explicit window), Microsoft Graph / IMAP (a module in `providers/` plus two
  entries in `registry.py`), and the AI layer (`EmailThread.summary` exists
  unused; anything AI must stay independent of sync, which must keep working
  with AI off). Also not done: a per-thread "read/unread" state —
  `gmail.modify` was requested partly for that but nothing writes label changes
  back yet. Nor is there a way to **delete** a calendar event. (Managing an
  existing event's guests *is* built now — see the Calendar view section.)
  One loose end there: removing a guest doesn't reliably tell the person
  removed. `sendUpdates` is set for the save as a whole, and the app doesn't
  distinguish "added Anna" from "dropped Luc" — so saving with notify on tells
  everyone still on the list, and whether the removed guest hears about it is
  Google's business. Settle whether that's worth surfacing before adding
  per-change notification.
- **Lead capture from the contact form**: `Client.sources` (via `SourceOption`) /
  `inquiry_type` / `first_message` exist (see [docs/data-model.md](data-model.md)) but nothing populates
  them automatically yet. Note the email side now has a working equivalent —
  `create_client_from_thread()` in `communications/services/email_service.py`
  fills the same `first_message` field — so a `/api/leads` endpoint should
  produce a client that reads the same way. Plan is a Make.com scenario (free tier — its Custom
  Webhook trigger doesn't require a paid plan) watching the bymonsieur.ca
  Squarespace contact form, POSTing to a new authenticated `/api/leads` endpoint
  that creates a `Client`. Not built yet — next step once the exact Squarespace
  field names/payload shape are confirmed. Also under discussion: whether the app
  should eventually draft (not send) a follow-up email via the Claude API for staff
  to copy into their own email client, rather than trying to send/receive email
  natively (a much bigger integration).
- **Creating an order from an enquiry.** The other half of the same request:
  a converted lead should arrive with an order attached, named from what the
  person actually asked for ("Mulberry bifold — ID window replacement"), which
  is a job for the Claude API rather than a rule. Note the module cannot do
  this by itself — `communications/` must not import `Order`, so the honest
  shape is a hook the host registers (the same way `billing/` takes
  `resolve_billable`), or an app-side listener over `AutoCreatedClient` rows.
  Worth settling that boundary before writing any of it.
- **Merging duplicate clients.** A sender rule reuses an existing client by
  **email address only** — never by name, since two people share a name far
  more often than an inbox, and silently merging two client records isn't
  something an unattended sync should be able to do. The cost is that a
  returning customer who fills in the contact form with a *different* address
  gets a second record. The fix is a **manual** merge: pick two clients, move
  orders, email threads, sources and payments onto one, keep the older record
  (its `created` history and its client-facing invoice numbers are the ones
  already in the world), and leave an audit line. Note where it has to live —
  `communications/` must not import `Order`, so merging is app-side
  (`/clients/<id>/merge`) calling into the module for the thread half, same
  boundary question as creating an order from an enquiry above.
  **Hiding is the partial answer that now exists** (`CL17`–`CL21`): the
  duplicate can be taken off the roster, keeping both records and both
  histories. It isn't a merge — the orders stay split across two clients, so
  neither one's lifetime value is right — but it stops the list filling up
  while the real thing is unbuilt. Note the interaction to get right if merge
  ever lands: the losing record is currently *hidden*, not deleted, and a
  merge would want to leave it that way rather than inventing a second
  disposal.
- **Square integration**: invoicing is currently local-only — the app numbers,
  renders and prints its own invoices, and payments are entered by hand whatever
  their `method`. The schema was shaped for the intended next step ("Path A"): the
  app keeps owning the invoice record and its number, and Square becomes one way to
  *deliver* the document and collect card payment, not a second source of truth.
  That would add, on `Invoice`, a `square_invoice_id` + `public_url` written back
  after `CreateOrder` → `CreateInvoice` → `PublishInvoice`, and a
  `/webhooks/square` endpoint (signature-verified) that records a `Payment` with
  `method="square"` and the Square payment id as `reference` — which is what
  `Payment.reference` already exists for. Two things to get right when it happens:
  sandbox vs production base URLs (`connect.squareupsandbox.com` /
  `connect.squareup.com`) belong in config, not scattered through code, or a test
  invoice eventually goes to a real customer; and OAuth is preferable to pasting a
  seller's personal access token, which is a full-access, non-expiring credential to
  their live business. **Discipline rule if this lands:** invoices must stop being
  created in the Square dashboard, since Square auto-numbers those and they'd
  collide with `next_invoice_number()`'s sequence.
- **Editing a line item**: lines can be added and removed, but not edited in place —
  changing one means removing it and re-adding. Fine at prototype scale; an inline
  edit form per row is the obvious follow-up.
- **Tax rates need periodic re-checking.** Verified against the CRA on 2026-07-30
  and pinned by `tests/test_tax.py`, but rates change; re-confirm before an
  accounting period closes. Still not tax advice.
- **Deleting the newest invoice frees its number.** See the numbering note above —
  a real limit of deriving the sequence from the highest existing row, and the first
  thing to fix if invoice deletion is ever exposed in the UI.
- **No per-line taxable flag**: tax applies to an order's whole subtotal. Fine while
  everything sold is taxable, which is true of leather goods; zero-rated or exempt
  items would need a flag on `OrderLine` and `taxes_for()` taking a taxable subtotal
  rather than the full one.
- **Nothing stops you editing an issued order's line items.** The invoice total is
  safely frozen (that's the point of `issued_subtotal`), so the client is never
  re-billed, but the order page will then show line items that don't add up to the
  invoice. The order page says so in a note; properly, adding or removing lines
  should be blocked once `order.is_issued`.
- ~~**No tax-collected report.**~~ *Done.* `/analytics`'s Revenue section now
  carries a **Tax billed YTD** card, one row per tax (GST, QST, …), summed
  from the frozen `InvoiceTaxLine` rows via `invoicing.tax_collected(company_id,
  since=Jan 1)`. It's labelled "billed" (accrual basis — every issued, non-void
  invoice, paid or not), which is how a Canadian remittance is normally filed. A
  cash-basis version and a per-period (quarter/custom range) view are both still
  open if a real remittance workflow is ever wanted.
- **Client-level documents**: the `documents/` module attaches files to an
  **order** only. Documents belonging to a client generally (a signed contract, a
  measurements sheet) were discussed and left out of scope — see
  `documents/REQUIREMENTS.md` §1. (Order-level upload/download itself is built;
  it is no longer a gap.)
- **Real lead times**: the `start` dates added for the timeline view are estimates,
  not client-provided numbers. Revisit once the client shares actual production lead
  time per item type.
- **Prices**: also estimates, not real numbers — same caveat as lead times.
- **No inventory-value report.** Threshold low-stock alerting *does* now
  exist — a per-item warning point driving an amber tier beside the red
  zero-or-negative one (see the Inventory module's "Stock alerts" section) —
  but there's still no rolled-up "total stock value" figure. An obvious next
  addition alongside the revenue cards on `/analytics`.
- **No image on an inventory item.** A thumbnail is the one thing that would
  actually show *which* leather a row means, and it's wanted — deliberately
  deferred rather than dropped, since it's an upload/storage/thumbnailing
  job (the `documents/` module's territory) rather than another column. The
  descriptive fields it would sit beside — `reference`, `url`, `notes` —
  shipped first (`I14`), and `INVENTORY_COLUMNS` is where its column would
  be declared when it lands.
- **A material's item and unit can't be changed once added, only its
  quantity** (`inventory.services.edit_material`) — same "remove and re-add"
  limit `OrderLine` has, and for the same "fine at prototype scale" reason.

## Planned: Showcase (portfolio, website sync, catalog mode)

*Spec agreed 2026-10-05; steps 1–4 built, the Share button not yet.* Turns finished work into a portfolio
the studio controls from one place: each item is pushed to the studio's own
website (bymonsieur.ca's "Recent Commissions" first), shared to social media
by hand, and shown full-screen on any device at client meetings and craft
markets. It started as the "Promotion layer" of the original Cooperator
brief, which was written before this app existed, so read that brief as
background, not spec. **Generic on purpose:** nothing here may assume
leather, bags or the word "commission". A ceramicist or a jeweller must be
able to use it unchanged; each website chooses its own section label.

When this lands it becomes a self-contained module, `showcase/`, with its own
`CLAUDE.md` + `REQUIREMENTS.md`, and these notes move there.

### Sold per company: a feature flag

- The platform admin turns Showcase on or off **per company** from `/admin`
  (the company page). It's meant to be sold later as an extra or a higher
  tier, so it's built as the first entry of a **generic per-company feature
  mechanism**, not a one-off `showcase_enabled` column: a
  `company_features` table (`company_id`, `feature_key`, `enabled_at`)
  plus a catalog of known keys in code, the same "closed catalog" idea as
  `usage.EVENTS`. A key not in the catalog is refused. The next paid
  feature then needs a catalog entry, not a migration.
- **Off means invisible, not broken.** No nav item, no order-page tab, no
  badge, no Settings section; every showcase route 404s; kiosk links stop
  resolving; nothing more is sent to the website. **Turning it off deletes
  nothing:** items, photos and settings stay, and turning it back on brings
  them all back. Whatever was already pushed to the website stays there
  (the website owns its copy); withdrawing it is a deliberate act, not a
  side effect of a billing change.
- Checked in one place (a helper such as `company_has(company, "showcase")`),
  never by querying the table ad hoc. Tests cover flag-off for every route.
- Events go to `usage` (`showcase.item_published`, `showcase.shared`,
  `showcase.catalog_opened`, …), which is also how to tell whether the tier
  is worth selling.

### Data (all module-owned, all filtered by `company_id`)

- **`ShowcaseItem`**: `title`, `description`, `category_id`, `visibility`
  (`private` / `in_person` / `public`), `status` (`draft` / `published` /
  `withdrawn`), `published_at`, and an **optional** `order_id`. Optional because pieces made
  for stock or a market have no order, and the catalog needs them most.
  `order_id` is stored as a plain integer the module never resolves itself
  (hard rule 4); the host does it through the adapter below.
- **`ShowcaseCategory`**: per company, ordered, **hide-don't-delete**
  (hard rule 8). Mapped by name onto a website's own categories.
- **`ShowcaseSpecField`**: the studio's **fixed list of spec labels**
  (e.g. Dimensions, Leather, Hardware, Lining, Thread), defined in
  Settings, ordered, hide-don't-delete. Every item shows the same fields in
  the same order, so the catalog and website read consistently and nobody
  retypes "Hardware". **`ShowcaseSpecValue`** (`item_id`, `field_id`,
  `value`) holds an item's answers. A blank value is simply not shown
  anywhere. Hiding a field hides it on every item but keeps the values, so
  unhiding restores them.
- **`ShowcasePhoto`**: the module's **own copies** of the chosen photos,
  ordered, the first one being the cover. Copies, not references to
  `order_documents`, so deleting an order document can't break a published
  item. Each copy is re-encoded (long edge about 2400px, JPEG, plus a
  thumbnail) with **all EXIF metadata stripped**: phone photos carry GPS
  coordinates, and a piece photographed at a client's home would otherwise
  publish their address. Stored under `data/showcase/<company_id>/` with
  its own size cap, for the same shared-disk reason as `documents/`.
- **`ShowcasePublication`**: one row per item per channel (`website`
  today), with `status`, `external_id`, `last_synced_at`, `last_error`.
  Shaped so a social-media channel can be added later without changing
  `ShowcaseItem`.
- **`ShowcaseDismissal`** (`order_id`, `dismissed_at`, `dismissed_by`): an
  order the studio decided not to showcase. See the reminder below.
- **`ShowcaseExcludedOrderType`** (`order_type_id`): order types that are
  never showcased. Kept in the module rather than as a column on the root
  `order_types` table, so the module doesn't reach into host schema.
- **`ShowcaseKioskLink`**: a name ("Market iPad"), the token stored
  **hashed**, `created_at`, `last_used_at`, `revoked_at`. One per device,
  revocable one at a time.

**Host adapter** (`showcase_adapter.py`, same pattern as
`billing_adapter.py`): the only file that knows a showcase item can come
from an `Order`. It supplies the prefill (item name, order type), the
order's image documents and their bytes for the photo picker, the
company's order types for the exclusion setting, and the delivered orders
for the reminder.

### Settings → Showcase (one-time setup per studio)

- **Categories**: add, drag to reorder, hide (delete while unused).
- **Spec fields**: add, drag to reorder, hide (delete while unused).
  Starts **empty** (hard rule 16: a fresh tenant gets no dataset).
  *As built:* no rename for either, matching every other company list in
  the app — hide the old label and add the new one.
- **Order types never showcased**: a checkbox per order type.
  Sampling/NDA and Subcontract/white-label are the obvious ones to tick: an
  NDA forbids it, and a white-label piece's brand isn't the studio's to
  show.
- **Default visibility** for new items: public or in-person only,
  **public** unless the studio changes it (decided 2026-10-05). Excluded
  order types already keep NDA and white-label work out, so the default
  only governs ordinary commissions; a client who objects is handled per
  item by switching it to in-person only or private.
- **Website**: the receiving site's endpoint URL and a shared secret, with
  a "Send test" button and a status line (*Connected · last sync 2 min
  ago*, or the last error in red).
- **Catalog mode**: create a kiosk link (shown once, as a link plus a QR
  code to open it on the device), see each link's last use, revoke it.

### Day to day

**The reminder nags until dismissed.** An order is *awaiting a showcase
decision* when it is `delivered`, its type isn't excluded, and it has
neither a showcase item nor a dismissal. That state is **derived, never
stored** (hard rule 10). Each such order shows a purple badge on its order
page, and the nav's **Showcase** item carries a purple count of them
("worth knowing", hard rule 7, no new badge weight). The badge stays until
the studio either creates the item or presses **"Not showcasing this
one"**. The dismissal is reversible from the order's Showcase tab, in case
the client later agrees. *As built:* only orders delivered (pickup date,
else due date) on or after the day Showcase was switched on nag, or the
first day would flood the badge with every order ever delivered; older ones
can still be showcased from their tab. Until a piece exists, the tab asks
**"Showcase this piece"** / **"Not showcasing this one"** first, and the
form below appears once the prefilled draft exists.

**Order page → new "Showcase" tab** (fourth, after Details / Materials /
Billing; present only when the flag is on, the order is delivered and its
type isn't excluded). It's the item form, prefilled:

| Field | Prefilled from | The studio does |
|---|---|---|
| Photos | the order's image documents, as a grid | ticks the ones to use, drags to set cover and order, or uploads new ones here (finished-piece photos usually aren't in the order's documents yet) |
| Title | the order's item | edits it ("Weekender Bag — No. 24") |
| Category | the order type, when a category has the same name | picks from the list |
| Description | blank | writes two or three sentences |
| Specs | the fixed spec fields from Settings, empty | fills in the ones that apply, leaves the rest blank |
| Visibility | the default from Settings (public unless changed) | private / in-person only / public |

Below the form, **a live preview of the public card**: cover photo, title,
category, specs. It never shows a price or client information, so what the
studio sees is exactly what can go public.

Actions: **Save draft** (stays private), **Publish** (adds it to catalog
mode), **Share…** (published items only, see below), **Withdraw** (out of
the catalog, nothing deleted). None of them touches the website: that's
always a separate button (see "Nothing reaches the website without a
button" below). The tab shows the piece's website state (*On the website,
up to date* / *Changed since last sent* / …) and a **Send to website**
button when there's something to send.

**Showcase page** (new nav item): every item as a card grid, filterable by
category, status and visibility, with website changes waiting and
failures flagged, and the **Website** panel described below. **New item**
opens the same form with no prefill, for stock and market pieces. An item
made from an order links back to it. **Present** opens catalog mode on this
device for a signed-in user.

### Sending items to the website

*Respecified 2026-10-06* (replaces the first draft's `group_id`
`showcase-<id>` adoption): loose coupling, one direction, generic for any
studio, and the website's own content left alone.

**The four rules**

1. **One direction: atelier pushes, the website receives.** The website
   never writes to atelier, and atelier never reads the website after the
   one-off import. Push rather than pull, so the website keeps serving its
   own copies when this app is down. All atelier knows is an endpoint URL
   and a shared secret.
2. **The website marks what atelier manages.** Every card atelier pushes
   carries the piece's **key**, stored by the website in one column of its
   own (`external_key`). A push creates or updates the card with that key,
   and nothing else.
3. **A card without a key is the studio's own.** Old collections,
   anything added directly on the website, a studio's existing gallery the
   day it connects: never edited, never deleted, never read back into
   atelier. A studio that connects its website loses nothing it already
   had, and a website card that doesn't exist in atelier stays on the
   website only. That's the intended state, not drift.
4. **One small contract for every website.** Not bymonsieur-specific: our
   Flask sites get the receiver from `website_modules`; any other site only
   has to answer the same calls.

**Keys.** Each `ShowcaseItem` and each `ShowcasePhoto` gets a random
`public_key` (a UUID, not the integer id: ids are sequential and shared
across tenants). It never changes, so the website can follow a piece
through any edit, and a photo through any reorder.

**The contract.** JSON over HTTPS, one endpoint, a format version in every
body, signed with an HMAC of the body using the shared secret, plus a
timestamp so replays are rejected:

- **`upsert`**: the piece's key, title, description, category **name**,
  spec label/value pairs (blank ones omitted), and its photos in order, each
  with its key and a content hash. The receiver answers with the photo keys
  it doesn't hold yet (or whose hash changed), and atelier sends just those
  photos in a second signed request. *Changed 2026-10-07* from short-lived
  download links: the website never has to reach atelier (demo's atelier
  sits behind the demo slot's basic auth), and unchanged photos are never
  re-sent.
- **`remove`**: the piece's key. Sent when a piece is withdrawn, deleted,
  or moved away from public.
- **`adopt`**: see "The one-time import, linked" below.
- **`ping`**: for Settings' "Send test" button.

The body is built by **one serializer that can only emit those fields**. A
test asserts that no price, client, order number, delivery date or note
can appear in it, whatever the piece holds. That's the privacy guarantee,
so it's tested rather than left to convention.

**Nothing reaches the website without a button** (decided 2026-10-07).
Every call to the website is a deliberate press by the studio, so nothing
on the site ever changes as a side effect of saving, publishing,
withdrawing or switching Showcase off. Atelier itself (the piece, catalog
mode) still changes at once, since that's the studio's own.

- **Website state per piece, derived** (hard rule 10) by comparing the
  piece with what was last sent (`ShowcasePublication` keeps a hash of the
  last body it sent, not a status):
  *Not on the website* (public, never sent) · *On the website, up to date* ·
  *Changed since last sent* · *To be taken off* (sent, but now withdrawn,
  deleted, or no longer public) · *Removed on the website* (the studio
  deleted the card there) · *Not linked* (an imported piece, see below) ·
  *Last send failed*. Drafts, private and in-person pieces that were never
  sent have no website state at all.
- **Per piece:** the editor shows the state and a **Send to website**
  button (or **Take off the website**) when there's something to do.
- **All at once:** the Showcase page's **Website** panel says what's
  waiting ("2 to add, 3 to update, 1 to take off") and opens a **review
  page** listing each piece and exactly what will happen to its card, with
  a tick box per line (all ticked) and one **Send to website** button. The
  result is reported per line on the same page.
- **No nagging.** Changes waiting are shown on the Showcase page only: no
  nav badge, no reminder. A piece left unsent is a choice.
- A failure leaves the piece *Last send failed*, with the error; pressing
  the button again resends. Every call is idempotent (an upsert by key, a
  remove of a key the site doesn't hold is a success), so resending is
  always safe. Calls go out while the page waits, no background queue.
- **Connecting sends nothing.** Settings' **Send test** only pings. Pieces
  already published as public then simply show *Not on the website* until
  sent.

**When they disagree.**
- A website-only card: stays website-only (rule 3).
- A managed card deleted in the website's own admin: the receiver keeps
  the key as deleted, and answers the next upsert with *gone* instead of
  re-creating it. Atelier learns it at the next send, marks the piece
  "Removed on the website" and offers **Send again**, which re-creates it
  on purpose.
- A managed card's content edited in the website's admin: not possible,
  see below.

**Receiver: `showcase_receiver.py` in `website_modules`**, synced into each
client site by `sync.py` like `contact_mail.py`. It checks the signature
and timestamp, downloads the photos, keeps the deleted keys, and calls a
**per-site mapping** that owns the site's own models. For bymonsieur, the
mapping writes `GalleryImage` rows in category `commissions` (a card being
the rows that share a `group_id`), stores the piece key on them and each
photo's key on its row, and matches the category to `ProductCategory` by
name; it creates none, so an unmatched name lands uncategorised.

**Who owns what on the website:** atelier owns a managed card's content
(photos, title, category). The site's admin owns its **position and whether
it shows**, so Joe can still reorder cards in bymonsieur's own dashboard.
That dashboard marks managed cards "Managed in Atelier" and doesn't offer to
edit their content, since the next push would overwrite it. It can still
delete one (handled above).

### Every case, at a glance

Agreed 2026-10-07. "Managed card": a website card atelier created or
linked (it carries atelier's key). Nothing in the Website column happens
until the studio presses **Send to website** (or **Take off the
website**).

**Made in atelier**

| What the studio does | Atelier and catalog mode | Website, once sent |
|---|---|---|
| Saves a draft | Atelier only | Nothing |
| Publishes as private | Atelier only, not in the catalog | Nothing |
| Publishes as in person | Atelier and catalog mode | Nothing, ever |
| Publishes as public | Atelier and catalog mode; *Not on the website* | A new managed card, at the top |
| Edits a piece that's on the website | Updated at once; *Changed since last sent* | The card is updated |
| Public → in person or private | As chosen; *To be taken off* | The card is removed |
| Withdraws | Out of the catalog, kept; *To be taken off* | The card is removed |
| Publishes again after it was taken off | Back in the catalog; *Not on the website* | A new card, at the top (the old position is gone) |
| Deletes (only once withdrawn) | Gone; *To be taken off* if the card's still up | The card is removed |
| A send fails | *Last send failed*, with the error | Keeps what it had |
| Showcase is switched off for the company | Hidden, nothing deleted | Untouched; nothing can be sent |

**Made on the website**

| What the studio does there | Atelier | Website |
|---|---|---|
| Adds a card | Never sees it; not in catalog mode | The site's own card |
| Edits, reorders, hides or deletes its own card | Nothing | As it likes |
| Wants a site-only card in atelier too | Makes the piece in atelier, sends it | Two cards until it deletes the original (no pull) |

**A managed card, touched on the website**

| What the studio does there | Result |
|---|---|
| Reorders or hides it | Allowed and kept: sends never change position or hidden |
| Edits its photos or caption | Not offered ("Managed in Atelier") |
| Deletes it | Allowed; the next send for it comes back *Removed on the website*, and **Send again** re-creates it on purpose |

**Joe's imported pieces** (the one-off, for bymonsieur only)

| Situation | Result |
|---|---|
| Imported, not linked | *Not linked*; nothing is ever sent for them |
| Linked | Managed cards, *up to date*; from then on as "Made in atelier" |
| Card not found while linking | Stays *Not linked*: **Send as a new card**, or leave it atelier-only |
| One he no longer wants online | Withdraw or switch to in person, then **Take off the website** |

### The one-time import, linked

`scripts/import_showcase_from_site.py` (SC34–SC38, built 2026-10-06) copied
bymonsieur's existing Recent Commissions into Showcase so catalog mode
wasn't empty on day one. It's **the only thing that ever goes from website
to atelier**, and it's a one-off: once a company has a website connection
in Settings, the script refuses to run, so nobody re-runs it later and
pulls website-only cards in. A studio that doesn't want its old cards in
atelier simply doesn't run it.

**The imported cards end up exactly as if atelier had pushed them.** After
linking, an imported piece's card is a managed card like any other: it
carries the piece's key, its photos carry their photo keys, the dashboard
shows "Managed in Atelier", and the next edit in atelier updates it. No
duplicate card, no re-upload, no second lossy re-encode of photos the
website already holds.

How:
- The import records where each photo came from: `ShowcaseItem.source_ref`
  ("<host><first photo's path>") and `ShowcasePhoto.source_ref` (the
  photo's own path on the site). *Both built* (SC34, SC39; the photo one
  2026-10-06, before prod's import).
- Linking sends one **`adopt`** per imported piece: the piece's key, and
  each photo's key with its `source_ref`. The receiver finds the website's
  row for each photo by its file, stamps the piece key on the whole card
  and each photo key on its row, and answers with what it found. It
  changes no content, so the card looks the same the moment after.
- Linking sends **no content**: an adopted piece is recorded as sent in
  its current state, so it shows *On the website, up to date*, and only a
  later edit plus **Send to website** changes its card. Its photos are
  never uploaded again: the website already holds every photo key.
- Linking is a button too: Settings → Website, **Link imported pieces**,
  after the connection is set up, with the same review page first. A piece
  whose card can't be found (Joe deleted it, or its photos changed) is
  listed and stays *Not linked*. Nothing is ever sent for a *Not linked*
  piece (sending it would duplicate its card) until the studio chooses
  **Send as a new card** on it.
- Pieces imported before `ShowcasePhoto.source_ref` existed (demo's) can
  only be matched by their first photo. Simplest: `--remove --apply` and
  import again before linking.

### Sharing to social media (by hand)

**Share…** copies the caption (title, description, specs) to the
clipboard, then opens the device's share sheet with the item's photos (the
Web Share API with files). The studio picks Instagram, Facebook or anything
else and pastes the caption. The clipboard step is there because Instagram
drops text passed through the share sheet. Where the browser can't share
files (most desktop browsers), the button falls back to **downloading the
photos and copying the caption**. Nothing posts by itself: publishing
stays a person's decision. Each share is logged on the item ("Shared
Oct 5").

### Catalog mode

**Any modern browser on any screen** (tablet, touch laptop, desktop on a
big monitor, phone), not an iPad app. The layout is responsive, and every
control works by touch, mouse **and** keyboard (arrows to move, Esc to back
out, Space to pause the slideshow). Device-specific features are
progressive enhancements that degrade silently where unsupported.

- **Entry**: a kiosk link (`/k/<token>`), which shows **only** the
  showcase, read-only, and nothing else of the app, so a visitor handed the
  tablet is not one tap from the invoices. Signed-in users can also open it
  from the Showcase page.
- **Gallery**: large tiles, category chips (the same categories as the
  website), tap a tile for a full-screen swipeable view with title,
  description and specs (no prices in this version, see below). Includes
  `in_person` items, which the website never receives; never includes
  `private` items or drafts.
- **Cinematic mode**: started from a button or after a few idle minutes.
  Random order, slow pan-and-zoom with crossfades, a small caption (title,
  category) and the studio's logo. Any touch, click or key drops into the
  gallery **on the piece that was showing**, so a visitor can say "tell me
  about this one". Honours `prefers-reduced-motion` with plain crossfades.
- **Offline**: a service worker caches the items and photos, so it keeps
  working on bad market wifi, and refreshes when back online. Plain JS, no
  build step (hard rule 2).
- **Stays on and full-screen**: Screen Wake Lock where supported, the
  Fullscreen API where supported, and a web manifest so "Add to Home
  Screen" opens it without browser chrome. On an iPad, Guided Access locks
  the device to it; worth a line in the in-app help.

### Not now (deliberately)

- **AI-drafted descriptions** from the order's notes. The `ai/` module
  could do it later; the description is plain text until then.
- **Posting directly to Instagram/Facebook** through Meta's API. It needs
  a Meta developer app, App Review and business verification before it
  works for anyone but test accounts, and Instagram only takes JPEGs at a
  public URL. `ShowcasePublication` is already shaped for it as another
  channel. Revisit when a second studio is on the platform.
- **Prices**, anywhere: not on items, not in catalog mode (decided
  2026-10-05, for a future version). In-stock pieces at a market are where
  they'd matter first. When it comes back, the price must stay out of the
  website serializer (and its privacy test must keep proving that), and it
  should be shown in catalog mode only behind a Settings switch. Whether
  only items with no order may carry a price is the question to settle
  then.
- **Receivers for websites that aren't one of our Flask sites.** The
  contract is generic (any site that answers `upsert`/`remove`/`ping` can
  connect), but the only receiver we build is the `website_modules` one.
  A Squarespace or Wix site would need a public JSON feed or an embeddable
  widget instead.

### Open question

**Spec fields per category?** A wallet has no strap length, a belt no
capacity. The proposed list is company-wide and blank fields just don't
show, which may be enough. Limiting a field to some categories is a cheap
addition (a field-to-category link table, no change to stored values) if
the form starts feeling cluttered, so building the company-wide version
first loses nothing. Undecided as of 2026-10-05.

### Build order

1. ~~The per-company feature mechanism and the `/admin` toggle.~~
   *Done* — `features/` (FE1–FE10) and the company page's Features section
   (admin `PA8a`). The `showcase` key exists in the catalog; switching it
   on has no visible effect until step 2 lands.
2. ~~Module skeleton: data, Settings → Showcase, the item form (order tab +
   Showcase page), photo copying with EXIF stripping, the nagging reminder.~~
   *Done* — `showcase/` (SC1–SC24), `showcase_adapter.py`, and the order
   guide's after-delivery step. Beyond the plan: a star button to make a
   photo the cover without dragging (drag does nothing on a touch screen),
   and the main nav now wraps instead of overflowing. Not yet: the
   `sample_data.py` demo lists, and `ShowcasePublication`, which arrives
   with step 4.
3. ~~Catalog mode and kiosk links: useful immediately, touches no website.~~
   *Done* — `showcase/kiosk.py`, `/showcase/present`, Settings → Showcase's
   Catalog mode section (SC25–SC33). As specced, plus: a deactivated
   company's links stop too; links show when they were last opened; the
   QR code is drawn server-side (`qrcode`, a new pure-Python dependency).
   The catalog page's script and service worker are checked by hand, not
   by the test suite.
4. ~~The website sync, as respecified 2026-10-06 and 2026-10-07 ("Sending
   items to the website", "The one-time import, linked", "Every case, at a
   glance" above).~~ *Done* 2026-10-07 — `showcase/website.py` (SC40–SC49),
   the review page, Settings → Showcase → Website; the protocol is
   website_modules' `showcase_protocol.py` (SP), synced into atelier as
   `showcase/protocol.py` and into bymonsieur, whose mapping is
   `showcase_site.py` (bymonsieur SH). Differences from the plan: the shared
   module is the *protocol* (signing, body, `handle()`, `send()`) rather
   than a whole receiver, because a receiver is mostly the site's own
   models; photos travel inside the call rather than as download links;
   bymonsieur's managed cards stay movable among the cards, but there's no
   per-card "hidden" flag on that site, so "whether it shows" isn't
   offered; and deleting a piece whose card is on the website is refused
   until it's taken off (SC46). Checked end to end against a local
   bymonsieur over HTTP. Not done: `SHOWCASE_ENCRYPTION_KEY` and
   `SHOWCASE_SECRET` set on the servers, and linking Joe's real import.
5. The Share button.

Each step ships with its tests and its `REQUIREMENTS.md` rules, and the
order lifecycle help page (`templates/help/order_lifecycle.html`) gets the
"after delivery" step when step 2 lands (`OR1i`).

