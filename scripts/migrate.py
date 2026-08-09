#!/usr/bin/env python
"""
Apply pending schema migrations to a database, and report what changed.

**You usually don't need this.** The app runs `db.create_all()` and every
module's `run_migrations()` at import (see the `with app.app_context()` block
in app.py), so redeploying the demo already migrates it on boot. This script
exists for the times you want that to happen *deliberately* rather than as a
side effect of a restart: applying a schema change to a running deployment
before swapping the image, or just seeing — in writing — which columns a
release added to a database you care about.

It is exactly as safe to run twice as booting the app twice is: every
migration in this codebase is a no-op once applied.

Run from the project root, with the venv active:

    python scripts/migrate.py

On the demo deployment (the container already has the code and the volume):

    docker compose -f docker-compose-demo.yml exec demo python scripts/migrate.py

Against a specific database, e.g. a copy you want to test the upgrade on:

    DATABASE_URL=sqlite:////app/data/atelier.db python scripts/migrate.py

`DATABASE_URL` has to be set *in the environment*, before this imports `app` —
the engine is built during app.py's module-level `create_all()` (hard rule 13
in CLAUDE.md). Setting it any later silently migrates the real
`data/atelier.db` instead.

**A SQLite database is copied to a timestamped `.bak` beside itself first**,
before anything touches it. `--no-backup` skips that; a non-SQLite URL never
had one to take.

Note that importing `app` runs the whole boot path, not just migrations —
`seed_if_empty()` included. That's deliberate: it means this applies exactly
what a real boot would, no more and no less. On an already-populated database
seeding is a no-op; on an empty one you get the same company/admin/option
lists a first boot creates.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa  # noqa: E402  (must follow the sys.path fix-up)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The same default app.py falls back to, resolved the same way — this has to
# match, since the whole point is to report on the database the app will use.
DEFAULT_URL = "sqlite:///" + os.path.join(PROJECT_ROOT, "data", "atelier.db")


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def _sqlite_path(url: str) -> str | None:
    """The on-disk file behind a SQLite URL, or None for any other backend
    (nothing to copy) or for in-memory SQLite (nothing to copy either)."""
    if not url.startswith("sqlite:"):
        return None
    path = sa.engine.make_url(url).database
    return path or None


def _snapshot(url: str) -> dict[str, set[str]]:
    """table -> its column names, read through a throwaway engine.

    Taken *before* `app` is imported, so it can't accidentally be the
    post-migration state: importing app.py is what applies the migrations.
    Disposed immediately, so the app builds its own engine cleanly. A
    database that doesn't exist yet reads as empty rather than raising —
    creating it from nothing is a legitimate thing to run this against.
    """
    engine = sa.create_engine(url)
    try:
        inspector = sa.inspect(engine)
        return {
            table: {c["name"] for c in inspector.get_columns(table)}
            for table in inspector.get_table_names()
        }
    except sa.exc.SQLAlchemyError:
        return {}
    finally:
        engine.dispose()


def _report(before: dict[str, set[str]], after: dict[str, set[str]]) -> bool:
    """Print the diff. Returns whether anything actually changed."""
    new_tables = sorted(set(after) - set(before))
    changed = False

    for table in new_tables:
        print(f"  + table {table} ({len(after[table])} columns)")
        changed = True

    for table in sorted(set(after) & set(before)):
        for column in sorted(after[table] - before[table]):
            print(f"  + column {table}.{column}")
            changed = True

    # Never expected — nothing in this codebase drops anything — but worth
    # saying out loud rather than silently omitting if it ever happens.
    for table in sorted(set(before) - set(after)):
        print(f"  - table {table} is gone")
        changed = True
    for table in sorted(set(after) & set(before)):
        for column in sorted(before[table] - after[table]):
            print(f"  - column {table}.{column} is gone")
            changed = True

    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument(
        "--no-backup", action="store_true",
        help="skip the timestamped copy of the SQLite file taken beforehand",
    )
    args = parser.parse_args()

    url = _database_url()
    print(f"database: {url}")

    path = _sqlite_path(url)
    if path and os.path.exists(path) and not args.no_backup:
        backup = f"{path}.{datetime.now().strftime('%Y%m%d-%H%M%S')}.bak"
        shutil.copy2(path, backup)
        print(f"backup:   {backup}")
    elif path and not os.path.exists(path):
        print("backup:   none — the database doesn't exist yet, it'll be created")
    elif not path:
        print("backup:   none — not a SQLite file (back it up however that backend does)")
    else:
        print("backup:   skipped (--no-backup)")

    before = _snapshot(url)

    # THIS is the migration: app.py's module-level block runs create_all() and
    # every module's run_migrations() at import. Deliberately not calling those
    # functions by hand — a hand-assembled list here would be one more place to
    # forget a module the next time one is added.
    print("applying: importing app (create_all + every module's migrations)…")
    from app import app  # noqa: E402  (must follow the backup + snapshot above)

    with app.app_context():
        after = _snapshot(url)

    if _report(before, after):
        print("done — schema updated.")
    else:
        print("done — nothing to apply, the schema was already up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
