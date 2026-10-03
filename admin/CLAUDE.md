# admin/ — platform administration

The *why* behind this package. The checkable rules are in
[REQUIREMENTS.md](REQUIREMENTS.md); the code comments carry the local
detail. This file is for the decisions that aren't visible from any single
file.

## It is not a module, and that is the point

Every other package here — `billing/`, `communications/`, `inventory/`,
`documents/`, `ai/` — is a self-contained module under hard rule 4: it owns
a slice of domain, it never imports a host model, and two of them have
tests that enforce it. That discipline is what makes them liftable.

This package inverts that. Its subject matter *is* `Company` and `User`; a
version of it that couldn't see them would have nothing to administer. So
it imports `models.py` freely, the boundary tests deliberately skip it, and
`app.py` registers it with the same `register(app, ...)` hook shape the
modules use only because that shape is already the convention here.

It's a package rather than a dozen more routes in `app.py` for one reason:
`app.py` is already past 2200 lines, and hard rule 3 always allowed a split
once a file grew enough to warrant one. If a `routes/` package ever
happens, this is the shape it should take.

## Platform staff are not inside a tenant

This is the shape everything else follows from. A `User` is one of two
things and never both:

- a **tenant user** — has a `company_id`, sees the app, cannot reach
  `/admin`;
- a **platform admin** — has `is_platform_admin` and **no company at all**,
  sees `/admin` and nothing else.

The first cut of this had `is_platform_admin` as an orthogonal flag on a
user who still belonged to a company, on the reasoning that it grants
`/admin` and never widens a query. That reasoning is sound and the result
was still wrong: it meant whoever administers the installation was also a
member of one customer's company, seeing their timeline and their clients
as their own. Fine while the operator of By Monsieur and the operator of
the platform are the same person; indefensible the moment they aren't.

So `users.company_id` is nullable, and "has no company" is what the rest of
the app keys off — `is_tenant_user`, the route guard, the nav. Nothing
enforces the exclusivity in the schema (SQLite has no partial check worth
migrating around), so `admin/services.py` is the gate: staff are **created**
as staff, never promoted, and never demoted. Promotion would manufacture
the forbidden account; demotion would leave a user with no company and no
rights, which is an account that can't do anything at all.

## What it deliberately can't do

**It cannot read tenant data.** No page here renders a client, an order, an
invoice or an analytics figure, and adding one would be a bigger decision
than it looks: every query in the app filters
`current_user.company_id` (hard rule 1, 67 call sites in `app.py` alone), so
a cross-tenant reporting view means either auditing all of them or growing
a second, parallel set of queries that nothing else exercises. The second
option is how tenant leaks happen.

The supported answer is impersonation. It costs a session swap and a
banner, and in exchange every page a platform admin looks at is rendered by
the same filtered code path a real user gets. There's nothing to keep in
sync because there's only one implementation.

**Keeping staff out of tenant routes is one hook, not 155 edits.**
`_keep_staff_out_of_tenant_routes` in `app.py` redirects a company-less user
back to `/admin` from anything that isn't an admin route, `/logout`, the
legal pages or static files. It fails closed, so a route added next year is
covered by default. A redirect rather than a 403 because the timeline isn't
*forbidden* to staff — it's meaningless, having no company whose orders it
could show — and landing them on the one page that does have an answer
beats an error page.

## Email replaced usernames, and why that needed a table rebuild

`User.username` was globally unique. That's invisible with one tenant and
fatal with several: the second studio to sign up also wants an `admin`.
Email is naturally unique platform-wide and is the only identifier a
password-reset flow could ever use, so it became the identity and
`full_name` became a plain display column with no constraint on it.

Removing the old constraint is the awkward part. SQLite can't add a UNIQUE
column and can't drop an inline UNIQUE, so `_migrate_users_to_email()` in
`models.py` rebuilds the table — the only migration in the project that
does. Leaving `username` in place and merely not using it was the
tempting alternative and would have left the collision exactly where it
was; the constraint had to actually go.

The backfill parks a non-address username at `<username>@example.invalid`.
`.invalid` is reserved by RFC 2606 and can never resolve, so a placeholder
address is incapable of quietly being someone's real one.

## Two bootstraps, guarded on two different questions

`seed_if_empty()` asks "is this database empty?". `ensure_platform_admin()`
asks "is there any platform staff?". They look redundant and aren't: the two
diverge in exactly the case that matters, an existing single-tenant database
being migrated. It has a company already, so seeding returns early — and
without the second call there'd be nobody who could reach `/admin`, on the
one deployment that most needs to.

