# Billing module — business requirements & rules

This is the living spec for `billing/`: every rule the module is supposed to
enforce, written as a checkable statement rather than prose. It exists so
requirements don't only live in code (or in someone's head), and so test
coverage can be checked against something. When behavior changes on purpose,
update the rule here in the same commit — if a rule and the code disagree,
one of them is a bug.

Each rule has an id (e.g. `F2`) referenced from the **Test coverage map** at
the bottom of this file. `billing/CLAUDE.md` explains the
*why* behind these choices; this file is the checklist of *what must hold
true*.

This module handles money that has already gone out to a customer. Two
rules dominate everything below, and most of the rest exists to serve them:

> **Once an invoice leaves draft, nothing that happens afterwards may change
> what it says.**
>
> **A number that has been issued is never reused and never rewritten.**

**Not tax advice.** The rates in §2 were verified against the CRA on
2026-07-30 and are pinned by tests, but rates change — re-confirm before an
accounting period closes.

## 0. Scope & module boundary

- **B1 — One import from outside.** No file under `billing/` may import
  anything from the host project except `from models import db` (the shared
  SQLAlchemy handle). Importing `Order`, `Client`, `Company` or `Payment`
  is forbidden, however convenient.
- **B2 — Never the adapter either.** `billing_adapter.py` lives at the
  project root, not inside the module, precisely because it knows a
  "subject" is an `Order`. Nothing under `billing/` may import it.
- **B3 — The tax engine imports nothing but the standard library.**
  `billing/tax.py` is plain data plus pure functions, liftable into a
  script or a notebook without dragging an ORM behind it. Anything there
  that needs a database belongs in `billing/services/`.
- **B4 — The document dataclasses touch no database.**
  `billing/documents.py` is the vocabulary the host and the module speak
  in; it must stay free of persistence.
- **B5 — One place names a host table.** `config.SUBJECT_FK` is the only
  string in the module referring to the host's schema. Porting to a project
  that invoices jobs or subscriptions is that one line.
- **B6 — `billing.services` is the public surface.** The host talks to
  `billing.services`, `billing.tax` and `billing.documents`, and nothing
  deeper. Reaching into `billing.models` from a host route works today and
  is exactly the coupling this layout exists to prevent.
- **B7 — Billing never sees the subject.** It receives a `Billable`
  dataclass (lines, buyer details, tax province, payments) built by the
  host's adapter. `Invoice.subject_id` is an opaque host id.
- **B8 — The host owns the tenant's *name*.** A company is called the same
  thing whether or not it invoices, so the name lives on the host and is
  passed in; everything else on the letterhead belongs to this module
  (§3).

## 1. What this module owns

- **W1.** Three tables: `billing_profiles` (the seller's letterhead, one
  row per tenant), `invoices`, `invoice_tax_lines`. No files: the logo is
  the studio's (brand/), and billing only embeds what it's handed (§14).
- **W2.** Invoice numbering (§4), issuing and freezing (§5), tax
  calculation (§2–§3), and the derived money on a document (§6).
- **W3.** An optional blueprint (`/invoices`, `/invoices/<id>`,
  `/invoices/<id>/pdf`, `/invoices/preview.pdf`,
  `POST /subjects/<id>/invoice`, `POST /invoices/<id>/status`) the host opts
  into via `routes.register()`. A host wanting its own UI ignores it and
  drives the services directly.
- **W4.** `register()` takes four host-supplied hooks it cannot know:
  `resolve_billable`, `uninvoiced`, `display_name`, `back_label`. Only
  `resolve_billable` is required; the rest degrade to empty list / empty
  name / the literal "Back".

## 2. Tax rates (`billing/tax.py`)

- **R1.** Every Canadian province and territory has an entry in
  `PROVINCE_TAXES` — all thirteen, none missing.
- **R2.** GST is 5% and applies in AB, BC, MB, NT, NU, QC, SK, YT.
- **R3.** Provincial rates: BC PST 7%, SK PST 6%, MB RST 7%, QC QST 9.975%.
- **R4.** HST replaces GST rather than stacking on it — an HST province
  charges exactly **one** tax line: ON 13%, NB/NL/PE 15%, **NS 14%**.
- **R5.** Nova Scotia is 14%, reduced from 15% on 2025-04-01. Check this
  one first if the table ever looks stale.
- **R6.** HST is gated on `gst_number`, because it's collected under the
  federal GST/HST registration.
- **R7.** Manitoba's tax is labelled **RST**, not PST — it prints under the
  name the province actually uses.
- **R8.** BC PST, SK PST and MB RST all read the **same** `pst_number`
  field: a seller is realistically registered in at most one. Quebec's QST
  is gated separately on `qst_number`.
- **R8a.** A tax the buyer's province levies but the seller can't charge for
  want of a registration is reported by `unregistered_taxes` (e.g. `PST` for a
  BC buyer when only GST is held), even though `status_for` says `ok` because
  *some* tax was charged. The order page and a draft invoice warn about it;
  an issued invoice doesn't (it's frozen). Amounts are unaffected. Tests:
  `test_unregistered_taxes_names_the_skipped_tax`,
  `test_a_bc_order_without_a_pst_number_warns_that_pst_is_skipped`.
- **R9.** The rate table must be corrected **from the CRA**, never from the
  test — and the test corrected from the CRA too. A test that imported the
  constant it checks would pass no matter what the constant said.
- **R10 — `normalize_province` resolves a written province to a code, or
  returns `None`.** "QC", "quebec", "Québec", "P.E.I." and every name in
  `PROVINCES` resolve; the lookup is **built from `PROVINCES`**, so a code and
  its name can't drift apart from the rate table.
- **R11 — Unrecognised input is dropped, never guessed**, and matching is
  **exact** against the folded table — no prefix, no fuzzy matching. Storing
  raw text puts something in the province column that matches no row in
  `PROVINCE_TAXES`, so nothing is charged and nothing on screen looks wrong —
  and F1 freezes that onto an issued invoice. (The column is declared
  `VARCHAR(2)`; SQLite ignores declared lengths and keeps the whole string,
  while Postgres/MySQL would truncate or reject. Wrong either way, so the
  rule does not depend on which.) Returning `None` routes to the same honest
  "no province" path as a blank field.
  Fuzzy matching is what would let "Nova Scotia office" or "not in canada"
  land somewhere real. Callers: the client edit form, and
  `communications/`'s contact-form field mapping (`F-20`, `F-21`), which
  reaches it re-exported from the host's `models.py` rather than importing
  `billing` module-to-module.

## 3. What actually gets charged

- **C1 — Destination-based.** Tax follows the **buyer's** province
  (`Billable.tax_province`), never the seller's — **except** when the goods
  are collected in person (C10). `place_of_supply()` is the one function
  that decides the province; live amounts and `freeze()` both go through it.
- **C2 — Registration-gated.** A tax is charged only when the seller holds
  the matching registration. A studio under the small-supplier threshold
  has no `gst_number` and charges no GST; one that never registered in BC
  charges no BC PST. This falls out of the data rather than needing a
  separate "do we charge tax" switch.
- **C3 — Prices are tax-exclusive.** A line's `unit_price` is pre-tax; tax
  is added on top. Total = subtotal + tax, where the subtotal is already
  net of any discount (DS4).
- **C4 — Charge nothing rather than guess.** A blank or unrecognised
  province yields no tax lines.
- **C5 — Say why nothing was charged.** `status_for()` returns `ok`,
  `outside_canada` (C11 — correct, shown as a note, not a warning),
  `no_seller_province` (C10 with no seller province on file),
  `no_buyer_province`, `unknown_province` or `not_registered`, so a host can
  surface the reason instead of silently billing zero.
