"""
Schema changes for this module.

Same contract as `documents/migrations.py` and `communications/migrations.py`:
`db.create_all()` adds missing *tables* but never missing *columns*, so a
column added after `ai_settings` first shipped needs an entry in
`ADDED_COLUMNS` here — not in the root `models.py` list, which must not have
to know what this module stores. Called from app.py, the composition root,
alongside the other module migrations.

`ADDED_COLUMNS` is empty: `ai_settings` is a brand-new table, so
`db.create_all()` covers it whole. The list stays so the next column lands
in the right place rather than being reasoned about from scratch (hard rule
12).

What this file does carry is one **data** migration — refreshing a stored
prompt that's still verbatim a superseded default. Same class of thing as
communications' `_backfill_read_messages()`: safe on every boot, and a
no-op once there's nothing left matching.
"""

import logging

import sqlalchemy as sa

from models import db

from ai import config, services

logger = logging.getLogger(__name__)

ADDED_COLUMNS: list[tuple[str, str, str]] = []


def _refresh_unedited_default_prompts() -> None:
    """Move companies still on a superseded default onto the current one.

    The rule is narrow on purpose: a prompt is only rewritten when it
    matches a previous default **byte for byte**, which can only be true if
    nobody ever edited it. A company that changed so much as a word keeps
    exactly what it wrote — this must never overwrite someone's own text.

    Without it, the only way off an old default is to blank the field, and
    a company that never realised the default had changed would stay on the
    old one forever, wondering why replies say "we".
    """
    if "ai_settings" not in set(sa.inspect(db.engine).get_table_names()):
        return

    # Compared **normalised**, not raw. A browser submits textarea content
    # with CRLF line endings while the defaults in config.py use LF, so a
    # company that opened Settings → AI and pressed Save without changing a
    # word holds a byte-different copy of the very prompt it never edited.
    # A raw `WHERE reply_prompt IN (...)` misses exactly those rows — which
    # is every row that has ever been through the form. Found against a real
    # database; services.normalise_newlines stops new rows drifting the same
    # way, and this catches the ones already saved.
    superseded = {services.normalise_newlines(p) for p in config.SUPERSEDED_REPLY_PROMPTS}
    rows = db.session.execute(
        sa.text("SELECT id, reply_prompt FROM ai_settings")).fetchall()
    stale = [
        row.id for row in rows
        if services.normalise_newlines(row.reply_prompt) in superseded
    ]
    if not stale:
        return

    db.session.execute(
        sa.text("UPDATE ai_settings SET reply_prompt = :current WHERE id IN :ids")
        .bindparams(sa.bindparam("ids", expanding=True)),
        {"current": config.DEFAULT_REPLY_PROMPT, "ids": stale},
    )
    db.session.commit()
    logger.info("ai: refreshed %s unedited default reply prompt(s).", len(stale))


def run_migrations() -> None:
    _refresh_unedited_default_prompts()

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
