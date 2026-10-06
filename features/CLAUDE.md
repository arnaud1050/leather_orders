# features/ — per-company features

The *why* behind this package. The checkable rules are in
[REQUIREMENTS.md](REQUIREMENTS.md) (FE1–FE9).

## What it's for

Some parts of the app are sold separately — as an extra, or as part of a
higher tier — so they have to be switchable per company. A platform admin
does that from the company's page in `/admin` (a **Features** section, one
checkbox per catalog entry), and this package is the one place the rest of
the app asks "does this studio have X?".

The first entry is `showcase` (the portfolio / website sync / catalog
mode planned in [docs/roadmap.md](../docs/roadmap.md)). Until that module
ships, switching it on changes nothing a studio can see.

## Shape, and why

- **A catalog in code, rows in a table.** `FEATURES` in `__init__.py`
  says which features exist; `company_features` says who has them. The
  next sellable feature is one dict entry, not a migration or a new column
  on `companies` — which is also why it isn't a `showcase_enabled` boolean
  on `Company`.
- **A row means on.** No `enabled` column: a stored `false` beside a
  missing row would be two ways to say "off" that could disagree. Switching
  off deletes the row and nothing else (FE5). `enabled_at` is kept because
  "since when" is the first thing a bill will want.
- **Unknown keys raise** (FE2). `is_enabled("showcse")` returning `False`
  would hide a feature someone paid for with no error anywhere.
- **404, not 403** (FE7). For a studio without the feature the page simply
  doesn't exist, the same reasoning as a deactivated thing being hidden
  rather than forbidden. `require(key)` is the call; a module's blueprint
  can make it in a `before_request` so no route is forgotten.

## Who may import it

Any module, like `usage` and `crypto.py`. It imports only `db` from the
host (FE9, enforced in `tests/test_features.py`), so a module depending on
it still never sees a host model. `admin/` uses it for the toggle;
`app.py` imports it before `db.create_all()` so the table exists and calls
`register(app)` for the template helper.

## Not built (yet)

- **Plans or tiers.** Today each feature is switched individually. If
  tiers arrive, a tier is a named set of catalog keys, and this package's
  read side doesn't change.
- **An audit trail** of who switched what, and when it was switched off.
  That's the admin audit log already listed in `admin/CLAUDE.md`, not
  something to start separately here.
- **Self-serve.** Only platform staff can switch features; studios can't
  buy one from inside the app.
