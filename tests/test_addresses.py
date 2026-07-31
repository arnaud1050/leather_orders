"""
Structured addresses, and the migration that produced them.

`province` stopped being cosmetic when tax became destination-based — it
decides what a client is charged. That's why the migration below refuses
to guess: filing someone in the wrong province silently mis-bills them,
which is worse than an address that visibly needs re-typing.
"""

import pytest
import sqlalchemy as sa

import billing.migrations as billing_migrations
from billing.documents import format_address
from billing.services import invoicing
from models import Client, db, run_migrations


# --- Rendering ------------------------------------------------------------

def test_full_address_uses_the_canada_post_layout():
    """Street on its own line, then "City, PROV  Postal" — two spaces
    before the postal code."""
    assert format_address("4820 rue Sainte-Catherine E", "Montréal", "QC", "H1V 1M6") == (
        "4820 rue Sainte-Catherine E\nMontréal, QC  H1V 1M6"
    )


def test_street_only():
    assert format_address("12 Main St", None, None, None) == "12 Main St"


def test_city_and_province_only():
    assert format_address(None, "Montréal", "QC", None) == "Montréal, QC"


def test_city_only():
    assert format_address(None, "Montréal", None, None) == "Montréal"


def test_province_and_postal_without_a_city():
    assert format_address(None, None, "QC", "H1V 1M6") == "QC  H1V 1M6"


def test_nothing_at_all_is_none():
    """None rather than "" so callers can skip the whole block instead of
    printing an empty line on the invoice."""
    assert format_address(None, None, None, None) is None
    assert format_address("", "", "", "") is None


def test_seller_and_buyer_render_the_same_way(company):
    """The seller's address lives on the billing profile, the buyer's on
    the client — both go through the same helper, so both print alike."""
    profile = invoicing.update_profile(
        company.id, company.name, street="12 Main St", city="Montréal",
        province="QC", postal_code="H1V 1M6")
    row = Client(company_id=company.id, first_name="A", last_name="B",
                 street="12 Main St", city="Montréal", province="QC",
                 postal_code="H1V 1M6")
    db.session.add(row)
    db.session.flush()
    assert profile.formatted_address == row.formatted_address


# --- The free-text -> structured migration --------------------------------

def add_legacy_address_column(table, value, row_id=1):
    """Put the table back into its pre-split shape and fill it in."""
    db.session.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN address TEXT"))
    db.session.execute(
        sa.text(f"UPDATE {table} SET address = :value, street = NULL, city = NULL, "
                "province = NULL, postal_code = NULL WHERE id = :id"),
        {"value": value, "id": row_id},
    )
    db.session.commit()


def columns(table):
    return {c["name"] for c in sa.inspect(db.engine).get_columns(table)}


def test_a_well_formed_address_is_split_into_its_parts(company, client_record):
    db.session.commit()
    add_legacy_address_column(
        "clients", "1240 rue Saint-Denis\nMontréal, QC H2X 3J5", client_record.id)

    run_migrations()
    db.session.expire_all()

    row = db.session.get(Client, client_record.id)
    assert row.street == "1240 rue Saint-Denis"
    assert row.city == "Montréal"
    assert row.province == "QC"
    assert row.postal_code == "H2X 3J5"


def test_the_split_recovers_a_province_that_tax_depends_on(company, client_record):
    """The point of parsing at all: a migrated client keeps being charged
    the right tax instead of silently dropping to zero."""
    company.gst_number = "123456789 RT0001"
    db.session.commit()
    add_legacy_address_column(
        "clients", "914 Queen St W\nToronto, ON M6J 1G6", client_record.id)

    run_migrations()
    db.session.expire_all()

    assert db.session.get(Client, client_record.id).province == "ON"


