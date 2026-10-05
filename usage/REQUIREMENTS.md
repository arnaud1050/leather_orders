# Feature usage — requirements & rules

The living spec for `usage/`: which features studios actually use, recorded
as events and read back on `/admin/usage`. Each rule has an id referenced
from `tests/test_usage.py`. `usage/CLAUDE.md` explains the *why*; this file
is the checklist of *what must hold true*.

## 1. Never at the action's expense

- **US1.** Recording an event can never fail, roll back or alter the action
  it describes. `track()` only queues on `flask.g`; the write happens in
  `usage.store.flush` (an `after_request` hook) on its **own connection**,
  never through `db.session`, inside a `try` that catches everything and
  logs a warning. A missing table, a full disk or a bug in the writer costs
  the event, not the user's save.
- **US2.** Only successful actions are recorded. `track()` is called on the
  success path, after the action's own checks and commit; and a response
  with status **400 or above** drops everything queued during it.
- **US3.** The write waits at most `LOCK_WAIT_MS` (250 ms) for a SQLite lock
  held by someone else, then gives up on the events. The connection's normal
  busy timeout is restored before it returns to the pool.
- **US3a.** If the request's own session still holds an uncommitted write,
  the events are dropped without trying: the action isn't durable, and
  waiting on our own lock would only add `LOCK_WAIT_MS` to the request.

## 2. What counts

- **US4.** Only tenant users count. Platform staff never do, and neither
  does a session that is impersonating a tenant user — that's us looking.
  Background jobs (scheduled sync) never call `track()` with a request, so
  they never count either.
- **US4a.** A company flagged `exclude_from_usage` (a demo or test
  tenant, set in `/admin`) is still **recorded** but left out of every
  report query. Excluding hides; it doesn't stop recording, so clearing
  the flag brings the company's history straight back.
- **US5.** Events marked *daily* in the catalog (page views) count at most
  once per user, per props value, per UTC day. Every other event counts
  each time.
- **US5a.** Page views are recorded from the endpoint that served them
  (`VIEW_ENDPOINTS`), for a GET answered with exactly **200** — a redirect
  has shown nothing yet, so a preview that redirects to a download counts
  once, as the download.
- **US6.** Events are append-only and kept for as long as the company
  exists. A deletion request for a company deletes its usage events with
  the rest of its data (privacy policy, section 7).
- **US7.** Props are small enum-like values (`via`, `method`, `section`…):
  str, int or bool only, anything else is dropped. **Never** names, email
  addresses, amounts, free text, or a studio's own labels.

## 3. The catalog

- **US8.** `usage.EVENTS` is the complete list, with an area, a label and
  the daily flag for each. The admin report lists every catalog event,
  including those with no uses in the period.
- **US9.** Every `track("...")` name in the source is in the catalog, and
  every catalog entry is emitted somewhere — checked by reading the source,
  because an unknown name is dropped at runtime rather than raised.
- **US10.** Modules (`ai/`, `billing/`, `communications/`, `documents/`,
  `inventory/`) may import `track` from the `usage` package root and nothing
  else from it. The package root depends on Flask and the standard library
  only, so the import doesn't tie a module to this host. `usage.store`
  (which knows `Company`, `User` and impersonation) is host code.

## Test coverage map

| Rules | Where |
| --- | --- |
| US1, US2, US3, US3a | `tests/test_usage.py` — failure isolation |
| US4 | `tests/test_usage.py` — who counts |
| US4a | `tests/test_usage.py` — excluding a company |
| US5, US5a | `tests/test_usage.py` — page views |
| US7 | `tests/test_usage.py` — recording |
| US8 | `tests/test_usage.py` — `/admin/usage` |
| US9, US10 | `tests/test_usage.py` — the catalog and the boundary |

**Not covered by tests:** US6's deletion half. There is no in-app company
deletion; a deletion request is handled by hand, and `usage_events` is on
the list of tables to clear (`usage/CLAUDE.md`).
