# CLAUDE.md

Guidance for Claude Code (or a future me) working in this repository.

**This file is the always-loaded orientation layer — deliberately short.** The
detail lives in the files indexed below. Each self-contained module has its own
`CLAUDE.md` (the *why*) next to its own `REQUIREMENTS.md` (the *what must hold
true*, as numbered checkable rules). A module's `CLAUDE.md` is picked up
automatically when working inside that directory; the `docs/` files are read on
demand — open the one the index points at rather than guessing from memory.

## Where things are documented

| Looking for | Read |
| --- | --- |
| Tables, columns, computed properties, tax, invoices, seeding, schema migrations | [docs/data-model.md](docs/data-model.md) |
| Timeline, calendar, lists, settings, analytics, modals, client/order pages | [docs/views.md](docs/views.md) |
| **Anything styling — palette, tokens, fonts, icons, spacing, what's been tried and rejected** | [docs/design.md](docs/design.md) |
| Test suite layout, what's covered, the `conftest.py` gotcha | [docs/testing.md](docs/testing.md) |
| Docker, gunicorn, the two deployments, env vars | [docs/deployment.md](docs/deployment.md) |
| Known gaps, next steps, things deliberately not built | [docs/roadmap.md](docs/roadmap.md) |
| **The order lifecycle in plain language, for the studio rather than for us** — standalone-styled page, served in-app at `/help/orders` and still a single file if saved or handed over | [templates/help/order_lifecycle.html](templates/help/order_lifecycle.html) |
| **The client lifecycle, same audience and same format** — enquiry → on the client list → has ordered, what the app does with each kind of incoming mail, and what hiding a client does and doesn't touch — served in-app at `/help/clients` | [templates/help/client_lifecycle.html](templates/help/client_lifecycle.html) |
| Core-app rules as checkable statements | [REQUIREMENTS.md](REQUIREMENTS.md) |
| Invoicing, Canadian sales tax, the freeze-at-issue contract | [billing/CLAUDE.md](billing/CLAUDE.md), [billing/REQUIREMENTS.md](billing/REQUIREMENTS.md) |
| Gmail + Google Calendar, leads, sender rules, sync | [communications/CLAUDE.md](communications/CLAUDE.md), [communications/REQUIREMENTS.md](communications/REQUIREMENTS.md) |
| Materials, stock, units, order material costs | [inventory/CLAUDE.md](inventory/CLAUDE.md), [inventory/REQUIREMENTS.md](inventory/REQUIREMENTS.md) |
| Order document upload, storage, thumbnails, document types | [documents/CLAUDE.md](documents/CLAUDE.md), [documents/REQUIREMENTS.md](documents/REQUIREMENTS.md) |
| AI reply suggestions, image rendering, vendor API keys, prompts | [ai/CLAUDE.md](ai/CLAUDE.md), [ai/REQUIREMENTS.md](ai/REQUIREMENTS.md) |
| **Platform admin — provisioning companies and users, impersonation, why email replaced usernames.** A host blueprint, *not* a module: it imports host models on purpose | [admin/CLAUDE.md](admin/CLAUDE.md), [admin/REQUIREMENTS.md](admin/REQUIREMENTS.md) |
| **Feature-usage events — what's tracked, why recording can never break the action, adding an event**, and the `/admin/usage` page that reads them | [usage/CLAUDE.md](usage/CLAUDE.md), [usage/REQUIREMENTS.md](usage/REQUIREMENTS.md) |
| **The studio's logo** — shared by invoices and catalog mode; upload, storage, the light/dark tone read from its pixels, the move out of billing | [brand/CLAUDE.md](brand/CLAUDE.md), [brand/REQUIREMENTS.md](brand/REQUIREMENTS.md) |
| **Per-company features — the parts sold separately**, switched on per company from `/admin`; `is_enabled()`, `require()`, `has_feature()` | [features/CLAUDE.md](features/CLAUDE.md), [features/REQUIREMENTS.md](features/REQUIREMENTS.md) |
| **Showcase — the portfolio of finished pieces** (sold per company): pieces, photos with metadata stripped, the order page's Showcase tab and its reminder, Settings → Showcase, catalog mode and its kiosk links (`/k/<token>/`, no sign-in) | [showcase/CLAUDE.md](showcase/CLAUDE.md), [showcase/REQUIREMENTS.md](showcase/REQUIREMENTS.md) |

