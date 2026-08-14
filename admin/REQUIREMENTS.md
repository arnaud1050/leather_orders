# Platform admin — business requirements & rules

This is the living spec for `admin/`: every rule the platform admin area is
supposed to enforce, written as a checkable statement rather than prose. When
behavior changes on purpose, update the rule here in the same commit — if a
rule and the code disagree, one of them is a bug.

Each rule has an id (e.g. `PA4`) referenced from `tests/test_admin.py` where
practical, and from the **Test coverage map** at the bottom of this file.
`admin/CLAUDE.md` explains the *why*; this file is the checklist of *what
must hold true*.

## 0. Scope

- **PA0.1 — This is a host blueprint, not a module.** Unlike `billing/`,
  `communications/`, `inventory/`, `documents/` and `ai/`, this package
  imports host models (`Company`, `User`) on purpose. Its subject matter
  *is* those models; hard rule 4 does not apply to it and the boundary
  tests deliberately don't cover it.
- **PA0.2 — It administers tenants, it does not read their data.** No page
  here renders a client, order, invoice, message or analytics figure. The
  supported way to look at a studio's records is to impersonate somebody
  inside it (`PA18`), which goes through the app's ordinary
  `current_user.company_id` filtering rather than around it.
- **PA0.3 — Platform staff and tenant users are disjoint.** A platform admin
  has `is_platform_admin` and **no `company_id`**; a tenant user has a
  `company_id` and no platform rights. Neither can be the other, and there
  is no route that converts one into the other. The person who administers
  the installation must not also be a member of a customer's company.

## 1. Access

- **PA1.** Every route in this blueprint requires a signed-in user whose
  `is_platform_admin` is true and whose session is not impersonating.
  Anyone else gets **403**, not a redirect — a redirect would confirm to a
  tenant user that `/admin` exists. The one exception is
  `/admin/stop-impersonating` (`PA22`).
- **PA2.** The **Admin** link in `base.html`'s nav renders only when the
  same condition holds, so the area is invisible to everyone else.
- **PA3.** A platform admin sees `/admin` **and nothing else**. Every tenant
  route redirects them back to it, enforced by a single `before_request`
  hook in `app.py` rather than a check threaded through 155 call sites — so
  a route added later is covered without anyone remembering to cover it. A
  redirect rather than a 403 because the timeline isn't forbidden to them,
  it's meaningless: there's no company whose orders it could show.
- **PA3a.** `base.html` renders the tenant nav only for a user with a
  company. Staff get Admin and Log out alone. The hidden link is a
  convenience; `PA3` is the actual rule.
- **PA3b.** An unmatched URL still 404s for staff — the guard in `PA3` only
  redirects when a route actually matched, so a typo doesn't silently
  become the admin page.
- **PA3c.** `/login` lands staff on `/admin`, and ignores `?next=` for them:
  a bookmarked tenant URL would bounce straight off `PA3`.
- **PA3d.** `/admin` has two top-level pages, **Companies**
  (`/admin/companies`) and **Admin Users** (`/admin/platform-admins`),
  sharing one sub-nav (`_admin_nav.html`, the same convention as
  `_settings_nav.html`). `/admin/` itself is a bare redirect to Companies,
  not a third page — a bookmark or a typed URL always lands on a real,
  sub-nav-highlighted page. A company's own detail page
  (`admin_company.html`) does **not** carry the sub-nav, matching
  `client_page.html` not carrying `_clients_nav.html`: the roster-level nav
  belongs on the roster, not on one row's page.

## 2. Provisioning a company

- **PA4.** A company is created only through `models.create_company()` — the
  same function `seed_if_empty()` calls. A tenant provisioned here is
  therefore identical to the one an empty database bootstraps itself with:
  the company row, its billing letterhead, its first user, and the
  `SourceOption` / `OrderType` starter lists.
- **PA5.** A new company gets **no clients, orders, invoices or sample
  data** — `CO9a` applies to every tenant, not just the first.
- **PA6.** The first user of a new company is a **tenant user** — active,
  with that company, and no platform rights. This holds for
  `seed_if_empty()`'s user too; there is no exception anywhere.
