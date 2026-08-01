"""
Schema changes for this module's own tables.

Separate from `run_migrations()` in the root models.py on purpose. That
function's `_ADDED_COLUMNS` is the *app's* list; putting communications
columns in it would mean the root model file has to know what this module
stores, which is the coupling the whole module layout exists to avoid.

Same contract as the root one, though, because the reason for it is the
same: `db.create_all()` adds missing *tables* but never missing *columns*,
so a column added to a table that already shipped has to be applied by
hand. Every step is a no-op once applied, so this is safe on every boot and
on a fresh database.

Called from app.py, which is the composition root and already imports both
halves — that avoids the circular import a call in either direction between
models.py and this package would create.
"""

import logging

import sqlalchemy as sa

from models import db

logger = logging.getLogger(__name__)

# (table, column, DDL) for columns added after that table first shipped.
# Appending here is the way to add another one.
#
# Note the module's *tables* never need an entry — create_all() builds any
# table it hasn't seen. This list only covers columns added later, which is
# why it was empty until the lead dismissal feature.
ADDED_COLUMNS = [
    ("email_threads", "dismissed_at", "DATETIME"),
    ("email_threads", "dismissed_reason", "VARCHAR(20)"),
    ("email_threads", "opened_at", "DATETIME"),
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
        logger.info("communications: applied %s column migration(s).", applied)
