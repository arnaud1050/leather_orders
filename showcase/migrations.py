"""
Schema changes for the showcase tables.

`db.create_all()` creates the tables; a column added after they first ship
goes in `ADDED_COLUMNS` here, not in the root migrations (hard rule 12).
"""

import logging

import sqlalchemy as sa

from models import db

logger = logging.getLogger(__name__)

ADDED_COLUMNS: list[tuple[str, str, str]] = [
    # Where an imported piece came from (SC34). Nullable, so existing pieces
    # read as "made in the app", which they were.
    ("showcase_items", "source_ref", "VARCHAR(255)"),
]


def run_migrations() -> None:
    inspector = sa.inspect(db.engine)
    tables = set(inspector.get_table_names())
    applied = 0
    for table, column, ddl in ADDED_COLUMNS:
        if table in tables and column not in {c["name"] for c in inspector.get_columns(table)}:
            db.session.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            applied += 1
    if applied:
        db.session.commit()
        logger.info("showcase: applied %s column migration(s).", applied)