That's also why the migration doesn't promote anyone. On upgrade, the
studio's existing login stays the studio's login (it owns their signature,
and communications' audit log has a foreign key to it); a *separate* staff
account appears beside it, from `PLATFORM_ADMIN_EMAIL` /
`PLATFORM_ADMIN_PASSWORD`.

`ensure_platform_admin()` asks whether a **usable** platform admin exists —
company-less *and* active — not whether the flag appears somewhere. That
distinction was learned the hard way. The first cut of this feature put the
flag on a user who still belonged to a company; deactivating that company
locked the account out (`load_user` drops any session whose company is off),
and a version of this function that merely counted flags would look at that
locked-out user, conclude staff already existed, and step politely aside
from the one database that needed rescuing. It now also *repairs* the
combination on sight: such a user keeps their studio and loses the flag,
since the flag is the half that was wrong.

## Impersonation, and the one guard that matters

`session["impersonator_id"]` holds the real platform admin's id and is the
**sole** definition of "currently impersonating". `acting_platform_admin()`
returns `None` whenever it's set, which is what makes `/admin` unreachable
from inside a tenant session.

The tempting shortcut — checking `current_user.is_platform_admin` — fails
open: impersonate a user who happens to hold the flag and the admin area
opens right back up, now acting as somebody else. `tests/test_admin.py`
checks that case from both sides.

`/admin/stop-impersonating` is the single route not behind
`platform_admin_required`, for the obvious reason that the guard is false
by design while impersonating. It checks the session key itself.

## The announcement banner has no `company_id`, and that's the whole design

`PlatformSettings` (`admin/models.py`) is the first table in this project
that belongs to neither a company nor a user — a singleton row, `id=1`,
created lazily on first read the same way `AISettings` and
`BillingProfile` are, except there's no `company_id` to key it by. It's
brand new, so `db.create_all()` covers it and there's no `migrations.py`
here (hard rule 12) — `admin/__init__.py` imports `admin.models` on
purpose, the same convention `ai/__init__.py` and `inventory/__init__.py`
already use, so the table lands in SQLAlchemy's metadata before that
`create_all()` call runs.

Two columns, `announcement` and `is_active`, rather than one field where
blank means off. The obvious shortcut costs an admin their draft every
time they turn a recurring maintenance notice off — and a maintenance
window is exactly the kind of thing that recurs. Keeping the text and the
switch separate means writing the sentence once.

**The banner ignores `current_user.is_authenticated`, and that's
deliberate, unlike the other two callables on the same context
processor.** `is_platform_admin` and `impersonating_as` are both about
*who's signed in*; a maintenance notice is about the installation and is
most useful to somebody about to sign in, not only somebody already past
that gate. It's why the render sits in `base.html` above the
`{% if current_user.is_authenticated %}` block entirely, rather than
inside it next to the impersonation banner.

**Purple, not amber.** The impersonation banner already owns amber for
"needs attention soon" (hard rule 7), and the two can render at once — a
platform admin impersonating a tenant user during a maintenance window is
a real, if rare, combination. Reusing amber for both would make them read
as one fact instead of two. Purple is the app's other established colour
for "something worth knowing" (docs/design.md), which is a closer match
for an announcement anyway — nothing here is broken and nothing needs
attention soon, there's just something to read.

`_keep_staff_out_of_tenant_routes` in `app.py` never touches this render:
that guard is about which *pages* staff may reach, not about a decoration
every page (including the ones staff can't get past) already carries.

Rendered with `{{ announcement }}`, no `|safe`, on purpose: Jinja's
autoescaping is the only thing between an admin's typo and a script tag
served on every page of the installation, so nothing here may turn it
off. `white-space: pre-wrap` on `.announcement-banner` is what lets a
deliberate line break in the textarea survive into the page without
needing raw HTML to do it.

## What was left out of the first iteration

Named here so the absences read as decisions rather than oversights:

- **In-company roles** (owner vs. member). Everyone inside a company is
  equal. Worth adding when someone actually asks for a user who can't
  change the letterhead. Note this is a different axis from staff/tenant:
  that split is about *which* installation-level thing you are, this one
  would be about what you may do inside one studio.
- **Self-serve signup and billing/plans.** Both need a public route surface
  and a payment story; neither belongs in the same change as the tenant
  model.
- **Email invitations and password reset.** The app has no address of its
  own to send from — the Gmail accounts under Settings → Email/Calendar are
  the studio's client mail, not the platform's (see `N2` in the root
  `REQUIREMENTS.md`). Until that changes, a platform admin sets an initial
  password and hands it over.
- **Audit logging of admin actions.** The obvious next thing, and
  deliberately not bolted on late: `communications/` already has an audit
  table, and the right move is probably to generalise that rather than
  start a second one.

## Gotcha for tests

Flask-Login caches the resolved user on `g`, which is scoped to the app
context — and `tests/conftest.py` holds one app context open for a whole
test rather than one per request. Without the `_forget_cached_login` hook
in `conftest.py`, a test driving two clients (a platform admin and the
tenant they're acting on) silently runs both as whoever signed in first.
That was worth an hour once; it shouldn't be worth it twice.
