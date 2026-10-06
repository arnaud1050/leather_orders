# Billing module (`billing/`)

> Part of the `leather_orders` app — see the root [CLAUDE.md](../CLAUDE.md) for
> stack, conventions and design language.

**This file is structure: why the module is shaped this way, and what lives
where. [REQUIREMENTS.md](REQUIREMENTS.md) is behaviour** — tax rates and gating,
numbering, the freeze-at-issue contract, derived money, migrations, tenant
isolation and UI, each a numbered checkable statement with a test-coverage map
marking the gaps. Same arrangement as `communications/` and `inventory/`.
Changing behaviour means changing REQUIREMENTS and the code in the same commit.

Invoicing, billing and Canadian sales tax, extracted into a **self-contained
module** on the same terms as `communications/`: its own tables, migrations,
blueprint and templates, and it never imports a host model.

**The rule that keeps it modular:** the rest of the app talks to
`billing.services`, `billing.tax` and `billing.documents`, and nothing deeper.
`tests/test_billing_boundary.py` enforces this by parsing every file under
`billing/` — `from models import db` (the shared SQLAlchemy handle) is the only
import from outside the module that's allowed, and `tax.py` may import nothing
but the standard library.

## Why this was harder than extracting communications

Nothing in the app depends on an `EmailThread`. Plenty depends on
`Order.total`, which is tax-inclusive and read by the timeline, analytics, both
list pages and the invoice list. Extracting naively produces a cycle: billing
would import `Order`, while `Order.total` calls `taxes_for`.

The seam that resolves it:

- **Billing never sees an Order.** It asks for a `Billable` (lines, buyer
  details, tax province, payments) — a plain dataclass in `documents.py`.
- **`billing_adapter.py` at the project root** is the only file that knows a
  Billable is built from an Order. It lives outside `billing/` deliberately:
  everything under `billing/` should survive a copy-paste into another
  codebase, and this wouldn't.
- **`Order.total` delegates into billing** (`Order._amounts`), importing
  `billing.services` *inside* the property because `billing.models` imports
  `db` back out of `models.py` — a top-level import would be circular.
- **`Invoice.subject_id`**, not `order_id`: the module doesn't know what it
  bills for. The foreign key target is `config.SUBJECT_FK` — porting to a
  project that invoices jobs or subscriptions is that one line, which is a
  better trade than an untyped id with no referential integrity.

## Layers

