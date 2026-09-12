# E2E tests (Playwright)

End-to-end tests for the leather_orders app, driving a real browser against
a real (but disposable) copy of the app. Separate Node/TypeScript project
from the Python `tests/` (pytest) suite — the two test different things and
don't share tooling.

## First-time setup

```bash
cd e2e
npm install
npx playwright install --with-deps   # downloads the actual browser binaries
```

You need the Python venv already set up at the repo root (`../.venv`) with
`pip install -r requirements.txt` done — the test harness runs the real
Flask app, it doesn't reimplement anything.

**If `playwright install` fails with `EPERM: operation not permitted` on a
`.exe` (Firefox or WebKit specifically)**: something on the machine —
antivirus, endpoint protection, a locked-down sandbox — is blocking the
write of a new executable file. This isn't a Playwright or project problem;
delete the half-written browser folder under
`%LOCALAPPDATA%\ms-playwright\` (its `INSTALLATION_COMPLETE` marker can be
present even when the actual binary is missing, which makes a plain rerun
silently skip re-downloading it) and either allow the exception or install
from a machine/account without that restriction. Chromium is unaffected by
this in practice and is enough to develop against day-to-day.

## Running

```bash
npm test                  # headless, all browsers (chromium/firefox/webkit)
npm run test:headed       # watch it happen in a real window
npm run test:ui           # Playwright's interactive UI mode — great for learning
npx playwright test --project=chromium         # one browser only
npx playwright test timeline.spec.ts           # one file
npx playwright test -g "rush order"            # one test by name
```

Every run:
1. Deletes and recreates a scratch SQLite database (`e2e/.tmp/e2e_test.db`).
2. Seeds it with a small, known dataset (`seed/e2e-data.json`) via
   `seed/seed_e2e_data.py` — six clients, a handful of orders covering
   each status, one rush order, one cancelled order, one delivered order
   far enough in the past to be off the timeline's default window.
3. Starts Flask against that database on `http://127.0.0.1:5000`
   (override with `E2E_PORT`).
4. Logs in once as the seeded admin and saves that session, so individual
   tests don't each pay for a login round-trip (see `fixtures/auth.fixture.ts`).
5. Runs the tests.
6. Stops Flask and deletes the scratch database (skip with `E2E_KEEP_DB=1`
   if a test failed and you want to inspect what was actually in it).

See `.env.example` for every override (pointing at an already-running
instance instead, a different fixture file, viewport size, video capture
mode, headed/headless, etc.) — copy it to `.env` to persist your own choices.

## Reports

```bash
npm run report             # Playwright's own HTML report (opens automatically)
npm run allure:generate    # builds an Allure report from the last run
npm run allure:open        # opens it
# or, in one step:
npm run allure:serve
```

Both reporters are wired up in `playwright.config.ts` and run on every
`npm test` — Playwright's HTML report is the fast local-debugging one
(inline traces, screenshots, videos), Allure is the one worth generating
when you want to archive a run or show someone else, browse by feature
area, or track history across runs. This uses the real Allure 3 report
(the `allure` npm package, pure JS, no JVM needed) — not the older
`allure-commandline` package, which still produces the Allure 2 report
format under the hood.

### Annotations — feature, description, step

Every real test calls two `allure-js-commons` functions as its first
lines:

```typescript
import { feature, description } from "allure-js-commons";

test("hiding a status updates the stat cards", async ({ adminPage }) => {
  await feature("Orders");
  await description("LST4: hiding a status recomputes the order-count and balance-due cards from the visible rows.");
  // ...
});
```

- **`feature(...)`** — the business domain (`"Timeline"`, `"Orders"`,
  `"Clients"`, `"Auth"`, `"Navigation"`, and so on as more areas get
  covered). This feeds Allure's **Behaviors** tab, which is what actually
  answers "show me every test that touches Inventory" — grouping by spec
  file wouldn't, since files are named after views, not business domains,
  and some domains (Auth) don't map to one file at all.
- **`description(...)`** — one sentence naming the REQUIREMENTS.md rule id
  and what's actually being checked, so the *why* shows up in the report
  next to the result instead of living only in a source comment.
- You get a **Suites** tab for free, grouped by spec file and
  `describe()` block, with no extra code — `playwright.config.ts`'s
  `suiteTitle: true` reporter option fills that in automatically. Feature
  tags and the automatic suite grouping are two independent views over the
  same results; both are worth having.

Every **Page Object method that performs an action or assertion** wraps
its body in a `step(...)` (see any method in `pages/`), so a test reads as
a narrative in the report — "Go to the Timeline" → "Toggle the 'confirmed'
status filter" → "Expect 'confirmed' to be filtered off" — instead of one
opaque pass/fail. Playwright's own `expect(...)` calls show up as steps
automatically, with no extra code, and nest naturally under whichever POM
step called them. Locator-builder methods that don't touch the page
(`rowByItem`, `legendButton`, and the like) are deliberately left
unwrapped — they don't do anything by themselves, so a step around one
would just be noise with no timing or outcome to show. Follow the same
pattern for any new POM method: wrap it in `step("...")` if it clicks,
fills, navigates, or asserts; leave it plain if it only builds and returns
a `Locator`.

### A concurrency note if you tune `E2E_WORKERS`