def test_a_multi_line_street_keeps_all_of_its_lines(company, client_record):
    db.session.commit()
    add_legacy_address_column(
        "clients", "780 Bute St\nApt 1104\nVancouver, BC V6E 1Y9", client_record.id)

    run_migrations()
    db.session.expire_all()

    row = db.session.get(Client, client_record.id)
    assert row.street == "780 Bute St, Apt 1104"
    assert row.city == "Vancouver"
    assert row.province == "BC"


def test_an_unparseable_address_survives_whole_in_the_street_field(
    company, client_record
):
    """Nothing is thrown away, and it reads visibly wrong in the UI —
    which is the intended prompt to re-enter it."""
    db.session.commit()
    add_legacy_address_column("clients", "care of the studio\nask for Marie",
                              client_record.id)

    run_migrations()
    db.session.expire_all()

    row = db.session.get(Client, client_record.id)
    assert row.street == "care of the studio, ask for Marie"
    assert row.city is None
    assert row.province is None


def test_an_unparseable_address_does_not_invent_a_province(company, client_record):
    """A wrong guess here would change the tax charged, so no guess is
    made at all."""
    db.session.commit()
    add_legacy_address_column("clients", "Somewhere in Ontario", client_record.id)

    run_migrations()
    db.session.expire_all()

    assert db.session.get(Client, client_record.id).province is None


def test_the_legacy_column_is_dropped(company, client_record):
    db.session.commit()
    add_legacy_address_column("clients", "12 Main St\nMontréal, QC H1V 1M6",
                              client_record.id)
    assert "address" in columns("clients")

    run_migrations()

    assert "address" not in columns("clients")


def test_a_legacy_company_address_moves_into_the_billing_profile(company):
    """The seller's address left `companies` with the billing module, so
    it's that module's migration that has to rescue an old one — whole, in
    `street`, since guessing a province would change the tax charged."""
    invoicing.update_profile(company.id, company.name, street=None)
    db.session.commit()
    db.session.execute(sa.text("ALTER TABLE companies ADD COLUMN address TEXT"))
    db.session.execute(
        sa.text("UPDATE companies SET address = :value WHERE id = :id"),
        {"value": "4820 rue Sainte-Catherine E\nMontréal, QC H1V 1M6",
         "id": company.id},
    )
    db.session.commit()

    billing_migrations.run()
    db.session.expire_all()

    profile = invoicing.profile_for(company.id)
    assert profile.street == "4820 rue Sainte-Catherine E, Montréal, QC H1V 1M6"
    assert profile.province is None  # not guessed
    assert "address" not in columns("companies")


def test_the_migration_is_a_noop_once_applied(company, client_record):
    db.session.commit()
    add_legacy_address_column("clients", "12 Main St\nMontréal, QC H1V 1M6",
                              client_record.id)
    run_migrations()
    db.session.expire_all()
    before = db.session.get(Client, client_record.id).formatted_address

    run_migrations()  # must not raise, must not change anything
    db.session.expire_all()

    assert db.session.get(Client, client_record.id).formatted_address == before


def test_a_row_that_already_has_a_street_is_left_alone(company, client_record):
    """Guards against a re-run overwriting hand-corrected data."""
    db.session.commit()
    db.session.execute(sa.text("ALTER TABLE clients ADD COLUMN address TEXT"))
    db.session.execute(
        sa.text("UPDATE clients SET address = :old, street = :new WHERE id = :id"),
        {"old": "stale free text", "new": "hand-corrected street",
         "id": client_record.id},
    )
    db.session.commit()

    run_migrations()
    db.session.expire_all()

    assert db.session.get(Client, client_record.id).street == "hand-corrected street"


@pytest.mark.parametrize("postal", ["H1V 1M6", "h1v 1m6", "H1V1M6"])
def test_postal_code_spacing_variants_are_recognised(company, client_record, postal):
    db.session.commit()
    add_legacy_address_column("clients", f"12 Main St\nMontréal, QC {postal}",
                              client_record.id)

    run_migrations()
    db.session.expire_all()

    row = db.session.get(Client, client_record.id)
    assert row.province == "QC"
    assert row.postal_code == postal.upper()
