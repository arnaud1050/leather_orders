# Per-company features — business requirements & rules

The living spec for `features/`. When behaviour changes on purpose, update
the rule here in the same commit — if a rule and the code disagree, one of
them is a bug. [CLAUDE.md](CLAUDE.md) explains the *why*.

- **FE1 — One closed catalog.** `features.FEATURES` is the complete list
  of switchable features, each with a label and a one-line description
  (both shown on the admin company page). Adding a feature is an entry
  there, never a migration. A stored key is never renamed: a feature is
  retired and a new key added.
- **FE2 — Unknown keys fail loudly.** `is_enabled()` and `set_enabled()`
  raise `KeyError` for a key not in the catalog, so a typo fails the first
  test that reaches it instead of reading as "off" forever.
- **FE3 — No company, no features.** A null `company_id` (platform staff,
  a signed-out visitor) has no features. Null means "not a tenant", never
  "no filter" (hard rule 1).
- **FE4 — Off by default, per company.** A new company has no features.
  Switching one on for a company changes nothing for any other.
- **FE5 — Off hides, never deletes.** A row in `company_features` means
  on, no row means off, and switching off removes that row and nothing
  else. Whatever a feature stored for the company stays, so switching it
  back on restores it exactly. Deactivating a company (admin `PA14`) leaves
  its features as they were.
- **FE6 — A retired key reads as off.** A row whose key has left the
  catalog is kept, but every read ignores it.
- **FE7 — A feature's routes 404 without it.** A route belonging to a
  feature calls `features.require(key)` first, which 404s unless the
  signed-in user's company has it: for that company the page doesn't
  exist. Hiding the link is a convenience; this is the rule.
- **FE8 — Templates ask `has_feature(key)`.** Injected for every template
  by `features.register(app)`, answering for the signed-in user's company,
  false when signed out. Nav items, tabs and badges of a feature render
  only when it's true.
- **FE9 — Any module may import it.** The package imports only `db` from
  the host — never a host model, `app.py`, another module or
  `usage.store` — so depending on it costs a module nothing of its
  boundary (hard rule 4).

- **FE10 — When a feature began.** `enabled_since(company_id, key)` is the
  start of the company's current spell of the feature (naive UTC), or None
  without it. Switching off and on again starts a new spell. A feature may
  use it to scope itself to what happened after it arrived (Showcase's
  reminder does, `SC20`).

Switching features is a platform-admin action: see admin `PA8a`.

## Test coverage map

| Rules | Where |
| --- | --- |
| FE1–FE10 | `tests/test_features.py` |
| admin PA8a | `tests/test_features.py` — the admin toggle; `tests/test_admin.py` — the route is guarded |