**Changing behaviour means changing the matching `REQUIREMENTS.md` rule in the
same commit.** If a rule and the code disagree, one of them is a bug.

**The two `templates/help/*_lifecycle.html` pages are bound by that same
rule.** They're served at `/help/orders` and `/help/clients`, linked only
from `base.html`'s footer — but being reachable doesn't mean anything checks
their *content*: they're still the only docs
here with no test or importer that catches a lifecycle change breaking
nothing visible in them, it just turns the page into a lie. The order one has
already gone stale twice. Change `order_lifecycle.html` alongside the `OR1*`
rules and `client_lifecycle.html` alongside `CL17`–`CL21` (and the
`communications/` rules it describes), not afterwards; `OR1i` and `CL22` say
so in the specs themselves.

## Hard rules

The things that break quietly if they're not front of mind. Each is explained
where it's indexed above; this is the checklist, not the reasoning.

1. **Every query filters `current_user.company_id`.** `Company` is the tenant
   boundary and every existing query respects it — don't add one that skips it.
   **A user is a tenant user or platform staff, never both**: staff have no
   `company_id` at all and are kept out of every tenant route by one
   `before_request` hook in `app.py`. A null `company_id` never means "no
   filter" — it means "not allowed here". See [admin/CLAUDE.md](admin/CLAUDE.md).
2. **Server-rendered Jinja, no JS framework, no build step.** Ask before
   changing that.
3. **New routes go in `app.py`**, unless a self-contained module owns them.
   Ask before restructuring into a `routes/` package.
4. **Module boundaries are one-way.** The app talks to `billing.services`,
   `communications.services`, `inventory.services`, `ai.services` and
   `showcase.services` and nothing deeper; a module never imports host models
   (`tests/test_billing_boundary.py` and `tests/test_ai_boundary.py` enforce
   this for `billing/` and `ai/`, which may import only `db` from `models.py`
   — plus, for `ai/` and `communications/`, the root `crypto.py`, the one
   shared helper that depends on nothing of the app). Any module may also
   call `from usage import track`: the `usage` package root is just as
   dependency-free, and `usage.store` stays off limits
   (`tests/test_usage.py`). Likewise `import features`, which imports
   only `db` from the host (`tests/test_features.py`), and so does
   `brand/` (`tests/test_brand.py`) — though billing still doesn't import
   it: the logo reaches billing through `invoicing.set_logo_source`. `showcase/` is as
   strict as `ai/` (`tests/test_showcase.py`); `showcase_adapter.py` is its
   seam, like `billing_adapter.py`.
   **`admin/` is not a module and this rule doesn't apply to it** — its
   subject matter *is* `Company` and `User`, so it imports them freely. It's
   a package only because `app.py` is long enough already. See
   [admin/CLAUDE.md](admin/CLAUDE.md).
5. **Design: no display font, no serif, no mono font, no brown/earthy accent
   colours.** All have been tried and explicitly reverted. Read
   [docs/design.md](docs/design.md) before any styling work.
6. **Exactly two delete conventions** — a trash icon inside a `.doc-list`, a
   button reading **"Delete"** inside a `.settings-source-list`. **Never a text
   "Remove" button.**
7. **Four badge weights, and only four**: grey = undecided, purple = worth
   knowing, amber = needs attention soon, red = broken. Amber was added with
   the inventory low-stock warning (`.nav-badge--low-stock`) — the one
   deliberate exception to what was "three, and only three". Don't add a
   fifth, and don't promote a count between them. See
   [docs/design.md](docs/design.md).
8. **Hide, don't delete**, for anything a company configures and historical
   records reference (`SourceOption`, `OrderType`, `InventoryType`,
   `InventoryUnit`, `DocumentType`) — hard delete only while `can_delete`.
   **`Client`, `Company` and `User` are the strict cases**: deactivatable,
   never deletable, no `can_delete` at all — and deactivating is
   roster-scope only, touching no order, invoice or analytics figure
   (`CL17`–`CL18`, `CO7a`). For a company or a user it also ends any session
   already open, not just future sign-ins (`CO4f`).
9. **A form that doesn't render a field means "leave it alone", not "clear
   it".** Guard with `if "field" in request.form` before writing it.
10. **Derived, never stored** — order totals, invoice paid-ness, lead counts,
    stock alerts. A stored copy is a copy that can disagree with reality.
