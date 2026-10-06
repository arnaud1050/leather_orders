"""
Schema changes for `company_brands`, and the one-time move of logos out of
billing (BL15).

`db.create_all()` creates the table; a column added later goes in
`ADDED_COLUMNS` (hard rule 12).

**Adopting billing's logos.** Until the logo became the studio's, it lived
in billing: a `logo_filename` on `billing_profiles` and the file under
`BILLING_LOGO_DIR/<company_id>/`. On boot, every company with such a logo
and no brand row gets one: the file is copied into `BRAND_DIR` under the
same name, the brand row is written, and only then is billing's column
cleared and the old file removed. A failure part-way leaves billing's copy
in place, so the next boot simply tries again. Read with raw SQL on purpose:
this package must not import billing's models.
"""

import logging
import os
import re
import shutil

import sqlalchemy as sa

from brand import config
from brand.models import CompanyBrand
from models import db

logger = logging.getLogger(__name__)

ADDED_COLUMNS: list[tuple[str, str, str]] = []

# What billing's logos.save() ever generated: 32 hex characters + ".png".
# Anything else in that column is not a name this code will build a path from.
_STORED_NAME = re.compile(r"^[0-9a-f]{32}\.png$")


def run_migrations() -> None:
    inspector = sa.inspect(db.engine)
    tables = set(inspector.get_table_names())
    applied = 0
    for table, column, ddl in ADDED_COLUMNS:
        if table in tables and column not in {c["name"] for c in inspector.get_columns(table)}:
            db.session.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            applied += 1
    if applied:
        db.session.commit()


def adopt_billing_logos(legacy_dir: str) -> int:
    """Move each company's invoice logo into its brand. Idempotent; returns
    how many logos were moved this time."""
    inspector = sa.inspect(db.engine)
    if "billing_profiles" not in inspector.get_table_names():
        return 0
    if "logo_filename" not in {c["name"] for c in inspector.get_columns("billing_profiles")}:
        return 0
    rows = db.session.execute(sa.text(
        "SELECT company_id, logo_filename FROM billing_profiles "
        "WHERE logo_filename IS NOT NULL AND logo_filename != ''"
    )).all()

    moved = 0
    for company_id, name in rows:
        source = os.path.join(legacy_dir, str(int(company_id)), name)
        brand = db.session.get(CompanyBrand, company_id)
        if brand is None or not brand.logo_filename:
            if not _STORED_NAME.match(name or "") or not os.path.isfile(source):
                # Nothing usable to move: a name billing never wrote, or a
                # file already gone. Clear the column so it isn't retried.
                logger.warning("brand: no logo file to adopt for company %s", company_id)
            else:
                target_dir = os.path.join(config.BRAND_DIR, str(int(company_id)))
                os.makedirs(target_dir, exist_ok=True)
                shutil.copy2(source, os.path.join(target_dir, name))
                if brand is None:
                    brand = CompanyBrand(company_id=company_id)
                    db.session.add(brand)
                brand.logo_filename = name
                moved += 1
        db.session.execute(
            sa.text("UPDATE billing_profiles SET logo_filename = NULL WHERE company_id = :c"),
            {"c": company_id},
        )
        db.session.commit()
        if os.path.isfile(source):
            try:
                os.remove(source)
            except OSError:
                pass  # read-only volume: harmless, nothing reads it any more
    if moved:
        logger.info("brand: adopted %s logo(s) from billing.", moved)
    return moved
