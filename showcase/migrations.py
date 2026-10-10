"""
Schema changes for the showcase tables.

`db.create_all()` creates the tables; a column added after they first ship
goes in `ADDED_COLUMNS` here, not in the root migrations (hard rule 12).
"""

import logging
import uuid

import sqlalchemy as sa

from models import db

logger = logging.getLogger(__name__)

ADDED_COLUMNS: list[tuple[str, str, str]] = [
    # Where an imported piece came from (SC34). Nullable, so existing pieces
    # read as "made in the app", which they were.
    ("showcase_items", "source_ref", "VARCHAR(255)"),
    # Where each imported photo came from (SC39). Null for earlier imports
    # and for photos added in the app.
    ("showcase_photos", "source_ref", "VARCHAR(255)"),
    # Keys on the studio's website (SC40), filled in below for rows that
    # predate them.
    ("showcase_items", "public_key", "VARCHAR(32)"),
    ("showcase_photos", "public_key", "VARCHAR(32)"),
    # The stored file's hash (SC40); null rows are filled on first need.
    ("showcase_photos", "content_sha256", "VARCHAR(64)"),
]

# Tables whose rows each need a website key of their own.
KEYED_TABLES = ("showcase_items", "showcase_photos")


def run_migrations() -> None:
    inspector = sa.inspect(db.engine)
    tables = set(inspector.get_table_names())
    applied = 0
    for table, column, ddl in ADDED_COLUMNS:
        if table in tables and column not in {c["name"] for c in inspector.get_columns(table)}:
            db.session.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            applied += 1
    keyed = 0
    for table in KEYED_TABLES:
        if table not in tables:
            continue
        # One random key per row: SQL can't make a distinct one per row portably.
        ids = db.session.execute(
            sa.text(f"SELECT id FROM {table} WHERE public_key IS NULL")).scalars().all()
        for row_id in ids:
            db.session.execute(sa.text(f"UPDATE {table} SET public_key = :key WHERE id = :id"),
                               {"key": uuid.uuid4().hex, "id": row_id})
        keyed += len(ids)
    if applied or keyed:
        db.session.commit()
        logger.info("showcase: applied %s column migration(s), keyed %s row(s).", applied, keyed)