`playwright.config.ts` caps local runs at 4 workers by default
(`E2E_WORKERS` to change it), and starts the auto-managed Flask server
with `threaded=True`. Both exist because Flask's dev server handling one
request at a time turned into real `page.goto` timeouts under Playwright's
default (CPU-core-based) worker count — confirmed by the same run being
100% reliable at 2 workers and flaky at 8, independent of any test logic.
Threading the server helped but didn't fully remove the ceiling in every
environment; 4 is a conservative default that's been reliable across
repeated runs here. If your machine handles more, raise `E2E_WORKERS` and
see — there's nothing else tying it to 4.

## Project layout

```
e2e/
  playwright.config.ts   # browsers, viewport, video, reporters, timeouts
  global-setup.ts        # seed DB -> start Flask -> log in once -> save session
  global-teardown.ts     # stop Flask, delete the scratch DB
  lib/env.ts             # every env-var/path decision, in one place
  seed/
    e2e-data.json         # the dataset itself — edit this to change fixtures
    seed_e2e_data.py       # writes e2e-data.json into the scratch DB via the
                            # app's own models, not raw SQL
  fixtures/
    testData.ts            # typed TS view of e2e-data.json, plus date/money
                            # helpers so assertions don't hardcode magic values
    auth.fixture.ts         # the custom `test`/`expect` every spec imports
  pages/                    # Page Object Models — one class per view,
                             # wrapping selectors so spec files read as
                             # plain English rather than repeating CSS
  tests/                    # the actual spec files
```

## Writing a new test

Import from the auth fixture, not from `@playwright/test` directly, so you
get `adminPage` (and `freshUserPage`, for the one forced-password-change
scenario):

```typescript
import { test, expect } from "../fixtures/auth.fixture";
import { TimelinePage } from "../pages/TimelinePage";

test("does a thing", async ({ adminPage }) => {
  const timeline = new TimelinePage(adminPage);
  await timeline.goto();
  // ...
});
```

If the view you're testing doesn't have a page object yet, add one under
`pages/` rather than putting raw selectors in the spec file — that's what
keeps a future markup change a one-file fix instead of a find-and-replace
across every spec that touches that page.

### Fixture data

`seed/e2e-data.json` is the single source of truth for what's in the
database, read by both the Python seed script and (via `fixtures/testData.ts`)
the TypeScript specs — so `clientByKey("ada")` in a test and the row the
Python script actually inserted can never drift into two different
datasets. Add a new client/order there rather than creating one through the
UI inside a test, unless the test is specifically about creation.

**Dates are day-offsets from "today", not fixed calendar dates** — same
convention the app's own `sample_data.py` uses, and for the same reason
(hard rule 16 in the root CLAUDE.md): a fixed date means the fixture slowly
walks out of whatever window a view like the Timeline actually shows. If
you add an order, keep in mind the timeline's window starts on "the Sunday
on or before today" (TL2) — an offset that's small and negative (roughly
-1 to -6) can fall just outside that window depending on which day of the
week the suite happens to run. See the comment on `margaret-ready-order` in
`e2e-data.json` for a worked example of the edge case.

### What's covered

Every spec runs against real selectors pulled from the actual templates;
nothing is stubbed. Each one exists because it covers something a pytest
route test structurally can't see:

- **`timeline.spec.ts`, `orders-list.spec.ts`, `clients-list.spec.ts`** —
  the `localStorage` filters and sorts (TL7–TL9, LST4–LST5), the timeline's
  `<dialog>` modals, and hiding a client end to end (CL17–CL19).
- **`modals-and-tabs.spec.ts`** — both detail pages' tabs carrying
  `return_to` through each switch (MOD3/MOD4), and hard rule 9 across two
  real forms posting to the same order.
- **`settings-drag-and-drop.spec.ts`** — the drag itself (CL8, LST10): the
  `dragend` handler that reads the DOM and builds the reorder payload,
  which nothing else exercises. Uses dispatched HTML5 drag events, since
  Playwright's `dragTo()` doesn't reliably produce the sequence these lists
  listen for.
- **`sender-rules.spec.ts`** — the field-mapping picker's `<optgroup>`
  structure (F-23), which is opened and closed by two separate
  `{% if group %}` conditionals in the template: a mismatched pair renders
  invalid HTML that still contains every option, so a substring check on
  the response body passes either way and only a parsed DOM catches it.
  Plus `required` on the address input (R-22) — browser-enforced, where
  the server's own guard is a different code path — and both rule forms
  driven as a person drives them (R-21, R-24), since a convert rule's card
  carries three forms and two of them hold an input of the same name.
  **No layout assertion here on purpose**; see the note at the top of the
  file for why one was written and then removed.
- **`order-form-errors.spec.ts`** — a refused save explaining itself where a
  person is looking (OR13): messages under the fields, everything typed
  kept, the new-client fields coming back revealed, and a timeline quick
  edit's dialog reopening by itself.
- **`auth.spec.ts`, `mobile-nav.spec.ts`** — sign-in, the forced password
  change, and the hamburger nav below 680px (CO5a).

Tests that change saved state (hiding a client, reordering a list, a
column's visibility) put it back in a `finally`, because every spec shares
one server and one database with every other, in parallel. The
`order-form-errors` tests don't need to: a refused submission writes
nothing.

Still not covered, and the obvious next targets: the inventory list's
client-side filters and the Materials tab's live cost estimate
(`inventory/REQUIREMENTS.md` U2, U4, U8).

### One gotcha worth knowing before you add more

The `freshUserPage` fixture logs in as the one seeded user who still has
`must_change_password=True` (CO4h). If a test actually completes that
password-change form, it consumes the seeded password for every test after
it in the same run. Either keep that flow to a single test, or seed a
second "fresh" user in `e2e-data.json` if you need more than one.