- **PA7.** A company is created with its first user or not at all. If the
  name, address or password is rejected, neither row exists afterwards.
- **PA8.** A company's name and time zone are editable here; the name must
  not be blank.

## 3. Identity

- **PA9.** `User.email` is the login identity and is unique across the whole
  platform, not per company — the login lookup is by address alone, so two
  users sharing one would be ambiguous.
- **PA10.** Addresses are stored folded (stripped, lower-cased) by
  `models.normalise_email()`, and the login lookup calls the same helper, so
  a capitalised address signs the same person in.
- **PA11.** A rejected address says which company already holds it. Across
  tenants the platform admin can't otherwise find out, and a bare "taken"
  makes the error unfixable.
- **PA12.** A password set here — for a new user or a reset — must be at
  least `MIN_PASSWORD_LENGTH` (8) characters, the same minimum
  `/settings/account` enforces. The two constants are duplicated to avoid a
  circular import and a test asserts they agree.
- **PA13.** Resetting a password here does **not** require the current one.
  That check exists to stop an unattended browser becoming a permanent one
  and cannot apply to an administrator acting on somebody else's account.
  This is the app's only password-recovery path; there is still no
  reset-by-email.

## 4. Deactivation

Hard rule 8 — hide, don't delete — applied to tenants and the people in
them. Neither a `Company` nor a `User` has a delete route anywhere in the
app, and neither has a `can_delete`.

- **PA14.** Deactivating a company or a user blocks sign-in and nothing
  else. No order, invoice, client, document or analytics figure changes,
  and reactivating restores access exactly as it was.
- **PA15.** The block is enforced in two places, and both are required: the
  login route (so the credentials are refused with a message that says why)
  and `load_user()` in `app.py` (so a session already open when the button
  is pressed ends on its very next request). Guarding only the login route
  would make deactivation a request to leave rather than an instruction.
- **PA16.** A platform admin may not deactivate **their own account**. Any
  company may be deactivated, including the one they happen to be looking
  at — they belong to none of them, so none can lock them out.
- **PA17.** The installation can never be left with no active platform
  admin, and `PA16` alone guarantees it: the caller got past `PA1`, so they
  are themselves an active platform admin, and the only account they can't
  switch off is their own. There is deliberately **no** separate
  last-active-admin check — it would be unreachable code.

## 4a. Platform staff

- **PA17a.** Platform staff are **created as staff** — `add_platform_admin()`
  makes a user with no company. There is no route that promotes a tenant
  user, and that absence is the rule, not an omission: promotion would
  produce exactly the account `PA0.3` forbids.
- **PA17b.** There is no demotion either. It would leave a user with no
  company *and* no rights — an account that can do nothing and reach
  nothing. Deactivating (`PA16`) is what "this person no longer works here"
  means.
- **PA17c.** A rejected address names where it's already in use: the company
  for a tenant user, "the platform admin team" for staff, who have no
  company to name.

## 5. Impersonation

- **PA18.** A platform admin may sign in as any active user of any active
  company. This swaps `current_user` and changes nothing else — every page
  renders through the same tenant filtering it always does, so there is no
  second set of query paths to get wrong.
- **PA19.** A deactivated user, a user in a deactivated company, another
  platform admin, and the caller's own account are all refused, with a
  message saying what to do. (The first two are `PA15` talking:
  `load_user()` would drop the session one redirect later. Another staff
  account is refused because there'd be nothing to see — they have no
  studio either.)
- **PA20.** While impersonating, **`/admin` is unreachable** and its nav
  link is hidden. `session["impersonator_id"]` is the sole definition of
  "currently impersonating"; checking `current_user.is_platform_admin`
  instead fails open whenever the impersonated user happens to hold the
  flag.
- **PA21.** Every page carries a banner naming the impersonated user and
  their company, with a one-click return. The risk of impersonation is
  forgetting you're doing it, so the banner is on every page rather than
  in a settings corner.
- **PA22.** `/admin/stop-impersonating` is the one route not guarded by
  `PA1` — that guard returns false for an impersonated session by design,
  so requiring it would make the exit the one button that can't be pressed.
  It checks the session key itself instead, and 403s when there's nothing
  to exit.
