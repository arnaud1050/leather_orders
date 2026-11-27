"""
Schema changes for this module.

Same contract as `inventory/migrations.py`: `db.create_all()` adds missing
*tables* but never missing *columns*, so a column added after one of this
module's tables first shipped goes in `ADDED_COLUMNS` here (hard rule 12).
Every table is new as of this module's first release, so the list starts
empty. Called from app.py right after the other modules' migrations.
"""

import logging

import sqlalchemy as sa

from models import db

logger = logging.getLogger(__name__)

ADDED_COLUMNS: list[tuple[str, str, str]] = []


def run_migrations() -> None:
    inspector = sa.inspect(db.engine)
    tables = set(inspector.get_table_names())
    existing = {t: {c["name"] for c in inspector.get_columns(t)} for t in tables}

    applied = 0
    for table, column, ddl in ADDED_COLUMNS:
        if table in tables and column not in existing[table]:
            db.session.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            applied += 1
    if applied:
        db.session.commit()
        logger.info("costing: applied %s column migration(s).", applied)
