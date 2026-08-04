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
  row per tenant), `invoices`, `invoice_tax_lines`.
- **W2.** Invoice numbering (§4), issuing and freezing (§5), tax
  calculation (§2–§3), and the derived money on a document (§6).
- **W3.** An optional blueprint (`/invoices`, `/invoices/<id>`,
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
- **R9.** The rate table must be corrected **from the CRA**, never from the
  test — and the test corrected from the CRA too. A test that imported the
  constant it checks would pass no matter what the constant said.

## 3. What actually gets charged

- **C1 — Destination-based.** Tax follows the **buyer's** province
  (`Billable.tax_province`), never the seller's.
- **C2 — Registration-gated.** A tax is charged only when the seller holds
  the matching registration. A studio under the small-supplier threshold
  has no `gst_number` and charges no GST; one that never registered in BC
  charges no BC PST. This falls out of the data rather than needing a
  separate "do we charge tax" switch.
- **C3 — Prices are tax-exclusive.** A line's `unit_price` is pre-tax; tax
  is added on top. Total = subtotal + tax.
- **C4 — Charge nothing rather than guess.** A blank or unrecognised
  province yields no tax lines.
- **C5 — Say why nothing was charged.** `status_for()` returns `ok`,
  `no_buyer_province`, `unknown_province` or `not_registered`, so a host can
  surface the reason instead of silently billing zero.
- **C6 — Round per tax line, to the cent**, so a document's total matches
  the sum of its own printed lines.
- **C7 — Never compound.** Each tax is computed on the pre-tax subtotal,
  never on another tax.
- **C8 — Rates print without trailing zeros** (`5%`, `9.975%`).
- **C9 — Tax applies to the whole subtotal.** There is no per-line taxable
  flag (see Z2).

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
  `gst_number`, `pst_number`, `qst_number`, `neq`, `payment_instructions`.
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
  code, prefix uppercased and capped at 10 characters) is the **host's**
  job, in its settings form. The module stores what it's given.

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
  `issued_subtotal`, and one `InvoiceTaxLine` row per tax charged.
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
  controls below it are `.no-print`, so "Print / save as PDF" produces the
  document alone.
- **U2.** Printing is `window.print()` — the browser's own PDF export, not
  server-side rendering (Z7).
- **U3.** The "no tax on this invoice" warning appears **only on a draft**
  and only when `tax_status != "ok"`, wording the specific reason (C5), and
  says the figures freeze the moment it's marked sent. Past draft the
  warning is gone, because the number is settled.
- **U4.** The status dropdown offers only `SETTABLE_STATUSES` — "paid" is
  never selectable (D6), and the page says so in as many words.
- **U5.** Payment instructions print under "How to pay" **only** when money
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

## 12. Explicit non-requirements

These are deliberate omissions, not oversights — listed so nobody "fixes"
them without checking first. See `docs/roadmap.md` for reasoning.

- **Z1.** **No invoice deletion**, of drafts or anything else. Adding it
  means fixing N11 first.
- **Z2.** **No per-line taxable flag** — tax applies to the whole subtotal
  (C9). Zero-rated or exempt items would need a flag on the host's line
  model and `taxes_for()` taking a taxable subtotal rather than the full
  one.
- **Z3.** **Nothing blocks editing an issued subject's line items.** The
  invoice total is safely frozen (F5), so the client is never re-billed, but
  the host's order page will then show lines that don't add up to the
  invoice. It says so in a note; properly, the host should block the edit.
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
- **Z7.** **No server-side PDF generation** — the browser's print dialog is
  the export path.
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
`tests/test_invoice_routes.py`, `tests/test_addresses.py`,
`tests/test_billing_boundary.py`.

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
| R9 | *(structural — `test_tax.py` writes the CRA table out a second time, independently of `PROVINCE_TAXES`. Nothing can test that a human did the corrections in the right direction.)* |
| C1 | `test_tax_follows_the_client_province_not_the_company`, `test_two_clients_in_different_provinces_are_taxed_differently` |
| C2 | `test_a_tax_is_not_charged_without_its_registration`, `test_a_seller_registered_for_nothing_charges_nothing` |
| C3 | `test_order_total_is_subtotal_plus_tax`, `test_quebec_client_is_charged_gst_and_qst`, `test_ontario_client_is_charged_one_hst_line`, `test_alberta_client_is_charged_gst_only` |
| C4 | `test_no_province_charges_nothing`, `test_an_unrecognised_province_charges_nothing` |
| C5 | `test_status_explains_why_nothing_was_charged`, `test_tax_status_is_ok_when_tax_applies`, `test_tax_status_flags_a_client_with_no_province`, `test_tax_status_flags_an_unrecognised_province`, `test_tax_status_flags_a_missing_registration` |
| C6 | `test_amounts_are_rounded_to_the_cent` |
| C7 | `test_each_tax_is_computed_on_the_subtotal_not_compounded` |
| C8 | `test_rate_percent_is_printable` |
| C9 | *(by construction — `taxes_for` takes one subtotal; see Z2)* |
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
| F12 | — gap — *(the "issued before freezing existed" branch of `amounts_for` has no direct test)* |
| F13 | `test_a_snapshot_with_a_blank_name_is_not_treated_as_frozen` |
| F14 | `test_a_draft_reads_seller_details_live`, `test_an_issued_invoice_ignores_later_seller_changes` |
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
| D11 | `test_tax_collected_sums_the_frozen_rows`, `test_tax_collected_excludes_voided_invoices`, `test_tax_collected_is_scoped_to_the_tenant` — gap: the `since`/`until` window has no test |
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
| A6 | `test_tax_collected_is_scoped_to_the_tenant`, `test_invoiced_subject_ids` — gap: `POST /invoices/<id>/status` against another tenant's invoice has no 404 test |
| U1 | — gap — *(print/no-print markup; manually verified in the browser only)* |
| U2 | — gap — *(client-side)* |
| U3 | `test_the_order_page_warns_when_tax_cannot_be_calculated` (host's order page) — gap: the invoice page's own draft-only warning is untested |
| U4 | `test_paid_cannot_be_set_by_hand` |
| U5 | — gap — |
| U6 | — gap — |
| U7 | `test_the_invoice_page_shows_the_tax_breakdown` |
| U8 | `test_the_invoice_list_totals_are_tax_inclusive` (the Outstanding total) — gap: the "not invoiced yet" list itself is untested |
| U9 | — gap — *(CSS)* |
| U10 | — gap — |
| U11 | — gap — *(would need a template scan like `test_billing_boundary.py` does for Python)* |
| Z1–Z10 | *(non-requirements — nothing to test)* |

### Known inconsistency

`analytics()` in `app.py` computes `invoicing.tax_collected(company_id)` and
passes it to `analytics.html`, which **doesn't render it**. So the query runs
on every analytics page load and the figure is never shown — D11 is
implemented and tested at the service layer, but the remittance report it
exists for isn't on the page yet. Either render it or stop computing it;
`docs/roadmap.md`'s "No tax-collected report" gap is describing this half-finished
state.

Everything marked "manually verified in the browser only" was exercised by
hand during development but has no regression protection — a future change to
that markup or CSS could silently break it and the suite would stay green.
Closing the "— gap —" rows is the obvious next step if this module gets
touched again; **F12, M3 and A6 are the ones worth closing first**, since each
guards money or a tenant boundary rather than appearance.