11. **Frozen at issue.** Once an invoice leaves draft, nothing may change what
    it says — not settings, not the client's province, not the line items.
12. **A column added to a table that already shipped needs a migration entry**,
    in *that module's* `migrations.py` — not the root one. New tables don't;
    `db.create_all()` covers them.
13. **Set `DATABASE_URL` before importing `app`**, never
    `SQLALCHEMY_DATABASE_URI` after — the engine is built during `app.py`'s
    module-level `create_all()`, so setting it late silently writes to the real
    `data/atelier.db`. This has bitten us.
14. **Carry `return_to` through** every cross-page link and edit form, so saving
    returns to the timeline window (or list) it was opened from.
15. **Keep both Dockerfiles and both compose files in sync by hand** — local and
    demo share no base file.
16. **A fresh database gets the tenant, not a dataset.** `seed_if_empty()`
    creates one company, its admin user and the `SourceOption`/`OrderType`
    starter lists — never clients, orders, invoices or a letterhead. The demo
    data lives in `sample_data.py`, which **nothing imports at startup**; it's
    loaded on purpose by `python scripts/seed_sample_data.py`. Fixed reference
    data (province tax rates, the unit catalog) isn't seeded either — it's code
    constants, and the per-company rows around it are created lazily on first
    use. **Sample dates are day offsets from the seed date, never calendar
    dates**, so the timeline always opens on current work — keep payments and
    invoices at offset `<= 0`, and don't mark an unstarted order ready.
