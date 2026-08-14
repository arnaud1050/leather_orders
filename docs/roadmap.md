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
  announcement banner (`/admin/settings`), shown on every page including
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