- **PA23.** If the real platform admin's account has gone away or been
  deactivated during the impersonation, returning signs the session out
  entirely rather than leaving it as the tenant user.

## 6. Migration

- **PA24.** `_migrate_users_to_email()` in `models.py` rebuilds the `users`
  table once, on databases that still have a `username` column. It
  preserves ids (communications' audit log has a foreign key into this
  table), carries the old username across as `full_name`, and backfills
  `email` from the username — keeping it if it already looks like an
  address, otherwise parking it at `<username>@example.invalid`.
- **PA24a.** **Nobody is promoted by the migration.** Everyone stays a
  tenant user of the company they were already in. Promoting one would mean
  detaching a studio's only login from its studio (`PA0.3`).
- **PA24b.** `ensure_platform_admin()` creates the staff account instead,
  and is guarded on "is there a **usable** platform admin?" — meaning one
  that is company-less *and* active — rather than on "is this database
  empty?" or "does the flag appear anywhere?". It is idempotent, never
  resets an existing account's password, and steps aside to
  `<local>+platform@<domain>` rather than failing the boot if a tenant user
  already holds the address.
- **PA24c.** The same call **repairs** a user who holds both a company and
  the flag: they keep the company and lose the flag. That arrangement
  (`PA0.3`) was possible under an earlier version of this feature, and it
  is a live lockout, not a cosmetic one — deactivate that company and the
  account can't sign in (`PA15`), leaving an installation with a platform
  admin on paper and nobody able to reach `/admin`. Checking the flag alone
  would make this function step aside from precisely the database that
  needs it.
- **PA25.** The rebuild is idempotent: `run_migrations()` runs on every boot
  and the second pass must leave the table untouched.

## 7. Platform settings — the announcement banner

`PlatformSettings` (`admin/models.py`) is a singleton row — always `id=1`,
created lazily on first read (`PA0.2`'s "created lazily on first use"
pattern extended to a table with no `company_id` at all).

- **PA26.** `PlatformSettings` holds `announcement` (text) and `is_active`
  (bool) as two separate columns, not one blank-means-off field — an admin
  drafting a notice ahead of time can save the wording, switch it on when
  the window starts, and switch it back off afterward without losing the
  text for next time.
- **PA27.** The banner renders on **every page of the installation**,
  authenticated or not — including `/login`, `/privacy` and `/terms` —
  because a maintenance notice is most useful to someone about to sign in,
  not only someone already in. It is injected by the same
  `app_context_processor` as `PA2`'s `is_platform_admin`, but — unlike
  that one — is **not** gated on `current_user.is_authenticated`.
- **PA28.** Turning the banner **on** with a blank message is refused; the
  save is rejected outright and nothing is written. Turning it **off**
  with a blank message is allowed and clears the draft.
- **PA29.** The message is rendered as plain text (no `|safe`, no markup
  support) — Jinja's autoescaping is the only thing standing between an
  admin's typo and a script tag rendered on every page of the
  installation, so nothing here may bypass it.
- **PA30.** `/admin/settings` is the third `_admin_nav.html` tab, guarded
  the same way as `/admin/companies` and `/admin/platform-admins`
  (`PA1`, `PA3d`).

## Test coverage map

| Rules | Where |
| --- | --- |
| PA1, PA2, PA20 | `tests/test_admin.py` — access and the nav link |
| PA0.3, PA3, PA3a–PA3d | `tests/test_admin.py` — staff see /admin and nothing else, sub-nav |
| PA4–PA8 | `tests/test_admin.py` — provisioning |
| PA9–PA13 | `tests/test_admin.py` — identity, passwords |
| PA14–PA17, PA17a–PA17c | `tests/test_admin.py` — deactivation and staff accounts |
| PA18–PA23 | `tests/test_admin.py` — impersonation |
| PA24, PA24a, PA25 | `tests/test_user_migration.py` |
| PA24b, PA24c | `tests/test_seeding.py` |
| PA26–PA30 | `tests/test_admin.py` — the announcement banner |

**Not covered by tests:** the templates' own markup (no page here has logic
worth asserting on beyond what the route tests already render), and the
`example.invalid` placeholder's *suitability* — that it can never resolve is
an RFC guarantee, not something a test can check.
