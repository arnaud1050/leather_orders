# Tests

> Part of the `leather_orders` app — see the root [CLAUDE.md](../CLAUDE.md) for orientation.

`pytest`. Most of the suite covers `communications/`; the rest covers the money
(tax, invoice numbering, the snapshot rules) and the `inventory/` module.
The core app is exercised through `tests/test_core_app.py` (client/order edit
rules, payments, the timeline window and analytics) and `tests/test_routes.py`;
`tests/test_client_orders_tab.py` covers one narrow slice of the client page (the
Orders tab's table — its status pill, its fixed columns, sorting, and the
invoice-or-dash cell), not the page as a whole. The Settings letterhead POST
routes are `tests/test_settings_company.py`, order-line add/delete is
`tests/test_order_lines.py`, and the two option lists' hide-vs-hard-delete
guard is in `tests/test_settings_options.py`.

**`tests/test_change_password.py`** covers Settings → Account (`CO4a`–`CO4c`).
Every rejection case asserts on the stored hash rather than on the message
that came back — a redirect carrying the right words says nothing about what
was written — and one test goes the whole way round through `/login` to prove
the new password is the one that actually authenticates.

**`tests/test_admin.py`** defends the platform admin area
(`admin/REQUIREMENTS.md` `PA1`–`PA30`): who may reach `/admin` (every mutating
route checked individually, since the guard on a list page is easy to
remember and the guard on ten POSTs is not — plus all three top-level pages'
GET routes, now that Companies, Admin Users and Settings are separate pages
sharing `_admin_nav.html`), that a company provisioned there gets exactly what
`seed_if_empty()` builds and no sample data, the platform-wide uniqueness of
email, deactivation of both a company and a user, and impersonation.

It also defends the **announcement banner** (`PA26`–`PA30`): that it shows on
a tenant page, a staff page, and — the point of the feature —
`/login` and `/privacy` while signed out; that turning it on with a blank
message is refused outright while turning it off with one blank just clears
the draft (nothing lost the next time the same maintenance window comes
around); and that the message is rendered escaped, checked with a literal
`<script>` tag that must survive as visible text, never as markup. The
`get_platform_settings()` singleton is checked directly too — two calls in a
row must return the same row, not create a second one.

It also pins the **staff/tenant split**: that a platform admin has no company,
that every tenant route redirects them back to `/admin` (parametrised over the
nine of them, since the guard is one hook and the thing worth checking is its
reach), and that no route exists to promote a tenant user — the `404` in
`test_there_is_no_route_to_promote_a_tenant_user` is the assertion, because the
absence *is* the rule. Note `platform_admin` in `conftest.py` deliberately
depends on `app` rather than `company`: a fixture that gave staff a studio
would re-create the arrangement the model forbids, and every access test built
on it would be testing fiction.

The load-bearing test is
`test_admin_is_unreachable_while_impersonating`, checked from both sides —
the shortcut implementation (`current_user.is_platform_admin` instead of the
session key) passes whenever the impersonated user happens to hold the flag,
and fails open silently.

**`tests/test_user_migration.py`** covers the one migration in the project
that rebuilds a table rather than extending one — `username` out, `email` in
(`PA24`, `PA25`). Every fixture builds the *old* schema by hand; testing
against `create_all`'s current shape would prove nothing. It checks the
backfill, id preservation (communications' audit log has a foreign key into
`users`), the case-collision fallback, that the new unique index really
constrains, and that a second `run_migrations()` is a no-op.

**`tests/test_client_hiding.py`** defends `CL17`–`CL21`, and most of it is one
assertion from different angles: **hiding a client must not touch their
orders.** The load-bearing test is
`test_hiding_does_not_change_a_single_analytics_figure`, which diffs the whole
`/analytics` body either side of a hide — deliberately blunt, because the
failure it guards against is a figure quietly moving, and enumerating the
cards would only defend the ones somebody thought to list. The rest covers the
toggle (tenant scoping, login, `return_to`), the roster/archive split (`CL19`),
the new-order picker (`CL20`), and the come-back-on-new-mail rule (`CL21`) —
that last group living here rather than in `tests/test_lead_triage.py` even
though the code is in `communications/`, since the rule is the host's.

`tests/test_seeding.py` additionally covers `ensure_platform_admin()` (`CO9g`),
whose whole reason for existing is a case `seed_if_empty()` can't reach — a
database that already has a company, where seeding returns early and nobody
would otherwise be able to sign in to `/admin`. The one to keep is
`test_a_platform_admin_inside_a_company_is_repaired`, written against a real
lockout: a platform admin who still belonged to a company, whose company was
then deactivated, leaving an installation with the flag set on paper and
nobody able to reach `/admin`. It's the reason that function tests for a
*usable* admin (company-less and active) rather than for the flag — the
flag-only version steps aside from exactly the database that needs it.

