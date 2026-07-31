"""
Schema changes for this module's own tables.

Separate from `run_migrations()` in the root models.py for the same reason
`communications/migrations.py` is: putting billing columns in the app's
list would mean the root model file has to know what this module stores,
which is the coupling the module layout exists to avoid.

Same contract as the others — every step is a no-op once applied, so this
is safe on every boot and on a fresh database. Called from app.py, the
composition root, after the app's own migrations (which may still be
splitting `companies.address` into parts this module then takes over).
"""

import sqlalchemy as sa

from models import db

from billing.models import BillingProfile, Invoice

# (table, column, DDL) for columns added to this module's tables after they
# first shipped. Appending here is the way to add another one.
ADDED_COLUMNS = [
    ("invoices", "issuer_name", "VARCHAR(120)"),
    ("invoices", "issuer_address", "TEXT"),
    ("invoices", "issuer_gst_number", "VARCHAR(40)"),
    ("invoices", "issuer_pst_number", "VARCHAR(40)"),
    ("invoices", "issuer_qst_number", "VARCHAR(40)"),
    ("invoices", "issuer_neq", "VARCHAR(40)"),
    ("invoices", "issuer_payment_instructions", "TEXT"),
    ("invoices", "issued_subtotal", "FLOAT"),
]

# Letterhead fields that used to live on the host's `companies` table,
# before this module owned them. (profile attribute, companies column)
_LETTERHEAD = [
    "invoice_prefix", "street", "city", "province", "postal_code",
    "gst_number", "pst_number", "qst_number", "neq", "payment_instructions",
]


def run() -> None:
    inspector = sa.inspect(db.engine)
    tables = set(inspector.get_table_names())
    existing = {t: {c["name"] for c in inspector.get_columns(t)} for t in tables}

    for table, column, ddl in ADDED_COLUMNS:
        if table in tables and column not in existing[table]:
            db.session.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
    db.session.commit()

    if "invoices" in tables and "order_id" in existing["invoices"]:
        _rename_order_id_to_subject_id()

    # "address" counts too: a database old enough to predate the
    # street/city/province split has only that, and no named letterhead
    # column would trigger the move.
    if "companies" in tables and (
        _letterhead_columns(existing["companies"]) or "address" in existing["companies"]
    ):
        _move_letterhead_off_companies(existing["companies"])

    _backfill_issuers()


def _backfill_issuers() -> None:
    """Freeze invoices that were issued before there was anything to freeze.

    Two halves, deliberately different:

    - Issuer details: today's profile is the only approximation available,
      so freeze that rather than let the invoice keep falling back to live
      values.
    - Money: freeze the subtotal, but write **no tax rows**. Those invoices
      were issued before tax was calculated at all, so zero tax is what
      their clients actually received; inventing tax now would change
      amounts already billed.

    The subtotal has to come from the host, which this module can't reach —
    so it's read straight from the frozen-in-time SQL sum of the subject's
    line items via the resolver the host installs. With none installed
    (a host that doesn't need it), the money half is skipped and only the
    issuer is frozen.
    """
    stale = Invoice.query.filter(
        Invoice.status != "draft",
        sa.or_(Invoice.issuer_name.is_(None), Invoice.issued_subtotal.is_(None)),
    ).all()
    if not stale:
        return

    for invoice in stale:
        if invoice.issuer_name is None:
            profile = BillingProfile.query.filter_by(company_id=invoice.company_id).first()
            if profile is not None:
                invoice.apply_issuer(profile.issuer)
        if invoice.issued_subtotal is None and _subtotal_resolver is not None:
            invoice.issued_subtotal = _subtotal_resolver(invoice.subject_id)
    db.session.commit()


# Set by the host so the backfill can price a subject it can't see. Kept
# module-level and optional rather than a required argument: a host with no
# legacy invoices never needs it.
_subtotal_resolver = None


def set_subtotal_resolver(resolver) -> None:
    """Install `subject_id -> subtotal`, used only by the backfill."""
    global _subtotal_resolver
    _subtotal_resolver = resolver


def _letterhead_columns(company_columns: set[str]) -> list[str]:
    return [name for name in _LETTERHEAD if name in company_columns]


def _rename_order_id_to_subject_id() -> None:
    """`invoices.order_id` predates this module.

    The column was named for the host's table back when invoicing lived in
    the app. It's `subject_id` here because this module doesn't know what
    it bills for — the foreign key still points at whatever
    `config.SUBJECT_FK` names, but the column no longer claims to.
    """
    db.session.execute(sa.text("ALTER TABLE invoices RENAME COLUMN order_id TO subject_id"))
    db.session.commit()


def _move_letterhead_off_companies(company_columns: set[str]) -> None:
    """Copy the seller's letterhead into billing_profiles, then drop it.

    The columns were on the host's `companies` table before this module
    existed. Dropping them is part of the migration rather than cleanup:
    leaving two copies means the next person to edit one wonders why the
    invoice didn't change.
    """
    present = _letterhead_columns(company_columns)
    # A database old enough to predate the street/city/province split has a
    # single free-text `address` instead. There's no reliable way to parse
    # one back into parts, and guessing a province would silently change
    # the tax charged — so it lands whole in `street`, visibly wrong until
    # someone re-enters it.
    legacy_address = "address" in company_columns and "street" not in company_columns
    if legacy_address:
        present = present + ["address"]
    if not present:
        return

    rows = db.session.execute(
        sa.text(f"SELECT id, {', '.join(present)} FROM companies")
    ).mappings().all()

    for row in rows:
        profile = BillingProfile.query.filter_by(company_id=row["id"]).first()
        if profile is None:
            profile = BillingProfile(company_id=row["id"])
            db.session.add(profile)
        for name in present:
            target = "street" if name == "address" else name
            # Don't overwrite a profile someone has already filled in.
            if getattr(profile, target, None) in (None, "") and row[name] is not None:
                value = row[name]
                if name == "address":
                    value = value.replace("\n", ", ")
                setattr(profile, target, value)
        if not profile.invoice_prefix:
            profile.invoice_prefix = "INV"
    db.session.flush()

    for name in present:
        db.session.execute(sa.text(f"ALTER TABLE companies DROP COLUMN {name}"))
    db.session.commit()