17. **Saving keeps your place.** After a form posts, the page that comes back
    opens where you were, or at the message the save left — never at the top
    of a long page. `static/assets/js/stay-in-place.js` (loaded by
    `base.html`) does it for every posting form, so a new form gets it for
    free as long as you: **redirect back to the same page** (a `#fragment`
    overrides it, so don't add one), **draw any message a save shows with
    `templates/_save_notice.html`** (which marks it `data-save-notice`),
    and **submit from script with `requestSubmit()`, never
    `form.submit()`**. `tests/test_stay_in_place.py` checks the last two.
    **The message goes in the section whose button was pressed** (MOD8):
    the form carries `{{ notice_field('x') }}`, the route stashes
    `{message, category, section}`, and the section renders
    `{{ notice_slot(notice, 'x') }}` under its heading — plus the page's
    `page_notice(notice, [its slots])` at the top as the fallback. Green for
    done, red for refused; amber `.warning-note` is for standing conditions
    only. `tests/test_save_notices.py` fails if a form names a slot its
    page lacks, or a reporting form names none. Hard rule 14 is the
    cross-page half of the same idea.

## What this is

A prototype order & inventory planner for a custom leather goods maker. Two main
views: a **timeline** (Gantt-style, multi-week, one row per order showing start →
due as a bar — this is the default landing page) and a day-level **calendar** (month
grid, orders shown on their due date). From the timeline, clicking a client name or
an order bar opens a quick-view/edit modal, which links out to a full **client
page** or **order page**. Orders carry line items, and an order can be turned into a
numbered **invoice** the app owns end to end — downloadable as a PDF in the
company's own layout, colours and logo, and reconciling cash,
e-transfer and Square payments against one record. Sign-in is required for every
view. Built to eventually grow into full order management + inventory tracking — and, per the tenant model
described below, potentially a multi-tenant SaaS product.

## Stack

- Python 3 / Flask (server-rendered Jinja templates, no JS framework, no build step)
- SQLite via SQLAlchemy (`Flask-SQLAlchemy`), file lives at `data/atelier.db`
  (gitignored, bind-mounted into Docker). See
  [docs/data-model.md](docs/data-model.md) — the ORM was chosen specifically so
  a later move to Postgres/MySQL is a connection-string + driver change, not a
  rewrite. **`DATABASE_URL` overrides the SQLite path**, and is the single
  change needed for that move (see hard rule 13 for the ordering trap).
- `Flask-Login` for session-based auth (see "Auth" below)
- Plain CSS, no preprocessor, no bundler

## Running it

**Local (dev, Flask's built-in server):**
```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt   # requirements.txt plus pytest, for running the test suite
python app.py
```
Visit `http://127.0.0.1:5000`. Debug mode is on, so the server auto-reloads on file changes.

`requirements-dev.txt` pulls in `requirements.txt` and adds `pytest`. The Docker
images (`Dockerfile`, `Dockerfile.demo`) install only `requirements.txt` —
tests, `tests/`, `e2e/`, and the doc/requirements files listed in
`.dockerignore` never reach a built image.

A fresh database starts **empty** — one company, one admin user, the option
lists, nothing else. For something to look at while developing, load the demo
clients/orders/invoices once:
```bash
python scripts/seed_sample_data.py
```

**Docker (prod-like, gunicorn):**
```bash
docker compose up --build
```
Visit `http://localhost:5013`. `docker compose down` to stop. See [docs/deployment.md](docs/deployment.md) for details.

## Project structure

```
app.py                 # Flask app: config, auth wiring, routes/view logic
models.py              # core SQLAlchemy models + first-boot bootstrap + run_migrations()
crypto.py              # shared Fernet SecretBox — the one host helper a module may import
sample_data.py         # demo clients/orders/invoices — imported by NOTHING at startup
billing_adapter.py     # the ONLY file that knows billing's "subject" means an Order
showcase_adapter.py    # the ONLY file that knows a showcase piece can come from an Order
REQUIREMENTS.md        # core-app rules as numbered, checkable statements

scripts/               # run by hand, never at startup
  seed_sample_data.py  # loads sample_data.py into a dev/demo database
  migrate.py           # applies pending migrations on purpose, and prints the diff
                       # (booting the app already does this — see docs/deployment.md)
  import_showcase_from_site.py  # copies a website's Recent Commissions into
                       # Showcase; dry run unless --apply, safe to re-run (SC34);
                       # --remove deletes what it imported (SC37)
  backfill_pickup.py   # marks existing orders "picked up"; dry run lists every
                       # uninvoiced order whose total would change, --apply writes

admin/                 # platform admin: companies, users       -> admin/CLAUDE.md
                       # NOT a module — imports host models on purpose
usage/                 # feature-usage events + /admin/usage    -> usage/CLAUDE.md
                       # __init__ = track() for anyone; store.py = host-only
brand/                 # the studio's logo (invoices + Showcase)  -> brand/CLAUDE.md
                       # imports only `db`; billing gets the logo via a hook
features/              # per-company features switched in /admin -> features/CLAUDE.md
                       # any module may import it (only `db` from the host)
showcase/              # portfolio of finished pieces (a feature) -> showcase/CLAUDE.md
billing/               # invoicing, Canadian sales tax          -> billing/CLAUDE.md
communications/        # Gmail + Google Calendar integration    -> communications/CLAUDE.md
inventory/             # materials & stock tracking             -> inventory/CLAUDE.md
documents/             # order document upload/storage          -> documents/CLAUDE.md
ai/                    # reply suggestions & image rendering    -> ai/CLAUDE.md

templates/             # base.html + the core app's Jinja templates
static/assets/css/     # style.css — all styling lives here
static/assets/js/upload-tile.js  # SYNCED COPY from website_modules (sync.py):
                       # never edit here — change the master, then sync
docs/                  # the long-form docs indexed at the top of this file
tests/                 # pytest suite                           -> docs/testing.md
data/                  # gitignored; bind-mounted; holds atelier.db + attachments

Dockerfile / docker-compose.yml            # prod deployment, port 5013
Dockerfile.demo / docker-compose-demo.yml  # demo deployment, port 5555
entrypoint.sh                              # chowns /app/data, then drops to appuser
```

Each module owns its own models, migrations, blueprint and templates, and is
registered from `app.py` — the composition root. Their internal layout is
documented in their own `CLAUDE.md`.

**Note on static files:** `app.py` uses Flask's default static handling
(`static_folder="static"`), and files live on disk under `static/assets/...`.
Templates reference them the normal way:
`url_for('static', filename='assets/css/style.css')`.

If you add new static assets (images, JS), drop them under `static/assets/` in a
sensibly named subfolder (`img/`, `js/`, etc.) — don't create a second top-level
`static/` folder.

## Auth

Every view route is decorated `@login_required` (`Flask-Login`). `/login` (GET shows
the form, POST checks `email`/`password_hash` via `werkzeug.security`),
`/privacy` and `/terms` are the only unauthenticated routes — the two legal
pages have to be openable by a signed-out Google OAuth reviewer, so they're
public on purpose and linked from the footer of every page (REQUIREMENTS `CO4b`).
The other exception is Showcase's **kiosk links** (`/k/<token>/…`): a capability
URL whose token is the permission, so they ignore the session entirely, and
app.py's staff and password-change redirects skip them (showcase `SC27`–`SC30`). `login_manager.login_view = "login"`,
so hitting any protected route while logged out redirects to `/login?next=...` and
bounces back after a successful sign-in.

`app.config["SECRET_KEY"]` reads from the `SECRET_KEY` env var, falling back to an
insecure dev default (`"dev-not-secure"`) — **must** be set to a real value for any
non-local deployment (`docker-compose.yml` already passes it through).

**Email is the login identity**, unique across the whole platform;
`users.full_name` is display only. Usernames were globally unique, which
made a second tenant impossible — see [admin/CLAUDE.md](admin/CLAUDE.md) for
why that needed the `users` table rebuilt rather than altered.

**Two kinds of user, and they don't overlap.** A *tenant user* has a
`company_id`, sees the app, and can't reach `/admin`. A *platform admin* has
`is_platform_admin`, **no company at all**, and sees `/admin` and nothing
else — the timeline has no answer to give somebody who isn't in a studio.
Neither can be turned into the other; staff are created as staff. Two
bootstrap credentials follow from that:

- `admin@example.invalid` / `changeme` — By Monsieur's own user
  (`ADMIN_EMAIL` / `ADMIN_PASSWORD`, `seed_if_empty()`).
- `platform@example.invalid` / `changeme` — the platform admin
  (`PLATFORM_ADMIN_EMAIL` / `PLATFORM_ADMIN_PASSWORD`,
  `ensure_platform_admin()`). Guarded on "is there any staff?" rather than
  "is the database empty?", so it also fires on a migrated single-tenant
  database, which seeding skips.

**Settings → Account** (`/settings/account`) changes the signed-in user's own
password — current password required, 8-character minimum, all enforced
server-side — and holds their **email signature** (`users.signature`), which
prefills the compose box and is appended to AI-drafted replies. Per user, not
per company or per mailbox: a signature is written by a person, and two people
sharing one `studio@` each want their own.

Adding a user and resetting someone else's password are no longer shell jobs
— a **platform admin** (`users.is_platform_admin`) does both from `/admin`,
along with provisioning companies and impersonating a tenant user for
support. There's still no password reset *by email*: the app has no address
of its own to send from. Deactivating a user or a company blocks sign-in
**and** ends any session already open, via `load_user()` in `app.py`.

`base.html`'s nav (`.view-switch` — Timeline / Calendar / Orders / Inventory /
Clients / Invoices / Analytics / Settings / Admin / Log out) only renders when
`current_user.is_authenticated`, so the login page itself has no nav. The
tenant links render only for a user with a company; **Admin** appears only for
a platform admin, and disappears while they're impersonating — which is also
when the tenant links come back, since `current_user` is then the tenant user.

## Tests

```bash
python -m pytest
```

Most of the suite covers `communications/`; the rest covers the money (tax,
invoice numbering, the snapshot rules) and `inventory/`. Timeline, orders and
analytics are still untested.

**`tests/conftest.py` sets `DATABASE_URL` to a temp file *before importing
`app`*, and `_app` asserts on it** — see hard rule 13. Each test drops and
recreates the schema; a rollback-per-test fixture was tried and doesn't work
here. Nothing touches Google: `tests/fakes.py` patches the provider registry
*and* the names already imported into calling modules.

Which test file defends which rule, and the deliberate regressions the suite was
checked against, are in [docs/testing.md](docs/testing.md).

## Deployment

Docker + gunicorn (2 workers, `--preload`), two parallel deployments sharing the
same app code and `entrypoint.sh`:

```bash
docker compose up --build                            # prod, port 5013
docker compose -f docker-compose-demo.yml up --build -d   # demo, port 5555
```

**Set a real `SECRET_KEY`** before deploying anywhere reachable — the fallback
is dev-only and insecure. `--preload` and the entrypoint's `chown` are both
load-bearing (seeding races and a read-only bind mount respectively), and the
two Dockerfiles are kept in sync by hand — see
[docs/deployment.md](docs/deployment.md) before changing either.

## Conventions when extending

- Keep templates server-rendered Jinja; don't introduce a JS framework without discussing it first — this is intentionally a simple stack.
- New routes go in `app.py` unless the file grows large enough to warrant a `routes/` or blueprint split — ask before restructuring.
