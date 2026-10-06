"""
Schema changes for `platform_settings`.

Same contract as the modules' `migrations.py` files: `db.create_all()` adds
missing *tables* but never missing *columns*, so a column added after
`platform_settings` first shipped needs an entry in `ADDED_COLUMNS` here
(hard rule 12). Called from app.py, alongside the module migrations.
"""

import logging

import sqlalchemy as sa

from models import db

logger = logging.getLogger(__name__)

ADDED_COLUMNS: list[tuple[str, str, str]] = [
    # The announcement's optional display window (see PlatformSettings).
    # Nullable with no default, so an existing row backfills to "no bound
    # on either side" — exactly how the banner behaved before the columns
    # existed.
    ("platform_settings", "starts_at", "DATETIME"),
    ("platform_settings", "ends_at", "DATETIME"),
    # The zone staff read and type times in. Backfills to the zone the
    # window was hard-wired to before this column existed, so a window
    # already saved reads back exactly as it was typed.
    ("platform_settings", "timezone", "VARCHAR(60) NOT NULL DEFAULT 'America/Vancouver'"),
]


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
        logger.info("admin: applied %s column migration(s).", applied)
