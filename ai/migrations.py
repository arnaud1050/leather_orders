"""
Schema changes for this module.

Same contract as `documents/migrations.py` and `communications/migrations.py`:
`db.create_all()` adds missing *tables* but never missing *columns*, so a
column added after `ai_settings` first shipped needs an entry in
`ADDED_COLUMNS` here — not in the root `models.py` list, which must not have
to know what this module stores. Called from app.py, the composition root,
alongside the other module migrations.

Empty on purpose right now: `ai_settings` is a brand-new table, so
`db.create_all()` covers it whole. The file exists so the next column lands
in the right place rather than being reasoned about from scratch (hard rule
12).
"""

import logging

import sqlalchemy as sa

from models import db

logger = logging.getLogger(__name__)

ADDED_COLUMNS: list[tuple[str, str, str]] = []


def run_migrations() -> None:
    if not ADDED_COLUMNS:
        return

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
        logger.info("ai: applied %s column migration(s).", applied)