- **C6 — Round per tax line, to the cent**, so a document's total matches
  the sum of its own printed lines.
- **C7 — Never compound.** Each tax is computed on the pre-tax subtotal,
  never on another tax.
- **C8 — Rates print without trailing zeros** (`5%`, `9.975%`).
- **C9 — Tax applies to the whole subtotal.** There is no per-line taxable
  flag (see Z2).
- **C10 — Collected in person is taxed at the seller.** A `Billable` with
  `picked_up` is taxed in the **seller's** province (`IssuerDetails.province`,
  from the letterhead), whatever the buyer's province — and even when the
  buyer is outside Canada. No seller province on file charges nothing and
  reports `no_seller_province`.
- **C11 — Exports owe nothing.** A `Billable` with `outside_canada` and not
  `picked_up` is charged no tax and reports `outside_canada`.
- **C12 — PST for another province is flagged, not refused.** One
  `pst_number` gates BC PST, SK PST and MB RST (R8), so a seller registered
  in one province charges all three. `taxes_elsewhere()` names any PST/RST
  charged for a province other than the seller's; the order page and a
  draft invoice warn about it. Silent when the seller's province isn't on
  file. Amounts are unaffected — selling into another province can require
  registering there, so the charge may well be right.

## 3a. Discounts (`Discount`, `clean_discount`)

One discount per subject, on the whole of it — not per line.

- **DS1 — Percentage or fixed amount, or nothing.** `Billable.discount` is a
  `Discount` of kind `percent` (value 10 = 10%) or `amount` (dollars), with
  an optional label. `clean_discount()` turns anything unusable — an
  unknown kind; a missing, zero, negative or non-finite value — into
  `None`, meaning no discount, rather than raising. A percentage is capped
  at 100; a label is whitespace-collapsed and cut to 60 characters, and a
  blank one is `None`.
- **DS2 — The amount is rounded to the cent and never exceeds the lines.**
  `amount_on()` rounds like a tax line (C6), so the printed rows add up, and
  clamps a fixed amount larger than the lines to their total — a discount
  never makes a subject worth less than nothing.
- **DS3 — How the line reads.** The label, or "Discount" without one, with
  the rate in brackets for a percentage: "Returning client (10%)",
  "Discount (12.5%)". A fixed amount prints its label alone.
- **DS4 — It comes off before tax.** `Billable.subtotal` is the lines less
  the discount, and that net is what `taxes_for` is given: in Canada a
  discount given at the time of sale lowers the amount GST/HST/QST/PST is
  charged on. `items_total` is the lines at full price. Everything
  downstream of `subtotal` and `total` — balance due, paid-ness, the host's
  revenue figures — is therefore net of the discount. An early-payment
  discount, which is taxed on the undiscounted price, is **not** this and
  isn't supported.
- **DS5 — Frozen at issue.** `freeze()` writes `issued_discount` (dollars)
  and `issued_discount_description` (the DS3 text) alongside
  `issued_subtotal`, which is already net. An issued invoice ignores later
  changes to the subject's discount; a draft follows them (F1). An invoice
  frozen before discounts existed has a null `issued_discount` and reads as
  none — which is what it was.
- **DS6 — What prints.** The invoice page and both PDF layouts show
  "Subtotal" as `items_total`, then — only when the discount isn't zero — a
  row with the DS3 text and the amount as a negative, then the tax lines on
  the net. No discount, no row.

## 4. The seller's letterhead (`BillingProfile`)

- **P1.** Exactly one profile per tenant (`company_id` is unique).
- **P2.** `profile_for(company_id)` **creates one empty on first use**, so a
  host never has to special-case "not set up yet" — an empty profile simply
  prints no letterhead.
- **P3.** `display_name` is a **real column**, not something set on the
  object by whichever call happened to pass a name. Any other path (a raw
  query inside a migration, say) must read the stored value.
- **P4.** A bare `profile_for(company_id)` with no name supplied must
  **not** blank a stored name.
- **P5.** `update_profile()` ignores unknown keys rather than raising, so a
  host form can post whatever it renders. The editable set is exactly:
  `invoice_prefix`, `street`, `city`, `province`, `postal_code`,
  `gst_number`, `pst_number`, `qst_number`, `neq`, `payment_instructions`,
  `invoice_template`, `primary_color`.
- **P6.** `invoice_prefix` falls back to `"INV"` whenever it would
  otherwise be empty.
- **P7.** `has_letterhead` is true once **anything beyond the name** is set.
  It exists to stop callers stamping a snapshot that records no fact (M9).
- **P8.** Editing the letterhead **never** touches invoices already issued —
  those carry their own frozen copy (§5).
- **P9.** Registrations print in a fixed order — **GST/HST → PST/RST → QST →
  NEQ** — tax accounts first, NEQ last since it identifies the enterprise
  rather than a tax account. Unset ones are omitted entirely.
- **P10.** Validation of what a *person* may type (province must be a real
  code, prefix uppercased and capped at 10 characters, layout must be a
  known one, the colour must be `#rrggbb`) is the **host's** job, in its
  settings form. The module stores what it's given — and, for the look,
  re-checks it on the way out (BR3), because those values reach a
  stylesheet.
- **P11.** The three appearance columns are **nullable, and null means "not
  chosen"**, resolved to the defaults in `config` at read time — so a
  default can change without a migration.

## 5. Invoice numbering

- **N1.** Numbers are `PREFIX-YEAR-0001` — the tenant's prefix, the issue
  year, a four-digit zero-padded sequence.
- **N2.** The sequence increments per invoice.
- **N3.** It restarts each calendar year.
- **N4.** It is per tenant: two companies may hold the same number, and
  neither's sequence affects the other.
- **N5.** The next number is derived from the **highest existing number**,
  not from a count. Deleting an older invoice therefore leaves a gap rather
  than causing a reuse.
- **N6.** **Voiding never frees a number** — the row stays.
- **N7.** Zero-padding keeps string order equal to numeric order past nine.
- **N8.** A unique constraint on `(company_id, number)` is the real guard:
  two simultaneous requests collide there rather than silently issuing the
  same number twice.
- **N9.** One invoice per subject (`subject_id` is unique).
  `create_invoice()` returns the **existing** invoice when the subject
  already has one, so a double-submitted button cannot burn a second number.
- **N10.** The prefix used is whatever the profile holds at creation time.
  Changing the prefix later starts a fresh sequence and does **not**
  renumber anything already issued.
- **N11 — Known limit.** Deleting the *most recent* invoice **does** hand
  its number back, since there is then no higher number to read. Harmless
  for a draft nobody has seen; fix it (a per-tenant high-water mark) before
  exposing invoice deletion in any UI. This is a documented limit, not a
  bug to be silently "fixed" by changing N5.
- **N12.** A newly created invoice is a **draft** and is **not frozen**.

## 6. Issuing & freezing

- **F1 — A draft tracks everything live.** Nobody has seen it, so fixing a
  typo in the GST number, editing the subject's line items, or correcting
  the buyer's province all reach a draft.
- **F2 — Freeze on the transition only.** `set_status()` compares the
  status before and after and freezes only on draft → not-draft. Re-saving
  an already-issued invoice must not re-stamp it with today's settings —
  that would rewrite history, which is the whole thing the snapshot
  prevents.
- **F3 — Freezing writes three things**: the issuer snapshot
  (`issuer_name`/`address`/registrations/`payment_instructions`),
  `issued_subtotal` with the discount beside it (DS5), and one
  `InvoiceTaxLine` row per tax charged.
- **F4.** An issued invoice ignores later changes to the **seller's**
  details.
- **F5.** An issued invoice ignores later changes to the subject's **line
  items**.
