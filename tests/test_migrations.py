"""
The module's own column migrations.

Worth real tests because this is the one part of the module that only ever
runs against a database created by an *older* version of the code. The
fixtures build the current schema with `create_all`, so a test that doesn't
deliberately take the columns away proves nothing.
"""

import sqlalchemy as sa

from models import db

from communications import migrations
from communications.models import EmailThread


def dismissal_columns():
    return {c["name"] for c in sa.inspect(db.engine).get_columns("email_threads")}


def drop_the_new_columns():
    """Recreate the pre-dismissal shape of email_threads.

    DROP COLUMN needs SQLite 3.35+, which every runtime this project targets
    is well past — the same assumption _migrate_order_price_to_lines() in the
    root models.py already makes.
    """
    for column, _ in [("dismissed_at", None), ("dismissed_reason", None)]:
        db.session.execute(sa.text(f"ALTER TABLE email_threads DROP COLUMN {column}"))
    db.session.commit()


def test_every_listed_column_targets_a_real_table(app):
    """A typo'd table name would make the migration silently skip forever."""
    tables = set(sa.inspect(db.engine).get_table_names())
    for table, _, _ in migrations.ADDED_COLUMNS:
        assert table in tables


def test_the_dismissal_columns_are_listed(app):
    """`email_threads` shipped before dismissal existed, so create_all won't
    add these to an existing database — only this list will."""
    listed = {(table, column) for table, column, _ in migrations.ADDED_COLUMNS}
    assert ("email_threads", "dismissed_at") in listed
    assert ("email_threads", "dismissed_reason") in listed


def test_migration_adds_missing_columns(app):
    drop_the_new_columns()
    assert "dismissed_at" not in dismissal_columns()

    migrations.run_migrations()

    assert {"dismissed_at", "dismissed_reason"} <= dismissal_columns()


def test_migration_is_a_noop_when_already_applied(app):
    """Runs on every boot, so it has to be safe to run repeatedly."""
    migrations.run_migrations()
    migrations.run_migrations()
    assert {"dismissed_at", "dismissed_reason"} <= dismissal_columns()


def test_existing_rows_survive_the_migration(app, company, account, lead_thread):
    """The dismissal columns are nullable with no default, so a thread that
    predates them reads as "not dismissed" rather than breaking."""
    db.session.commit()
    thread_id = lead_thread.id

    drop_the_new_columns()
    migrations.run_migrations()

    db.session.expire_all()
    restored = db.session.get(EmailThread, thread_id)
    assert restored is not None
    assert restored.dismissed_at is None
    assert restored.is_dismissed is False


def test_a_migrated_thread_can_then_be_dismissed(app, company, account, lead_thread):
    """End to end: an old database gets the columns, and the feature works on
    rows that existed before it did."""
    from communications.services import email_service

    db.session.commit()
    thread_id = lead_thread.id

    drop_the_new_columns()
    migrations.run_migrations()
    db.session.expire_all()

    email_service.dismiss_thread(company.id, thread_id)
    assert email_service.lead_threads(company.id) == []
