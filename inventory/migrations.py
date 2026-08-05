"""
Schema changes for this module.

Same contract as `documents/migrations.py`: `db.create_all()` adds missing
*tables* but never missing *columns*, so a column added after one of this
module's tables first shipped needs an entry in `ADDED_COLUMNS` here. Called
from app.py, the composition root, right after the other module migrations.

`inventory_units` is a brand new table (added alongside the customizable
Units settings section), so `db.create_all()` covers it with no
`ADDED_COLUMNS` entry needed — same as every other table this module owns.
It does need a **data** backfill, though: see `_seed_units_from_existing_items`.
"""

import logging

import sqlalchemy as sa

from models import db

from inventory.config import DEFAULT_UNIT
from inventory.models import InventoryItem, InventoryUnit

logger = logging.getLogger(__name__)

ADDED_COLUMNS: list[tuple[str, str, str]] = [
    # The low-stock warning point on an item, added after inventory_items
    # first shipped (see InventoryItem.low_stock_threshold). `db.create_all()`
    # adds missing tables but never missing columns, so an installation that
    # predates this feature needs the ALTER here. NOT NULL DEFAULT 0 backfills
    # every existing row to "no warning point set" — the exact behaviour those
    # items had before the column existed (only the hard zero/negative signal
    # applies until someone sets a threshold).
    ("inventory_items", "low_stock_threshold", "FLOAT NOT NULL DEFAULT 0"),
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
        logger.info("inventory: applied %s column migration(s).", applied)

    if "inventory_units" in tables and "companies" in tables:
        _seed_default_unit_for_every_company()
    if "inventory_units" in tables and "inventory_items" in tables:
        _seed_units_from_existing_items()


def _seed_default_unit_for_every_company() -> None:
    """Every company always has "Each" selectable — including one that's
    never created a single inventory item, so there's nothing for
    `_seed_units_from_existing_items` below to notice. Same guarantee
    `services._ensure_default_unit` makes lazily on first read; this just
    means it's already there the first time anyone opens the Units section,
    rather than depending on that page being the thing that creates it.
    Reads company ids with raw SQL rather than importing the host's
    `Company` model, same "modules only import `db`" convention every other
    module's migrations follow. No-op once every company has one.

    Runs before `_seed_units_from_existing_items` so a company's "Each" row
    (and its sort_order, appended after whatever it already has) always
    exists before that function might otherwise need to invent one.
    """
    company_ids = [row[0] for row in db.session.execute(sa.text("SELECT id FROM companies")).all()]
    already_have = {
        row.company_id for row in
        InventoryUnit.query.filter_by(key=DEFAULT_UNIT).with_entities(InventoryUnit.company_id).all()
    }
    missing = [company_id for company_id in company_ids if company_id not in already_have]
    if not missing:
        return
    for company_id in missing:
        next_sort_order = InventoryUnit.query.filter_by(company_id=company_id).count()
        db.session.add(InventoryUnit(company_id=company_id, key=DEFAULT_UNIT, sort_order=next_sort_order))
    db.session.commit()
    logger.info("inventory: seeded the default unit for %s compan(y/ies).", len(missing))


def _seed_units_from_existing_items() -> None:
    """Give every unit an `InventoryItem` already carries a real row in the
    company's Units list — not just a value the app happens to tolerate.

    Every item created before this feature existed has a literal `unit`
    string on file (e.g. "sqft") with no matching `InventoryUnit` row. Left
    alone, a company would open Settings -> Inventory and see only "Each",
    even though "Sqft" is clearly already in active use on their own
    inventory. This backfills one `InventoryUnit` per distinct
    `(company_id, unit)` pair actually on file that doesn't already have a
    row, so upgrading surfaces existing usage instead of hiding it.

    "Each" is already guaranteed by `_seed_default_unit_for_every_company`
    above, so in practice this only ever backfills non-default units — every
    backfilled unit is appended after whatever the company already has
    configured. No-op once every pair has a row, safe on every boot.
    """
    existing_pairs = {
        (row.company_id, row.key)
        for row in InventoryUnit.query.with_entities(InventoryUnit.company_id, InventoryUnit.key).all()
    }
    used_pairs = {
        (row.company_id, row.unit)
        for row in InventoryItem.query.with_entities(InventoryItem.company_id, InventoryItem.unit).all()
    }
    missing = sorted(used_pairs - existing_pairs)
    if not missing:
        return

    next_sort_order: dict[int, int] = {}
    for company_id, unit in missing:
        if company_id not in next_sort_order:
            next_sort_order[company_id] = InventoryUnit.query.filter_by(company_id=company_id).count()
        db.session.add(InventoryUnit(company_id=company_id, key=unit, sort_order=next_sort_order[company_id]))
        next_sort_order[company_id] += 1
    db.session.commit()
    logger.info("inventory: backfilled %s unit row(s) from existing items.", len(missing))