- **`tax.py`** — `PROVINCE_TAXES`, `TaxRule`, `TaxLine`, `taxes_for`,
  `status_for`, `PROVINCES`, `normalize_province`. Pure data plus pure
  functions; usable from a script or another framework as-is. `taxes_for`
  takes a *mapping* of registrations rather than a Company, which is what
  makes it ORM-free. `normalize_province` turns whatever a human or a web form
  wrote into a code and **returns `None` for anything it can't resolve**
  (`R10`, `R11`) — the province column selects the rate, so a stored "Quebec"
  matches no row in `PROVINCE_TAXES` and silently charges nothing (and on
  Postgres the `VARCHAR(2)` would truncate or reject it too; SQLite keeps the
  whole string, so the rule can't lean on the backend). Its lookup is
  built *from* `PROVINCES`, so codes and names can't drift from the rate table.
  It's re-exported by the host's `models.py` because `communications/` needs it
  for contact-form mapping and must not import a sibling module.
  `unicodedata` (for folding "Québec") is the only addition to this file's
  stdlib-only allowlist in `test_billing_boundary.py`.
- **`documents.py`** — `Billable`, `LineItem`, `PaymentRecord`, `PartyDetails`,
  `IssuerDetails`, `InvoiceDocument`, `Discount`, plus `format_address` (the
  host imports that one back for `Client.formatted_address`) and
  `clean_discount`. No database. **`subtotal` means net of the discount
  everywhere in this module** — it's what tax is charged on (`DS4`);
  `items_total` is the lines at full price. The host's `Order.subtotal` is
  the exception: it predates discounts and stays the lines at full price.
- **`models.py`** — `BillingProfile` (the letterhead, one row per tenant),
  `Invoice`, `InvoiceTaxLine`, `next_invoice_number`.
- **`services/invoicing.py`** — the public API. Every function takes
  `company_id` first and filters on it; a boundary test asserts that.
  `amounts_for` is the one that decides frozen-vs-live; `document_for` builds
  what a template renders.
- **`pdf.py`** — the invoice as a server-rendered PDF (WeasyPrint), from
  the standalone templates under `templates/billing/pdf/`. Split into
  `render_html` (runs anywhere, and is what most tests pin) and `render_pdf`
  (needs WeasyPrint). **WeasyPrint is a lazy import and doesn't load on the
  Windows dev machine** — it needs Pango, which both Docker images install
  along with `harfbuzz-subset` and the Inter font. Locally the invoice page
  falls back to the browser's print dialog, so to see a real PDF, build the
  image. The renderer fetches no URLs at all, which is why the templates
  carry their CSS inline. **Presentation is live, content is frozen**
  (`PD12`): the freeze contract is about what an invoice says, not how it
  looks.
- **Branding** — the look a tenant picks under Settings → Invoicing: a
  layout (`classic` or `banded`, listed in `config.INVOICE_TEMPLATES`) and
  one colour, stored as nullable columns on `BillingProfile` and
  handed to templates as a `Branding` dataclass (`documents.py`). **A
  template must use `branding.primary` / `.on_primary`, never
  the raw stored strings** — those properties are the only thing standing
  between a database value and the stylesheet (`BR3`). `banded.html` is a
  port of the standalone `billing` project's invoice (see its `CLAUDE.md`),
  not a copy: that one hardcodes its issuer and pins blocks to the page
  bottom. Adding a layout means a key in `config.INVOICE_TEMPLATES`, a file
  in `pdf.TEMPLATES`, and nothing else — a test fails if the two drift, and
  every document test then runs against the new layout automatically.
- **The footer and the payment instructions use WeasyPrint's paged-media
  features, on purpose.** The footer is a running element
  (`position: running(pagefoot)`) shown in the bottom margin box, so it
  repeats per page, carries the page counter, and can't overlap content;
  the closing block — notes in a fixed three-line box, then payment
  instructions — is a footnote (`float: footnote`), which puts it at the
  foot of the last page and moves it on rather than letting items run
  underneath — the bug the original absolutely-positioned layout had. The
  band fills the bottom margin exactly (14mm), so it reaches the sheet's
  bottom edge; Classic's runs out through its side margins with negative
  margins. Two dead ends, so nobody retries them: coloured corner margin
  boxes leave seams, and WeasyPrint ignores `background-size` on a page
  background (and crashes on `calc()` there). **Keep the closing block's
  markup free of whitespace between tags** (`PD8a`); `tests/test_invoice_pdf.py` checks the real rendered text with
  `pdftotext`, but only inside the Docker image.
- **The logo isn't billing's any more.** It was (`logos.py`, under
  `data/billing_logos/`) until it became the studio's and moved to
  [brand/](../brand/CLAUDE.md) on 2026-10-06. Billing now only embeds one:
  the host registers a source with `invoicing.set_logo_source(fn)` (PNG
  bytes per company — app.py passes `brand.logo_png`), and
  `invoicing.branding_for` puts it on `Branding` as a `data:` URI (`L7`) —
  **use that, not `profile.branding`, wherever a PDF is rendered**, or the
  logo silently goes missing. `billing_profiles.logo_filename` and
  `config.LOGO_DIR` remain only for brand's one-time migration.
- **The URL fetcher is WeasyPrint-version-specific.** `pdf._fetcher` is a
  `URLFetcher` subclass, the API of WeasyPrint 70; older releases took a
  plain function returning a dict, and 70 rejects that with an assertion
  only when something is actually fetched — which is why it went unnoticed
  until the logo was the first thing to fetch. `requirements.txt` pins
  `>=70`. After upgrading WeasyPrint, run `tests/test_invoice_pdf.py` and
  `tests/test_invoice_logo.py` **inside the image**; locally they skip.
- **`routes.py`** — a blueprint the host opts into via `register(app, ...)`,
  passing four hooks it can't know: `resolve_billable`, `uninvoiced`,
  `display_name`, `back_label`. A host wanting its own UI ignores it.

## What moved off `Company`

The letterhead — prefix, address, GST/PST/QST/NEQ, payment instructions — now
lives on `BillingProfile`, keyed by `company_id`, the way `EmailAccount` does.
`Company` kept `name` and `timezone` only: a company is called the same thing
whether or not it invoices. `billing/migrations.py` copies the old columns
across and drops them, and also rescues a pre-split free-text
`companies.address` (whole, into `street` — guessing a province would change
the tax charged). `invoices.order_id` is renamed to `subject_id` there too.

`profile_for(company_id)` creates an empty profile on first use, so a host
never has to special-case "not set up yet" — an empty profile just prints no
letterhead.