- **F6.** An issued invoice ignores the **buyer moving province**; an
  uninvoiced subject does follow it.
- **F7.** Issuing with no taxable province stores **no** tax rows —
  correctly recording that the client was billed no tax.
- **F8.** **Void freezes too.** Voiding a draft is still a transition out of
  draft, so the document is snapshotted; a voided invoice must still print
  what it said.
- **F9.** Saving a draft as a draft (changing only notes or due date)
  leaves it unfrozen.
- **F10.** An unrecognised status is **ignored**, not stored — the stored
  status stays what it was, and no freeze is triggered.
- **F11.** `is_frozen` is exactly `issued_subtotal is not None`. That single
  marker is what tells `amounts_for()` which figures to use.
- **F12.** An invoice issued **before freezing existed** (status past draft,
  `issued_subtotal` null) reports its live subtotal with **zero tax** —
  which is what its client actually received. Inventing tax retroactively
  would change an amount already billed.
- **F13.** A snapshot with a **blank** `issuer_name` counts as *never
  frozen*, not as "frozen with no seller". A document that prints no seller
  at all is useless, so falling back to live details beats honouring a
  snapshot that can only have come from a bug.
- **F14.** `document_for()` reads the **live** profile when the invoice is a
  draft or has no usable snapshot, and the **frozen** copy otherwise.
- **F15 — Back to draft is how a sent invoice is corrected.** Setting a
  sent invoice's status back to Draft makes it live again (F1): it follows
  current lines, settings and province. Sending it again re-freezes it with
  those figures (F2's draft → not-draft transition), **under the same
  number**. The client already received the old version, so whoever does
  this sends them the corrected one.

## 7. Derived money & status

Everything in this section is computed on read and never stored — the
reason being that a stored copy can disagree with the rows it describes.

- **D1 — Paid-ness is derived.** `display_status` returns `"paid"` once
  recorded payments cover the total, without anyone setting it.
- **D2.** A deposit does **not** make an invoice paid; neither does paying
  exactly the pre-tax subtotal.
- **D3.** **Void wins over paid** — a voided invoice reports `void` however
  much money is against it.
- **D4.** Settlement is float-tolerant (`balance_due < 0.005`): a cent of
  rounding must not leave a document looking permanently unpaid.
- **D5.** A zero-value invoice is **not** reported as paid — otherwise
  every empty draft would claim to be settled.
- **D6.** `"paid"` is **not settable by hand.** It is absent from
  `SETTABLE_STATUSES` and from the status dropdown, though present in
  `STATUS_LABELS` for display.
- **D7.** `total`, `balance_due` and every list total are **tax-inclusive**.
- **D8.** The host's own derived figures built on `total` (lifetime value,
  timeline sort, analytics) are tax-inclusive as a consequence.
- **D9.** An order with no tax charged still totals its subtotal — no tax is
  not the same as no money.
- **D10.** `is_outstanding` = issued, not void, and still owed money. That
  is what an "outstanding" figure sums; work that has never been invoiced is
  not money anyone owes yet.
- **D11.** `tax_collected()` sums the **frozen** `InvoiceTaxLine` rows per
  label, excludes voided invoices, is scoped per tenant, and accepts an
  optional `since`/`until` window. This is why the tax lines are a real
  table and not a JSON blob — a GST/QST remittance is a `SUM ... GROUP BY`.
- **D12.** `invoiced_subject_ids()` answers "what haven't I invoiced yet?"
  for the host, without billing needing to know what a subject is.

## 8. Addresses

- **AD1.** An address prints as street, then `City, PROV␣␣Postal` — two
  spaces before the postal code, per Canada Post.
- **AD2.** Any subset renders sensibly: street alone, city alone, city +
  province, province + postal with no city.
- **AD3.** Nothing filled in returns `None`, so callers skip the block
  rather than printing an empty line.
- **AD4.** The seller's and the buyer's addresses render through the **same**
  function — a document must not format one party differently from the
  other.
- **AD5.** The structured columns exist so the province can be a validated
  dropdown, but only the **rendered string** is frozen onto an invoice
  (`issuer_address`, one column). A snapshot only has to reproduce what was
  printed.
- **AD6.** The free-text → structured migration is **best effort**: a
  trailing `City, PROV  Postal` line is split out properly (including
  postal-code spacing variants and multi-line streets); anything else lands
  **whole** in `street` and reads visibly wrong until re-entered.
- **AD7 — Never guess a province.** An unparseable address must not have a
  province inferred for it. Filing a buyer under the wrong province silently
  changes the tax they're charged, which is worse than an address that
  visibly needs re-typing.
- **AD8.** The legacy free-text column is dropped once migrated, and the
  migration is a no-op on a row that already has structured parts.

## 9. Migrations (`billing/migrations.py`)

- **M1.** This module's schema changes live **here**, not in the host's
  `run_migrations()` — putting billing columns in the app's list would mean
  the root model file has to know what this module stores.
- **M2.** Every step is a **no-op once applied**, so it is safe on every
  boot and on a fresh database. New *tables* need no entry (`create_all()`
  covers them); a column added to a table that already shipped goes in
  `ADDED_COLUMNS`.
- **M3.** `invoices.order_id` is renamed to `subject_id` — the column was
  named for the host's table back when invoicing lived in the app.
- **M4.** The letterhead is copied off the host's `companies` table into
  `billing_profiles` and then **dropped** there. Leaving two copies means
  the next person to edit one wonders why the invoice didn't change.
- **M5.** The move must **not overwrite** a profile field someone has
  already filled in.
- **M6.** A pre-split free-text `companies.address` is rescued **whole**
  into `street` (newlines flattened to commas) — same never-guess rule as
  AD7.
