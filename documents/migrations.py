"""
Schema changes for this module.

Same contract as `communications/migrations.py`: `db.create_all()` adds
missing *tables* but never missing *columns*, so a column added after this
module's table first shipped needs an entry in `ADDED_COLUMNS` here. Called
from app.py, the composition root, right after the other module migrations.

Also does the one-time cleanup this module exists to do: the legacy
`documents` table (fake "Mockup"/"Invoice" placeholder rows, no real files
behind them) is dropped outright on any install that still has it, rather
than migrated — there's nothing in it worth keeping. `order_documents`
itself needs no entry here; it's a brand-new table, covered by
`db.create_all()`.
"""

import logging

import sqlalchemy as sa

from models import db

logger = logging.getLogger(__name__)

# document_types is a brand-new table, no entry needed — db.create_all()
# covers it. order_documents already shipped, so its new column does.
ADDED_COLUMNS: list[tuple[str, str, str]] = [
    ("order_documents", "document_type_id", "INTEGER"),
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
        logger.info("documents: applied %s column migration(s).", applied)

    if "documents" in tables:
        db.session.execute(sa.text("DROP TABLE documents"))
        db.session.commit()
        logger.info("documents: dropped legacy fake-placeholder table.")
