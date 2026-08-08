#!/usr/bin/env python
"""
Load the demo dataset (sample clients, orders and invoices) into a database.

For test/demo environments only — a production install is meant to start
empty, which is why `seed_if_empty()` no longer does this. Run from the
project root, with the venv active:

    python scripts/seed_sample_data.py

It writes to whatever database the app itself would use, so **point it at a
throwaway file rather than your real one** unless you mean it:

    DATABASE_URL=sqlite:///$(pwd)/data/demo.db python scripts/seed_sample_data.py

`DATABASE_URL` has to be set *in the environment*, before this imports
`app` — the engine is built during app.py's module-level `create_all()`
(hard rule 13 in CLAUDE.md). Setting it any later silently writes to the
real `data/atelier.db`.

Importing `app` also runs the usual boot path (create_all, migrations,
`seed_if_empty`), so this works against an empty directory as well as an
existing install: the company, admin user and option lists are created
first, then the sample data lands on top of them.

Refuses to run if the company already has clients — it fills an empty
install, it never resets a populated one. To start over, stop the app,
delete the database file, and run this again.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (must follow the sys.path fix-up)
from sample_data import seed_sample_data  # noqa: E402


def main() -> int:
    with app.app_context():
        print(f"database: {app.config['SQLALCHEMY_DATABASE_URI']}")
        if seed_sample_data():
            print("Sample clients, orders and invoices added.")
            return 0
        print(
            "Nothing added — this company already has clients (or none exists). "
            "This script only fills an empty install.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