**`tests/test_seeding.py`** defends the bootstrap/sample-data split (`CO9a`–
`CO9c`, hard rule 16): that `seed_if_empty()` creates a tenant and *no* clients,
orders, invoices or letterhead; that `sample_data.seed_sample_data()` inserts the
demo dataset but refuses a company that already has clients; and — by parsing
`app.py` and `models.py` with `ast`, the same trick `test_billing_boundary.py`
uses — that **neither imports `sample_data`**. That last one is the real
regression guard: a single import added for convenience would put ten fake
clients back into every production deployment, and no other test would notice.
The same file covers the relative-date rules (`CO9d`, `CO9e`) by seeding at a
pinned future date — 2029, 2031, 2033 — and asserting the timeline still has
past, current and upcoming orders, that no payment or invoice lands after the
seed day, and that nothing unstarted claims to be ready or delivered.

What's still thin: the Gmail API
wrapper (`communications/providers/gmail_provider.py`, substituted by
`tests/fakes.py` rather than run) and the PDF thumbnail path (needs `poppler`).

The four money files, and the single rule each exists to defend:

- **`tests/test_tax.py`** — every rate in `PROVINCE_TAXES` pinned against a
  **second, independently written copy of the CRA's table**. This is the point:
  a test that imported the constant it checks would pass no matter what the
  constant said. Also covers the two gating rules — tax follows the *client's*
  province, and a tax is only charged when the company holds its registration.
- **`tests/test_invoicing.py`** — numbering (per company, per year, derived from
  the highest number) and freezing. The rule: *once an invoice leaves draft,
  nothing may change what it says* — not company settings, not the client's
  province, not the order's line items. Each of those has a test, plus its
  mirror proving a **draft** still tracks them.
- **`tests/test_invoice_routes.py`** — the freeze-on-transition rule, which
  lives in `app.py` rather than the models: `set_invoice_status()` compares the
  status before and after, so re-saving a sent invoice can't re-stamp it.
- **`tests/test_addresses.py`** — rendering, and the free-text→structured
  migration. Its sharpest test is that an unparseable address does **not** get a
  guessed province, since that would silently change the tax charged.
- **`tests/test_billing_boundary.py`** — parses every file under `billing/` and
  fails on an import that would tie the module to this project. Without it,
  "self-contained" is an intention rather than a property: one `from models
  import Order` added for convenience would go unnoticed by every other test.

- **`tests/test_mail_attachments.py`** — attaching an order document to an
  outgoing email, which spans three parts none of which may import the
  others: `documents/` owns the bytes, `communications/` owns the sending,
  and the two hooks in `app.py` are the only place an Order, a Document and
  an EmailMessage meet. So the tests are aimed at the seam rather than at
  either module: what the picker offers (orders with documents, grouped by
  type; "no orders" told apart from "no documents"), and what happens to an
  id that shouldn't resolve. The sharpest one posts **another tenant's**
  `document_id` into `/mail/send` and asserts both halves — their file
  doesn't go out, *and* the message still does. Dropping rather than
  raising is the rule (`SEND-9`), and a test that only checked the first
  half would pass against code that lost the user's draft.

- **`tests/test_ai_boundary.py`** — the same trick for `ai/`, which claims a
  stricter boundary than any other module: it never sees a host model at all,
  since it's wired to three different host concepts (orders, documents, mail
  threads) and a foreign key to any one would tie it to all three. It also
  pins the *one* allowance — the root `crypto.py` — by asserting that file
  imports nothing but the standard library and `cryptography`. That assertion
  is what stops the allowance quietly widening into "modules may import host
  helpers".
- **`tests/test_ai_settings.py`** — API keys at rest, and the two form rules
  that would otherwise destroy data. Its sharpest test reads the raw column
  and asserts the key isn't in it: a round-trip test passes just as happily
  against a plaintext column. The others cover "a blank key field means keep
  the saved key" (the input is always rendered blank, so treating blank as a
  clear would wipe the key on every prompt edit) and the page still rendering
  after an encryption-key rotation, when the value can no longer be decrypted.

- **`tests/test_ai_reply.py`** — reply suggestions, with **two levels of
  vendor double** and a reason for each. The `vendor` fixture replaces
  `openai_client.generate_reply` wholesale, which is right for asserting on
  what the prompt *contained*; the failure tests patch `openai.OpenAI`
  itself, one layer lower, because the translation from vendor exception to
  readable sentence lives inside the function the first double replaces.
  The first version of these tests got that wrong and asserted on the
  double's own behaviour instead of the code's. It also reads the **real**
  `openai` exception classes and checks their status codes still map to the
  advice intended — the translator is duck-typed on purpose (importing those
  classes would defeat the lazy import), and a library rename would
  otherwise silently downgrade every message to the generic one.

