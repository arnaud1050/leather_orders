"""
Schema changes for this module.

Same contract as `documents/migrations.py`: `db.create_all()` adds missing
*tables* but never missing *columns*, so a column added after one of this
module's tables first shipped needs an entry in `ADDED_COLUMNS` here. Called
from app.py, the composition root, right after the other module migrations.

Every table this module owns (`inventory_types`, `inventory_items`,
`order_materials`, `order_material_others`) is brand new, so there's nothing
to migrate yet — this starts empty, same as `documents/migrations.py` did
before its first column was added.
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
        logger.info("inventory: applied %s column migration(s).", applied)
