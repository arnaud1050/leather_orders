# Tests

> Part of the `leather_orders` app — see the root [CLAUDE.md](../CLAUDE.md) for orientation.

`pytest`. Most of the suite covers `communications/`; the rest covers the money
(tax, invoice numbering, the snapshot rules) and the `inventory/` module.
Timeline, orders and analytics are still untested; `tests/test_client_orders_tab.py`
covers one narrow slice of the client page (the Orders tab's table — its status
pill, its fixed columns, sorting, and the invoice-or-dash cell),
not the page as a whole.

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

