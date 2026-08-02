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
    # The literal 30 is what the calendar job was hardcoded to before the
    # interval was configurable — so an existing deployment keeps exactly
    # the cadence it had. A migration records what shipped; it must not
    # start tracking the model's default if that ever changes.
    ("email_sync_settings", "calendar_frequency", "INTEGER NOT NULL DEFAULT 30"),
    ("email_messages", "read_at", "DATETIME"),
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

    if "email_messages" in tables:
        _backfill_read_messages()


def _backfill_read_messages() -> None:
    """Mark messages read in threads that were already opened.

    Without this, adding per-message read state lights up an unread count
    for every conversation on file — including ones somebody read months
    ago — and the first thing the feature does is cry wolf.

    A thread carries `opened_at` precisely because someone looked at it, so
    its messages *were* seen; that timestamp is the honest answer to when.
    Threads never opened stay unread, which is also correct.

    Runs on every boot and is a no-op once applied: it only touches rows
    that still have a null `read_at`, and after the first pass an opened
    thread has none.
    """
    result = db.session.execute(sa.text("""
        UPDATE email_messages
           SET read_at = (
                 SELECT opened_at FROM email_threads
                  WHERE email_threads.id = email_messages.thread_id
               )
         WHERE read_at IS NULL
           AND thread_id IN (SELECT id FROM email_threads WHERE opened_at IS NOT NULL)
    """))
    if result.rowcount:
        db.session.commit()
        logger.info("communications: marked %s message(s) read.", result.rowcount)