- **M7.** Profiles missing a `display_name` are backfilled from the host's
  company name (P3's column arrived after profiles already existed).
- **M8.** A snapshot that lost its seller name keeps its address and
  registrations — those are genuine — and has the **name restored** rather
  than being discarded.
- **M9.** The backfill **never freezes an empty letterhead.** Stamping
  nothing records no fact and permanently blocks the invoice from ever
  showing one.
- **M10.** A snapshot that captured *only* a name is **un-frozen** (its
  `issuer_name` cleared, returning it to live details) — but **only when the
  seller now has a letterhead**. That combination is the signature of the
  earlier buggy backfill. A snapshot with real content is never touched.
- **M11.** The backfill freezes `issued_subtotal` but writes **no tax rows**
  — see F12.
- **M12.** The four repair steps run in an order that **converges in one
  pass**: name the profiles, restore nameless snapshots, clear contentless
  ones, then re-freeze properly. A row the repair just cleared is re-stamped
  immediately, not on the next boot.
- **M13.** The subtotal resolver the backfill needs is **optional** — a host
  with no legacy invoices never installs one, and the money half is then
  skipped.

## 10. Tenant isolation & auth

- **A1.** Every route in the blueprint requires an authenticated session —
  both the two `GET` pages and the two `POST` actions.
- **A2.** `get_invoice()` filters by `company_id`; reaching another tenant's
  invoice by id **404s**.
- **A3.** `list_invoices()` / `documents_for()` return only the given
  tenant's rows.
- **A4.** Creating an invoice for another tenant's subject 404s — the host's
  resolver raises `LookupError`, which is the second line of defence behind
  the host's own route guard.
- **A5.** Profiles are per tenant and never shared or fallen back to.
- **A6.** `tax_collected()` and `invoiced_subject_ids()` are scoped the same
  way. Every public service function takes `company_id` **first**.

## 11. UI behavior

- **U1.** Everything inside `.invoice-doc` is the printable document; the
  controls below it are `.no-print`, so the print fallback (U2) produces the
  document alone.
- **U2.** The invoice page's export button is **"Download PDF"**, linking to
  the server-rendered PDF (§12). `window.print()` survives only as the
  fallback where the renderer can't load (PD2), under its old label
  "Print / save as PDF" — the page shows exactly one of the two.
- **U3.** The "no tax on this invoice" warning appears **only on a draft**
  and only when `tax_status != "ok"`, wording the specific reason (C5), and
  says the figures freeze the moment it's marked sent. Past draft the
  warning is gone, because the number is settled.
- **U4.** The status dropdown offers only `SETTABLE_STATUSES` — "paid" is
  never selectable (D6), and the page says so in as many words.
- **U5.** Payment instructions print under "Payment instructions" **only** when money
  is still owed and the invoice isn't void.
- **U6.** The buyer's name and the subject link are rendered from
  `doc.payer.url` / `doc.subject_url`; a host that supplies neither gets
  plain text, not a broken link. Both carry `return_to`.
- **U7.** The invoice page shows the tax breakdown as one line per tax, with
  its label and rate.
- **U8.** The invoice list shows every invoice, an **Outstanding** total
  (D10), and a "not invoiced yet" list of subjects with no invoice — that
  second list being the actual to-do the page exists for.
- **U9.** Addresses render with `white-space: pre-line` rather than
  converting newlines to `<br>`, which keeps the line breaks without needing
  `|safe` on user-entered text.
- **U10.** The back link's wording comes from the host's `back_label` hook,
  since an invoice is reachable from several places.
- **U11.** Templates read **only** from `doc` (an `InvoiceDocument`) and
  never reach for a host model — this is what lets the module move.

## 12. The PDF (`billing/pdf.py`)

- **PD1 — The document alone.** `GET /invoices/<id>/pdf` returns the invoice
  as a PDF rendered on the server from a standalone template: nothing of the
  host's `base.html` (nav, footer, scripts, forms) is in it.
- **PD2 — The renderer is optional.** WeasyPrint is imported lazily. Where
  it, or the Pango library under it, is missing, `available()` is false, the
  app still boots, the page falls back to printing (U2), and the PDF URL
  redirects to the invoice page instead of erroring.
- **PD3 — One document, two renderings.** The PDF is built from the same
  `InvoiceDocument` (`document_for`) as the page, so frozen-vs-live (F14) is
  decided once and the two cannot disagree. PDF templates read only from
  `doc`, as U11 requires of the page.
- **PD4 — Nothing is fetched.** The stylesheet is inline, the font is one
  installed on the system, the logo is embedded in the document as a base64
  PNG, and the renderer is handed a URL fetcher that answers **only** that
  embedded-PNG form — decoding it itself — and refuses every other URL
  (`http`, `file`, any other `data:` type). Nothing typed into an invoice
  can make the server request a resource. A refused reference is left out
  and the invoice still renders.
- **PD5 — No typed text inside CSS.** Everything a person typed is
  HTML-escaped, and the invoice number reaches the page footer through CSS
  `string-set`, never by being templated into the stylesheet; the footer
  text is HTML in a running element (FT3), never a CSS string. The only
  tenant choices that do enter the stylesheet are colours, and only as
  `Branding` hands them out (BR3, FT4).
- **PD6 — Host links are not rendered.** `doc.payer.url` and
  `doc.subject_url` are for the app's own page; the PDF prints names as text.
- **PD7 — Status on paper.** "Draft", "Void" and "Paid" are printed beside
  the number; "Sent" is not — it is the app's bookkeeping, not something the
  client needs to read.
- **PD8.** Payment instructions follow U5 exactly: printed only while money
  is owed and the invoice isn't void, under the heading **"Payment
  instructions"**.
- **PD8a — A closing block at the bottom left of the last page.** Notes,
  then payment instructions, sit together in WeasyPrint's footnote area
  (`float: footnote`, with no call or marker printed), so they close the
  page they land on, at its foot and on the left, as in the invoice the
  Banded layout was ported from. Unlike that invoice the block is **never
  absolutely positioned**: when it doesn't fit under the last line, it
  moves to the next page instead of being printed over items. With a
  footer it ends about 10mm above the band. Its markup has **no whitespace
  between its tags** — inside the footnote area each gap becomes an empty
  ~27px line, which once made it measure twice its height and moved it to
  a page of its own with room to spare. With no notes and nothing owed,
  there is no closing block.
- **PD8b — Notes have a "Notes" heading and always take the room of three
  lines**, written or not, so the payment instructions print at the same
  height on every invoice whatever the notes say. Longer notes stored
  before the limit (PD8c) are cut at three lines on paper.
- **PD8c — Notes are at most three lines.** `clean_notes()` (applied by
  `set_status`) drops blank lines, joins any lines past the third onto it
  rather than losing them, and caps the text at `config.NOTES_MAX_LENGTH`
  (250) characters; the invoice page's box is three rows tall with that
  `maxlength`.
- **PD9.** The file downloads as an attachment named `<number>.pdf`, the
  number reduced to letters, digits, `_` and `-` (the prefix is
  tenant-typed), falling back to `invoice.pdf`.
- **PD10 — It flows.** Letter portrait. A long invoice runs onto as many
  pages as it needs, repeating the table header, with the invoice number and
  "Page n of m" in the page footer (inside the footer band when there is
  one, FT2); the totals, payments and notes blocks are never split across a
  page break.
- **PD11.** An unknown template name falls back to the default (`classic`)
  rather than raising — a stale stored value must not block an export.
- **PD12 — Presentation is live; content is frozen.** The freeze contract
  (§6) covers what an invoice *says*. How it *looks* — layout, colour and
  logo — is read from the profile at render time, for every
  invoice however long ago it was issued. A reprint after a rebrand carries
  the same seller details and figures in the new look.
- **PD13.** The PDF route is login-protected and tenant-scoped like the page
  (A1, A2): another tenant's invoice id is a 404.

## 13. Branding (`Branding`, Settings → Invoicing → Invoice appearance)

- **BR1 — Two layouts**, named **Classic** and **Banded**.
  `config.INVOICE_TEMPLATES` lists them: `classic` (plain, black on white —
  the default) and `banded` (a full-width coloured header band, accent
  table header, large invoice total). Every layout listed there has a
  template in `pdf.TEMPLATES`, and vice versa.
- **BR2 — One colour, used by `banded` only**: the band, the table header
  and its rule, the totals labels and the invoice total. The small labels,
  the status pill and the page footer stay the app's soft grey (`#7e7a78`)
  whatever is chosen. `classic` ignores the colour.
- **BR3 — Only a checked `#rrggbb` reaches the stylesheet.** `clean_color`
  accepts exactly a `#` and six hex digits (lower-cased), nothing else — no
  names, no shorthand, no functions. `Branding.primary` returns the cleaned
  value or the default, so a bad stored value can neither break a render
  nor inject CSS. Templates use that property (and `on_primary`, derived
  from it), never the stored string.
- **BR4 — Defaults.** With nothing chosen: `classic`, colour `#1c1a17` —
  the app's own ink.
- **BR5 — The band stays readable.** Text on the band is white on a dark
  primary and near-black on a pale one, chosen by relative luminance rather
  than left to the tenant.
- **BR6 — Everything `classic` prints, `banded` prints**: letterhead and
  registrations, buyer, dates, lines, every tax line, paid and balance,
  payment instructions (PD8), payments received, notes, and the status
  (PD7). The whole of PD1–PD10 holds for both.
- **BR7 — The settings form** (the host's, `POST
  /settings/invoicing/appearance`) refuses an unknown layout or a malformed
  colour, **leaves the stored value as it was**, says so in a notice shown
  **inside the appearance section**, and still saves the fields that were
  valid. A field the form didn't send is left alone. Every appearance
  route returns to `/settings/invoicing`, which reopens where it was
  (the host's MOD7), scrolled to that notice when there is one.
- **BR8 — The preview.** `GET /invoices/preview.pdf` renders a sample
  invoice — the tenant's real letterhead and next number, an invented
  buyer — in the **saved** look, inline in the browser. It stores nothing
  and consumes no invoice number. The settings page links to it only where
  a PDF can be rendered (PD2).
- **BR9.** Branding is per tenant: one company's choice never shows on
  another's invoices.
- **BR10 — The settings section, top to bottom: logo, layout, accent
  colour, footer** (FT6), under a two-sentence introduction. "Logo" is a
  pointer to Settings → General → Brand, where the logo is set (brand
  BL10), plus brand's warning when the saved look would hide it (BL14). The layouts are radio
  buttons drawn as cards, each with a miniature of the invoice; no
  dropdown. The **accent colour** is a swatch beside a `#rrggbb` text box:
  clicking the swatch opens a picker the page draws itself (a
  saturation/brightness area and a hue bar, usable by pointer and
  keyboard) that closes on a click elsewhere or Escape. No preset colours,
  and never the browser's native colour input. The text box is what's
  submitted, so the page works without JavaScript; the script
  (`static/assets/js/invoice-appearance.js`) only adds the picker, shows
  the accent colour only while Banded is selected, and makes the
  miniatures follow the colour live.

### The footer

- **FT1 — Off until switched on.** `footer_enabled` defaults to false, and
  with it off neither layout prints a footer band or any footer text, even
  if text and colours are stored.
- **FT2 — A band at the very bottom of every page**, as on the invoice it's
  modelled on: 14mm tall, exactly filling the page's bottom margin, from
  the left edge of the sheet to the right in both layouts (Classic's
  reaches out through its side margins as one box — coloured corner boxes
  left visible seams where they met; a page background was tried and
  WeasyPrint ignores its size). The footer text on the left and the
  invoice number with "Page n of m" on the right, which then print nowhere
  else.
- **FT3 — It can't cover or break the invoice.** The band is a running
  element shown in a margin box, so it is never on top of the content. Its
  text is centred, about 7mm up from the edge, which clears what most
  printers can't reach; the band's colour itself may stop short of the
  edge on a printer that can't print borderless. The text is printed as
  escaped HTML, never written into the stylesheet.
- **FT4 — Colours and text.** A background and a text colour, each a
  checked `#rrggbb` through `Branding.footer_bg` / `footer_fg`, defaulting
  to `#e4e4e3` and `#666666` (the grey band of the invoice it's modelled
  on). The text is one line, whitespace collapsed, at most
  `config.FOOTER_TEXT_MAX_LENGTH` (200) characters; blank is stored as
  null, and a footer with no text still prints its band and page number.
- **FT5 — Like the rest of the look, it's live** (PD12): switching it on,
  off or changing it applies to every invoice, issued or not.
- **FT6 — The settings form** has a checkbox, and below it — shown only
  while ticked — the text box and two colour pickers like the accent
  colour's. A hidden `footer_form` marker tells an unticked box ("off")
  from a form that didn't show the footer ("leave alone", hard rule 9).
  Switching off keeps the stored text and colours. The layout miniatures
  show the band, in its colours, while it's on.

## 14. The logo on the invoice

The logo itself — what an upload must be, where it's kept, the settings
form — is the studio's brand since 2026-10-06: `brand/REQUIREMENTS.md`
BL1–BL15, which took over this section's old `L1`–`L6` and `L9`–`L11`.
Billing keeps only what happens on the document.

- **L7 — It reaches the PDF embedded.** The host registers where the logo
  comes from (`invoicing.set_logo_source(fn)`, `fn(company_id) -> PNG bytes
  | None`); `branding_for()` returns it as a `data:image/png;base64,…` URI on
  `Branding`, and `profile.branding` alone does not carry it. No source, no
  logo, or a file gone missing: the invoice simply prints without one — it
  must not stop an export. The source must hand over PNG it re-encoded
  itself, since the renderer accepts an embedded PNG and nothing else (PD4).
- **L8 — Both layouts print it, and still name the seller in words.**
  `classic` puts it above the company name; `banded` puts it in the band in
  place of the name, which moves to the head of the address block.

## 15. Explicit non-requirements

These are deliberate omissions, not oversights — listed so nobody "fixes"
them without checking first. See `docs/roadmap.md` for reasoning.

- **Z1.** **No invoice deletion**, of drafts or anything else. Adding it
  means fixing N11 first.
- **Z2.** **No per-line taxable flag** — tax applies to the whole subtotal
  (C9). Zero-rated or exempt items would need a flag on the host's line
  model and `taxes_for()` taking a taxable subtotal rather than the full
  one.
- **Z3.** **The module doesn't freeze an invoice's lines, only its money.**
  `document_for()` lists the subject's lines as they are now, so it's the
  **host's** job to stop them changing once an invoice is out — this one
  does (the host's `OR7a`: an order's lines lock while its invoice is sent
  or void). A host that let them change would print lines that don't add up
  to the frozen total. Snapshotting the lines at freeze would remove the
  dependency, at the cost of a table and a migration.
- **Z4.** **Money is stored as `Float`, not integer cents.** Rounding is
  handled per tax line (C6) and settlement is float-tolerant (D4). Fine at
  this scale; integer cents is the correct fix if this ever handles
  someone's books for real.
- **Z5.** **No payment-processor integration.** Invoicing is local-only: the
  app numbers, renders and prints its own invoices, and payments are entered
  by hand whatever their method. Square is one `method` value, not a second
  source of truth. If that ever lands, invoices must stop being created in
  the processor's dashboard — its auto-numbering would collide with N1.
- **Z6.** **No CSRF layer on this blueprint's forms** — same posture as the
  host's own mutating routes (relies on `SESSION_COOKIE_SAMESITE=Lax`),
  unlike `communications/`, which has its own. Add it here too if the app
  ever gains `CSRFProtect`.
- **Z7.** **No stored PDFs.** The PDF is rendered on request and never kept:
  the printed document changes after issue anyway (payments received, balance
  due), so a stored copy would be a copy that can disagree (PD12).
- **Z8.** **No credit notes, refunds or partial voids.** Void is
  all-or-nothing.
- **Z9.** **Single currency.** Nothing carries a currency code; CAD is
  implied throughout.
- **Z10.** **No dunning, reminders or due-date automation.** `due_date` is
  recorded and printed, and nothing acts on it.

---

## Test coverage map

Rule id → covering test(s). **"— gap —"** means the rule is real and
currently believed true, but nothing in the suite would catch a regression
of it; that's a to-do, not a shrug.

Files: `tests/test_tax.py`, `tests/test_invoicing.py`,
`tests/test_invoice_routes.py`, `tests/test_invoice_pdf.py`,
`tests/test_invoice_logo.py`, `tests/test_discounts.py`,
`tests/test_addresses.py`, `tests/test_billing_boundary.py`.

| Rule | Test(s) |
| --- | --- |
| B1 | `test_billing_never_imports_a_host_model` |
| B2 | `test_billing_never_imports_the_host_adapter` |
| B3 | `test_the_tax_engine_imports_nothing_but_the_standard_library` |
| B4 | `test_the_document_dataclasses_touch_no_database` |
| B5 | `test_the_subject_foreign_key_is_configurable` |
| B6 | `test_services_are_the_public_surface`, `test_there_are_billing_sources_to_check` (guards the scan itself from silently finding nothing) |
| B7 | `test_the_module_never_needs_a_host_model` |
| B8 | — gap — *(the name being host-owned is structural; nothing asserts a profile can't invent one)* |
| W1–W4 | *(implicit — module shape and `register()`'s signature)* |
| R1 | `test_every_province_and_territory_is_covered` |
| R2 | `test_gst_hst_rate_matches_the_cra` |
| R3 | `test_provincial_rate_matches_the_cra` |
| R4 | `test_hst_provinces_charge_one_combined_tax`, `test_gst_hst_rate_matches_the_cra` |
| R5 | `test_nova_scotia_is_the_reduced_rate` |
| R6 | `test_hst_hangs_off_the_federal_registration` |
| R7 | `test_manitoba_uses_its_own_name_for_the_tax` |
| R8 | `test_pst_provinces_share_one_registration_field`, `test_quebec_qst_is_gated_on_the_qst_registration` |
| R8a | `test_unregistered_taxes_names_the_skipped_tax`, `test_a_bc_order_without_a_pst_number_warns_that_pst_is_skipped` |
| R9 | *(structural — `test_tax.py` writes the CRA table out a second time, independently of `PROVINCE_TAXES`. Nothing can test that a human did the corrections in the right direction.)* |
| R10 | `test_the_spellings_of_one_province_all_resolve`, `test_the_other_provinces_resolve_too`, `test_every_code_and_name_in_the_table_resolves_to_itself` |
| R11 | `test_anything_else_is_dropped_rather_than_guessed`, `test_a_dropped_province_is_not_a_taxable_one` |
| C1 | `test_tax_follows_the_client_province_not_the_company`, `test_two_clients_in_different_provinces_are_taxed_differently` |
| C2 | `test_a_tax_is_not_charged_without_its_registration`, `test_a_seller_registered_for_nothing_charges_nothing` |
| C3 | `test_order_total_is_subtotal_plus_tax`, `test_quebec_client_is_charged_gst_and_qst`, `test_ontario_client_is_charged_one_hst_line`, `test_alberta_client_is_charged_gst_only` |
| C4 | `test_no_province_charges_nothing`, `test_an_unrecognised_province_charges_nothing` |
| C5 | `test_status_explains_why_nothing_was_charged`, `test_tax_status_is_ok_when_tax_applies`, `test_tax_status_flags_a_client_with_no_province`, `test_tax_status_flags_an_unrecognised_province`, `test_tax_status_flags_a_missing_registration` |
| C6 | `test_amounts_are_rounded_to_the_cent` |
| C7 | `test_each_tax_is_computed_on_the_subtotal_not_compounded` |
| C8 | `test_rate_percent_is_printable` |
| C9 | *(by construction — `taxes_for` takes one subtotal; see Z2)* |
| C10 | `test_place_of_supply`, `test_a_picked_up_order_is_taxed_in_the_studio_province`, `test_a_picked_up_order_with_no_studio_province_says_so`, `test_an_export_collected_at_the_studio_is_taxed_there`, `test_freezing_uses_the_pickup_province` (`tests/test_place_of_supply.py`) |
| C11 | `test_status_for_the_new_cases`, `test_an_export_is_charged_nothing_and_says_why` (`tests/test_place_of_supply.py`) |
| C12 | `test_taxes_elsewhere_flags_pst_for_another_province`, `test_pst_for_another_province_is_charged_but_flagged` (`tests/test_place_of_supply.py`) |
| DS1 | `test_anything_unusable_is_no_discount`, `test_a_percentage_is_capped_at_a_hundred`, `test_a_blank_label_is_none_and_spaces_collapse` (`tests/test_discounts.py`, as are the rest of DS) |
| DS2 | `test_a_percentage_rounds_to_the_cent`, `test_a_fixed_amount_never_exceeds_what_there_is_to_discount` |
| DS3 | `test_the_description_names_the_rate_only_for_a_percentage` |
| DS4 | `test_tax_is_charged_on_the_discounted_subtotal`, `test_a_fixed_discount_comes_off_before_tax_too`, `test_no_discount_changes_nothing` |
| DS5 | `test_issuing_freezes_the_discount`, `test_an_issued_invoice_ignores_a_later_discount_change`, `test_a_draft_follows_the_discount_live` |
| DS6 | `test_both_pdf_layouts_print_the_discount_line`, `test_no_discount_prints_no_discount_line` *(the invoice page's row is unasserted)* |
| P1 | `test_profiles_are_per_tenant` |
| P2 | `test_profiles_are_per_tenant` (creation-on-first-use is exercised, not separately asserted) |
| P3 | `test_the_profile_name_survives_a_plain_query` |
| P4 | `test_profile_for_does_not_blank_a_stored_name` |
| P5 | — gap — *(unknown keys being ignored is not asserted)* |
| P6 | — gap — *(the `"INV"` fallback is exercised by every numbering test, never asserted directly)* |
| P7 | `test_the_backfill_does_not_freeze_an_empty_letterhead` (indirectly) |
| P8 | `test_an_issued_invoice_ignores_later_seller_changes`, `test_a_draft_reads_seller_details_live` |
| P9 | — gap — *(registration ordering is markup-adjacent and unasserted)* |
| P10 | `test_update_company_rejects_an_unknown_province`, `test_update_invoicing_truncates_a_long_prefix_to_ten_chars` (both in `tests/test_settings_company.py`) |
| N1 | `test_first_number_of_the_year` |
| N2 | `test_numbers_increment` |
| N3 | `test_the_sequence_restarts_each_year` |
| N4 | `test_sequences_are_per_company`, `test_two_companies_may_hold_the_same_number` |
| N5 | `test_deleting_an_older_invoice_leaves_a_gap_rather_than_reusing_it` |
| N6 | `test_a_voided_invoice_does_not_free_up_its_number` |
| N7 | `test_numbers_stay_sortable_past_nine` |
| N8 | `test_the_same_number_twice_for_one_company_is_rejected` |
| N9 | `test_creating_twice_for_one_subject_returns_the_same_invoice`, `test_double_submitting_does_not_burn_a_second_number` |
| N10 | `test_next_invoice_number_takes_the_prefix_it_is_given` |
| N11 | `test_deleting_the_latest_invoice_DOES_free_its_number` *(pins the limit deliberately — if this ever fails, the limit was fixed and this rule should be rewritten, not the test deleted)* |
| N12 | `test_a_new_invoice_starts_as_an_unfrozen_draft`, `test_creating_an_invoice_assigns_the_next_number`, `test_a_draft_is_not_frozen` |
| F1 | `test_a_draft_reads_seller_details_live`, `test_a_draft_subtotal_follows_its_line_items`, `test_an_uninvoiced_order_does_follow_a_province_change` |
| F2 | `test_resaving_an_issued_invoice_does_not_rewrite_history`, `test_resaving_a_sent_invoice_does_not_rewrite_history` |
| F3 | `test_issuing_stores_the_seller_details_and_the_money`, `test_marking_it_sent_freezes_the_issuer_and_the_money` |
| F4 | `test_an_issued_invoice_ignores_later_seller_changes` |
| F5 | `test_an_issued_invoice_ignores_later_line_item_changes` |
| F6 | `test_an_issued_invoice_ignores_the_buyer_moving_province` |
| F7 | `test_issuing_with_no_taxable_province_stores_no_tax_rows` |
| F8 | `test_voiding_a_draft_freezes_it_too` |
| F9 | `test_a_draft_saved_as_a_draft_stays_unfrozen` |
| F10 | `test_an_unknown_status_is_ignored` (both `test_invoicing.py` and `test_invoice_routes.py`) |
| F11 | *(implicit — every freeze test reads through it)* |
| F12 | `test_an_invoice_issued_before_freezing_existed_shows_no_tax` (`tests/test_invoice_routes.py`) |
| F13 | `test_a_snapshot_with_a_blank_name_is_not_treated_as_frozen` |
| F14 | `test_a_draft_reads_seller_details_live`, `test_an_issued_invoice_ignores_later_seller_changes` |
| F15 | `test_back_to_draft_and_sent_again_refreezes_with_todays_tax` (`tests/test_invoice_routes.py`); in a browser, `e2e/tests/invoice-correction.spec.ts` |
| D1 | `test_display_status_is_paid_once_payments_cover_the_total` |
| D2 | `test_a_deposit_does_not_make_it_paid`, `test_paying_the_pre_tax_amount_does_not_make_it_paid` |
| D3 | `test_void_wins_over_paid` |
| D4 | `test_a_cent_of_rounding_does_not_leave_it_unpaid` |
| D5 | `test_a_zero_value_order_is_not_reported_as_paid` |
| D6 | `test_paid_cannot_be_set_by_hand` |
| D7 | `test_balance_due_is_tax_inclusive`, `test_the_invoice_list_totals_are_tax_inclusive` |
| D8 | `test_lifetime_value_is_tax_inclusive` |
| D9 | `test_a_taxless_order_still_totals_its_subtotal` |
| D10 | — gap — *(`is_outstanding` itself is unasserted; the list total that uses the same predicate is covered by D7)* |
| D11 | `test_tax_collected_sums_the_frozen_rows`, `test_tax_collected_excludes_voided_invoices`, `test_tax_collected_is_scoped_to_the_tenant`, `test_tax_collected_windows_on_the_issued_date` (the `since`/`until` window) |
| D12 | `test_invoiced_subject_ids` |
| AD1 | `test_full_address_uses_the_canada_post_layout` |
| AD2 | `test_street_only`, `test_city_and_province_only`, `test_city_only`, `test_province_and_postal_without_a_city` |
| AD3 | `test_nothing_at_all_is_none` |
| AD4 | `test_seller_and_buyer_render_the_same_way` |
| AD5 | — gap — *(that only the rendered string is frozen is structural — one column — and unasserted)* |
| AD6 | `test_a_well_formed_address_is_split_into_its_parts`, `test_the_split_recovers_a_province_that_tax_depends_on`, `test_a_multi_line_street_keeps_all_of_its_lines`, `test_postal_code_spacing_variants_are_recognised`, `test_an_unparseable_address_survives_whole_in_the_street_field` |
| AD7 | `test_an_unparseable_address_does_not_invent_a_province` |
| AD8 | `test_the_legacy_column_is_dropped`, `test_the_migration_is_a_noop_once_applied`, `test_a_row_that_already_has_a_street_is_left_alone` |
| M1 | *(structural — enforced by B1's scan plus the file's existence)* |
| M2 | `test_the_migration_is_a_noop_once_applied` (the address half only) — gap: `ADDED_COLUMNS` re-running is untested |
| M3 | — gap — *(the `order_id` → `subject_id` rename has no test)* |
| M4 | `test_a_legacy_company_address_moves_into_the_billing_profile` |
| M5 | — gap — *(the don't-overwrite-a-filled-profile branch is untested; the equivalent for clients is `test_a_row_that_already_has_a_street_is_left_alone`)* |
| M6 | `test_a_legacy_company_address_moves_into_the_billing_profile` |
| M7 | `test_profile_names_are_backfilled_from_the_host` |
| M8 | `test_a_nameless_snapshot_gets_its_name_restored` |
| M9 | `test_the_backfill_does_not_freeze_an_empty_letterhead` |
| M10 | `test_a_contentless_snapshot_is_repaired_once_a_letterhead_exists`, `test_a_real_snapshot_is_never_repaired` |
| M11 | — gap — *(that the backfill writes no tax rows is unasserted)* |
| M12 | — gap — *(single-pass convergence was verified by hand against a copy of the real database, not by a test)* |
| M13 | — gap — |
| A1 | `test_invoice_pages_require_a_login`, `test_issuing_an_invoice_requires_a_login` |
| A2 | `test_get_invoice_is_scoped_to_the_tenant`, `test_viewing_another_tenants_invoice_404s` |
| A3 | `test_listing_is_scoped_to_the_tenant` |
| A4 | `test_creating_an_invoice_for_another_tenants_order_404s` |
| A5 | `test_profiles_are_per_tenant` |
| A6 | `test_tax_collected_is_scoped_to_the_tenant`, `test_invoiced_subject_ids`, `test_changing_another_tenants_invoice_status_404s` (`tests/test_invoice_routes.py`) |
| U1 | — gap — *(print/no-print markup; manually verified in the browser only)* |
| U2 | `test_the_page_offers_the_pdf_when_it_can_be_rendered`, `test_the_page_falls_back_to_printing_when_it_cannot` |
| U3 | `test_the_order_page_warns_when_tax_cannot_be_calculated` (host's order page), `test_the_invoice_page_tax_warning_shows_on_a_draft_only`, `test_the_draft_invoice_page_explains_the_tax` (`tests/test_invoice_routes.py`); in a browser, `e2e/tests/invoice-lifecycle.spec.ts` |
| U4 | `test_paid_cannot_be_set_by_hand` |
| U5 | `test_payment_instructions_show_only_while_money_is_owed`, `test_a_void_invoice_shows_no_payment_instructions` (`tests/test_invoice_routes.py`); in a browser, `e2e/tests/invoice-payments.spec.ts` |
| U6 | — gap — |
| U7 | `test_the_invoice_page_shows_the_tax_breakdown` |
| U8 | `test_the_invoice_list_totals_are_tax_inclusive` (the Outstanding total) — gap: the "not invoiced yet" list itself is untested |
| U9 | — gap — *(CSS)* |
| U10 | — gap — |
| U11 | — gap — *(would need a template scan like `test_billing_boundary.py` does for Python)* |
| PD1 | `test_the_html_is_the_document_alone`, `test_the_html_carries_the_whole_invoice`, `test_it_renders_a_real_pdf` *(skipped without WeasyPrint — runs in the Docker image)* |
| PD2 | `test_rendering_without_weasyprint_says_so`, `test_without_a_renderer_the_pdf_url_returns_to_the_page`, `test_the_page_falls_back_to_printing_when_it_cannot` |
| PD3 | `test_the_pdf_is_built_from_the_same_document_as_the_page` |
| PD4 | `test_the_html_needs_nothing_from_the_network`, `test_the_renderer_fetches_nothing`, `test_the_renderer_accepts_only_an_embedded_png`, `test_a_malformed_embedded_image_is_refused`, `test_the_logo_is_the_only_thing_a_document_references`; and through WeasyPrint itself *(skipped without it)*: `test_the_fetcher_handed_to_weasyprint_refuses_too`, `test_an_external_image_in_a_document_is_left_out_not_fetched` |
| PD5 | `test_typed_text_is_escaped`, `test_the_number_is_never_templated_into_the_stylesheet` |
| PD6 | `test_host_links_are_not_rendered` |
| PD7 | `test_a_status_that_changes_the_meaning_is_printed`, `test_sent_is_not_printed` |
| PD8 | `test_payment_instructions_follow_the_same_rule_as_the_page` |
| PD8a | `test_payment_instructions_sit_at_the_foot_of_the_last_page`, `test_the_payment_block_has_no_whitespace_between_its_tags`, `test_nothing_closes_an_invoice_with_no_notes_and_nothing_owed`, `test_a_short_invoice_with_everything_on_it_stays_on_one_page` *(skipped without WeasyPrint)*, `test_with_a_footer_the_payment_instructions_sit_well_above_it` *(skipped without WeasyPrint)*, `test_rendered_payment_instructions_close_the_last_page_after_the_notes` *(needs WeasyPrint and pdftotext — runs in the Docker image)* |
| PD8b | `test_notes_have_a_heading_and_come_before_the_payment_instructions`, `test_notes_take_the_room_of_three_lines_whatever_their_length`, `test_the_payment_instructions_print_at_the_same_height_whatever_the_notes` *(skipped without WeasyPrint)* |
| PD8c | `test_notes_are_kept_to_three_lines`, `test_notes_are_capped_in_length` (`tests/test_invoicing.py`), `test_saved_notes_are_kept_to_three_lines`, `test_the_notes_box_says_it_holds_three_lines` (`tests/test_invoice_routes.py`) |
| PD9 | `test_the_pdf_downloads_under_the_invoice_number`, `test_the_filename_is_the_number_made_safe` |
| PD10 | `test_a_long_invoice_renders_without_error` *(skipped without WeasyPrint)* — gap: that blocks stay whole and the header repeats was checked by eye on a rendered 4-page invoice, not asserted |
| PD11 | `test_an_unknown_template_falls_back_to_the_default` |
| FT1 | `test_there_is_no_footer_until_it_is_switched_on`, `test_the_footer_is_off_with_grey_defaults_until_chosen`, `test_the_page_shows_the_footer_switched_off_by_default` |
| FT2 | `test_the_footer_prints_its_text_and_the_page_number`, `test_the_footer_band_fills_the_bottom_margin`, `test_the_rendered_footer_band_touches_the_bottom_edge` *(skipped without WeasyPrint)*, `test_the_rendered_band_spans_the_sheet_in_one_colour` and `test_the_rendered_footer_is_on_every_page` *(need WeasyPrint and poppler)* |
| FT3 | `test_the_footer_text_is_escaped_and_never_enters_the_stylesheet` — gap: that the band never overlaps content was checked by eye on rendered PDFs |
| FT4 | `test_bad_footer_colours_fall_back_to_the_defaults`, `test_the_footer_text_is_one_line_and_capped`, `test_a_bad_footer_colour_is_refused` |
| FT5 | *(by construction — the footer is on `Branding`, read live like the rest; see PD12)* |
| FT6 | `test_switching_the_footer_on_saves_it_with_its_text_and_colours`, `test_unticking_the_box_switches_the_footer_off`, `test_a_form_without_the_footer_fields_leaves_the_footer_alone`, `test_switching_the_footer_off_keeps_its_text_and_colours`, `test_the_page_shows_a_saved_footer`, `test_each_colour_has_its_own_picker` — gap: showing the fields only while ticked, and the miniatures following the colours live, are script behaviour checked by hand in a browser |
| PD12 | `test_an_issued_invoice_follows_a_rebrand_but_keeps_what_it_said`, `test_the_pdf_wears_the_companys_saved_look` |
| PD13 | `test_the_pdf_requires_a_login`, `test_another_tenants_invoice_pdf_404s`, `test_a_missing_invoice_pdf_404s` |
| P11 | `test_branding_with_nothing_chosen_is_the_default_look`, `test_the_settings_page_shows_the_defaults_before_anything_is_chosen` |
| BR1 | `test_every_layout_offered_in_settings_has_a_template_behind_it` |
| BR2 | `test_the_banded_layout_wears_the_chosen_colour`, `test_the_classic_layout_ignores_the_colours`, `test_there_is_no_secondary_colour_any_more` |
| BR3 | `test_only_a_plain_hex_colour_counts_as_a_colour`, `test_a_bad_stored_colour_never_reaches_the_stylesheet` |
| BR4 | `test_branding_with_nothing_chosen_is_the_default_look` |
| BR5 | `test_text_on_the_band_stays_readable` |
| BR6 | every document test in `test_invoice_pdf.py` runs once per layout (the `look` fixture) |
| BR7 | `test_update_appearance_saves_the_layout_and_the_colour`, `test_update_appearance_refuses_a_colour_that_is_not_plain_hex`, `test_update_appearance_refuses_an_unknown_layout`, `test_update_appearance_leaves_alone_what_the_form_did_not_send`, `test_update_appearance_does_not_touch_the_letterhead`, `test_update_appearance_requires_a_login`, `test_a_refusal_is_shown_inside_the_appearance_section`, `test_saving_returns_to_the_same_page_without_choosing_a_place` (settings tests in `tests/test_settings_company.py`) |
| BR8 | `test_the_preview_shows_a_sample_in_the_saved_look`, `test_the_preview_uses_up_no_invoice_number`, `test_the_preview_requires_a_login`, `test_without_a_renderer_the_preview_goes_somewhere_that_works`, `test_the_sample_is_the_sellers_own_document`, `test_a_sample_from_an_unregistered_seller_charges_no_tax`, `test_the_preview_link_appears_only_where_a_pdf_can_be_rendered` |
| BR9 | `test_update_appearance_is_per_company` |
| BR10 | `test_the_logo_comes_before_the_layout`, `test_the_introduction_is_short`, `test_the_layouts_are_radio_buttons_named_classic_and_banded`, `test_each_layout_has_a_thumbnail`, `test_the_saved_layout_is_the_checked_one`, `test_the_page_shows_the_saved_colour`, `test_the_page_shows_the_defaults_before_anything_is_chosen`, `test_the_accent_colour_is_a_swatch_that_opens_a_picker` — gap: opening and closing the picker, dragging, keyboard control, the live miniatures and the Banded-only accent colour are script behaviour, checked by hand in a browser (desktop and phone width), not by the suite |
| L1–L6, L9–L11 | moved to brand/ as BL1–BL11 — see `brand/REQUIREMENTS.md` |
| L7 | `tests/test_invoice_logo.py`: `test_branding_carries_the_logo_embedded`, `test_branding_without_a_logo_has_none`, `test_a_logo_whose_file_has_gone_is_simply_left_off`, `test_without_a_source_invoices_print_no_logo`, `test_the_logo_keeps_the_layout_and_colour_beside_it`, `test_a_real_pdf_renders_with_a_logo` *(skipped without WeasyPrint)* |
| L8 | `test_every_layout_prints_the_logo`, `test_the_seller_is_still_named_in_words_beside_a_logo`, `test_no_logo_no_image` |
| Z1–Z10 | *(non-requirements — nothing to test)* |

### The tax-collected report

`analytics()` in `app.py` computes
`invoicing.tax_collected(company_id, since=date(this_year, 1, 1))` and renders
it in `analytics.html`'s Revenue section as a **Tax billed YTD** card — one
`(label, amount)` row per tax charged (GST, QST, …), the shape a remittance
takes. It exercises the `since` window of D11 (previously a gap), and is
scoped `issued_date >= Jan 1` so it lines up with the Revenue YTD figure beside
it. This closes `docs/roadmap.md`'s "No tax-collected report" gap, which
described the earlier half-finished state where the figure was computed but
never shown.

The card is labelled **"Tax billed"**, not "collected", on purpose: it sums the
frozen tax on every issued, non-void invoice regardless of whether it's been
paid — the **accrual-basis** figure a Canadian GST/HST/QST remittance is
normally filed on. A cash-basis figure (tax only on invoices whose payments
cover the total) would be a different, more involved computation and isn't
built.

Everything marked "manually verified in the browser only" was exercised by
hand during development but has no regression protection — a future change to
that markup or CSS could silently break it and the suite would stay green.
Closing the "— gap —" rows is the obvious next step if this module gets
touched again; **F12, M3 and A6 are the ones worth closing first**, since each
guards money or a tenant boundary rather than appearance.