- **`tests/test_ai_render.py`** — image rendering, with the same two-level
  vendor doubling. Its load-bearing assertions are the ones about what a
  *draft* is: a rendered image must not appear in the order's Documents
  area or consume the company's 1GB quota until someone saves it, and
  saving must go through `documents.services.upload()` so validation and
  quota apply to a vendor's image exactly as to a dragged-in one. It also
  pins the two pre-flight refusals (wrong type, oversized source) as
  happening *before* the vendor call — those are the cases where getting it
  wrong costs money rather than correctness, and no other test would notice.

`tests/test_inventory.py` covers the newer `inventory/` module on the same
per-file-per-rule principle, even though it isn't one of the four money files
above — its rule is the opposite one: **cost-tracking must never touch
billing.** Alongside stock decrement/restore/delta-adjustment, snapshot
pricing surviving a later price change on the item, hide-don't-delete on
both `InventoryType` and `InventoryItem`, and tenant isolation on every
route, it asserts `Order.total` is unchanged after adding materials or
Others — the one assertion that would catch this module quietly growing a
billing side effect.

Checked against four deliberate regressions (wrong Nova Scotia rate; dropped
registration gating; freezing on every save instead of the transition; issued
orders reading live tax instead of frozen). Each was caught — 2, 4, 1 and 3
failures respectively. If you weaken one of those protections and the suite
stays green, the test lied; find out why.

```bash
python -m pytest
```

`tests/conftest.py` sets `DATABASE_URL` to a temp file **before importing
`app`**, and `_app` asserts on it. That is not a style choice: `app.py` runs
`db.create_all()` at module level, so Flask-SQLAlchemy builds and caches its
engine during the import, and a script that sets `SQLALCHEMY_DATABASE_URI`
*afterwards* silently writes to the real `data/atelier.db`. This bit us once.

`_app` also registers a `before_request` hook that pops Flask-Login's cached
user off `g`. That cache is scoped to the *app context*, and a real deployment
pushes a fresh one per request — but the `app` fixture holds a single app
context open for a whole test and Flask reuses it rather than pushing another.
Without the hook, the first request's identity sticks, so a test driving two
clients (a platform admin and the tenant user they're acting on, say) silently
runs both as whoever signed in first. It reads as a bug in the code under test,
which is what makes it worth preventing centrally. `tests/test_admin.py` is
the file that needs it.

Each test drops and recreates the schema. A rollback-per-test fixture was tried
first and doesn't work here — `send_email()` and `create_event()` deliberately
commit (once Gmail has accepted a message it has really gone out, so the local
record must not be lost by a caller rolling back), and those commits escaped the
wrapping transaction and leaked rows between tests.

**Nothing touches Google.** `tests/fakes.py` provides `FakeEmailProvider` /
`FakeCalendarProvider` and a `fake_providers()` context manager that patches the
*registry lookup* plus the names already imported into calling modules
(`from x import y` binds at import time, so patching only the registry would
miss every existing call site). The fakes record what they were asked to do —
`SENT_LOG`, `CALENDAR_LOG`, `FETCH_LOG` — which is how the suite asserts on
things that exist only as an outbound API call, like the `threadId` and
`In-Reply-To` on a reply.

What's covered, in rough order of how much it matters:

- **Tenant isolation** — every service function and route is tested with a
  second company's id; cross-tenant reads 404, cross-tenant matching finds
  nothing.
- **CSRF** — every unsafe route rejects a missing and a wrong token, and
  accepts a valid one.
- **OAuth state** — missing, mismatched and replayed callbacks are all refused;
  a callback whose session company differs from `current_user`'s is refused too.
- **Token encryption** — ciphertext isn't the plaintext, is non-deterministic,
  fails typed on a rotated key, and rejects tampering.
- **Sync idempotence** — running the same window twice creates nothing new.
- **Gmail parsing** — nested MIME trees, unpadded base64, quoted commas in
  address headers, timezone conversion, sent-vs-received direction.
- **XSS** — a `<script>` in a message body or subject renders escaped;
  attachments serve as `application/octet-stream`, never inline.
- **Path traversal** — a `../../` attachment filename can't escape the
  company's directory, on write or on read.

The suite was checked against four deliberate regressions (dropping the tenant
filter on account lookup, dropping it on client matching, disabling the CSRF
comparison, and ignoring `keep_unmatched`); 15 tests failed. If you change one
of those protections and the suite stays green, the test lied — find out why.

