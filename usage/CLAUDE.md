# usage/ — feature-usage events

The *why* behind this package. The checkable rules are in
[REQUIREMENTS.md](REQUIREMENTS.md).

## What it's for

Knowing which features studios actually use, so development goes where the
use is and sales can point at real adoption. The answer lives on
`/admin/usage` (platform staff only): per feature, how many **companies**,
people and uses over a period, with a per-company view and a list of
companies by activity.

It's in the database rather than the logs on purpose. Logs rotate,
can't be joined to a company, and break silently when a line is reworded.
Raw events rather than pre-computed totals, because the volume is tiny
(a few thousand rows a month) and raw rows still answer questions nobody
has thought of yet. If the table ever gets big, add a rollup then.

## Two halves, and why

- **`usage/__init__.py`** is the catalog (`EVENTS`) and `track()`. It
  imports Flask and the standard library only, so every module can call
  `track()` without importing anything of the host, the same allowance
  `crypto.py` gets. `track()` writes nothing: it appends to a list on
  `flask.g`.
- **`usage/store.py`** is host code: the `UsageEvent` table, the
  `after_request` writer (`flush`), the endpoint map for page views, and the
  report queries the admin page uses. It knows `Company`, `User` and
  impersonation, so no module may import it.

## The one rule that matters: the action never pays

Recording that someone saved an invoice must never be the reason the
invoice didn't save, or took five seconds. Every design choice follows from
that:

- **Queue now, write after.** `track()` can't touch the database, so it
  can't fail the handler's transaction. The writer runs in `after_request`,
  after the handler has committed.
- **Own connection.** The write goes through `db.engine.connect()`, never
  `db.session`. A failed insert can't poison the session the action used.
- **Catch everything.** An exception from an `after_request` hook would
  turn a successful save into a 500, so `flush` catches everything and logs
  a warning.
- **Short lock wait.** SQLite's default busy timeout is five seconds. The
  writer lowers it to 250 ms on its connection, then puts it back before
  the connection returns to the pool.
- **Skip when the request is still writing.** If the handler left an
  uncommitted write, the action will be rolled back at teardown. There's
  nothing true to record, and our own lock would block us. This also keeps
  the test suite fast: conftest's fixtures only `flush()`.

## Where events come from

- **Actions**: an explicit `track("name", **props)` on the success path,
  after the commit. In `app.py` for the core app, in each module's
  `routes.py` for theirs.
- **Page views and file opens**: no handler code at all. `VIEW_ENDPOINTS` in
  `store.py` maps an endpoint to an event, recorded for a GET that returned
  200. A view has no success path to find, just a 200.
- **Settings**: one event, `settings.changed`, with `section` saying which.
  About 35 routes; one event per route would bury the signal.

## Adding an event

1. Add it to `EVENTS` in `usage/__init__.py` (area, label, daily?).
2. Call `track()` on the success path, or add the endpoint to
   `VIEW_ENDPOINTS` for a read-only page.
3. `tests/test_usage.py` fails if either half is missing. Add a test that
   the action records it.

Props are enums only: no names, emails, amounts or free text, and not a
studio's own labels (an order type's name is the studio's data, not ours).

## Deliberately not recorded

Deletes, toggles, reorders outside Settings, refused saves, everything in
`/admin`, the automatic sync jobs (those belong in the logs), the legal
pages, and the timeline itself: it's the landing page, so it would always
read 100%.

## Privacy

The privacy policy (section 2) lists usage records, and a company deletion
request includes `usage_events` (US6). Rows hold ids and enums, never
content, and the admin page shows counts only (admin `PA33`).
