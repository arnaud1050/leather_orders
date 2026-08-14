"""
The `users` table rebuild — username out, email in.

This is the only migration in the project that replaces a table rather
than extending one, because SQLite can neither add a UNIQUE column nor
drop the inline UNIQUE that `username` carried. It runs exactly once, on
databases created by an older version of the code, and every fixture here
builds that older shape by hand — a test against `create_all`'s current
schema would prove nothing at all.
"""

import pytest
import sqlalchemy as sa

from models import User, _users_company_id_is_nullable, db, run_migrations

LEGACY_USERS = (
    "CREATE TABLE users ("
    " id INTEGER NOT NULL,"
    " company_id INTEGER NOT NULL,"
    " username VARCHAR(80) NOT NULL UNIQUE,"
    " password_hash VARCHAR(255) NOT NULL,"
    " signature TEXT,"
    " PRIMARY KEY (id),"
    " FOREIGN KEY(company_id) REFERENCES companies (id))"
)


def build_legacy_users(company_id, rows):
    """Replace the current `users` with the pre-email shape, and fill it.

    `rows` is a list of (id, username, signature).
    """
    db.session.execute(sa.text("DROP TABLE users"))
    db.session.execute(sa.text(LEGACY_USERS))
    for user_id, username, signature in rows:
        db.session.execute(sa.text(
            "INSERT INTO users (id, company_id, username, password_hash, signature)"
            " VALUES (:id, :company_id, :username, 'hash-placeholder', :signature)"
        ), {"id": user_id, "company_id": company_id,
            "username": username, "signature": signature})
    db.session.commit()


def user_columns():
    return {c["name"] for c in sa.inspect(db.engine).get_columns("users")}


def read_users():
    return db.session.execute(sa.text(
        "SELECT id, company_id, email, full_name, signature, is_active,"
        " is_platform_admin FROM users ORDER BY id"
    )).all()


def test_the_rebuild_swaps_the_columns(app, company):
    build_legacy_users(company.id, [(1, "admin", None)])
    assert "username" in user_columns()

    run_migrations()

    columns = user_columns()
    assert "username" not in columns
    assert {"email", "full_name", "is_active", "is_platform_admin"} <= columns


def test_a_plain_username_is_parked_at_an_unreachable_domain(app, company):
    """`.invalid` is reserved by RFC 2606, so a backfilled address is
    incapable of being someone's real mailbox — it reads as a placeholder
    because it is one."""
    build_legacy_users(company.id, [(1, "admin", None)])

    run_migrations()

    assert read_users()[0].email == "admin@example.invalid"


def test_a_username_that_was_already_an_address_is_kept(app, company):
    build_legacy_users(company.id, [(1, "Marie@Example.com", None)])

    run_migrations()

    assert read_users()[0].email == "marie@example.com"


def test_the_old_username_survives_as_the_display_name(app, company):
    """Nobody's account should stop being recognisable on the day this runs."""
    build_legacy_users(company.id, [(1, "admin", None)])

    run_migrations()

    assert read_users()[0].full_name == "admin"


def test_case_colliding_usernames_do_not_break_the_boot(app, company):
    """SQLite's UNIQUE was case-sensitive, so `Admin` and `admin` could both
    exist and fold onto one address. An ugly address someone can edit beats
    a deployment that won't start."""
    build_legacy_users(company.id, [(1, "Admin", None), (2, "admin", None)])

    run_migrations()

    emails = [row.email for row in read_users()]
    assert len(set(emails)) == 2
    assert emails[0] == "admin@example.invalid"
    assert "2" in emails[1]


def test_signatures_and_ids_survive(app, company):
    """Ids especially: communications' audit log has a foreign key into
    this table, and a rebuild that renumbered rows would silently
    reattribute every entry in it."""
    build_legacy_users(company.id, [(1, "admin", "Sign here"), (7, "colleague", None)])

    run_migrations()

    rows = read_users()
    assert [row.id for row in rows] == [1, 7]
    assert rows[0].signature == "Sign here"


def test_everyone_comes_back_active(app, company):
    build_legacy_users(company.id, [(1, "admin", None), (2, "colleague", None)])

    run_migrations()

    assert all(row.is_active for row in read_users())


def test_nobody_is_promoted_to_platform_admin(app, company):
    """Everyone migrated stays a tenant user of the studio they were already
    in. A platform admin has no company, so promoting one here would mean
    silently detaching the studio's only login from its studio —
    `ensure_platform_admin()` adds the staff account alongside instead."""
    build_legacy_users(company.id, [(3, "colleague", None), (1, "admin", None)])

    run_migrations()

    rows = read_users()
    assert all(row.is_platform_admin == 0 for row in rows)
    assert all(row.company_id == company.id for row in rows)


# --- the second rebuild: company_id NOT NULL -> nullable ------------------

NOT_NULL_USERS = (
    "CREATE TABLE users ("
    " id INTEGER NOT NULL,"
    " company_id INTEGER NOT NULL,"
    " email VARCHAR(255) NOT NULL,"
    " full_name VARCHAR(120),"
    " password_hash VARCHAR(255) NOT NULL,"
    " signature TEXT,"
    " is_active BOOLEAN NOT NULL DEFAULT 1,"
    " is_platform_admin BOOLEAN NOT NULL DEFAULT 0,"
    " PRIMARY KEY (id),"
    " FOREIGN KEY(company_id) REFERENCES companies (id))"
)


def build_not_null_users(company_id, rows):
    """The shape the *first* platform-admin release wrote.

    Email was already the identity by then, so `_migrate_users_to_email()`
    won't fire on it — this database is past that migration and stuck
    behind the next one. `rows` is a list of (id, email, is_platform_admin).
    """
    db.session.execute(sa.text("DROP TABLE users"))
    db.session.execute(sa.text(NOT_NULL_USERS))
    db.session.execute(sa.text("CREATE UNIQUE INDEX ix_users_email ON users (email)"))
    for user_id, email, is_platform_admin in rows:
        db.session.execute(sa.text(
            "INSERT INTO users (id, company_id, email, password_hash,"
            " is_active, is_platform_admin)"
            " VALUES (:id, :company_id, :email, 'hash-placeholder', 1, :flag)"
        ), {"id": user_id, "company_id": company_id, "email": email,
            "flag": is_platform_admin})
    db.session.commit()


def test_a_company_less_user_can_be_inserted_afterwards(app, company):
    """The failure this exists to prevent, reproduced end to end.

    A database migrated by the first release has `company_id NOT NULL`.
    Making the *model* nullable doesn't relax a constraint SQLite has
    already written, and SQLite has no ALTER for it — so
    `ensure_platform_admin()` fails on insert and takes the whole boot
    down. This is the one test that would have caught that.
    """
    build_not_null_users(company.id, [(1, "admin@example.invalid", 1)])

    run_migrations()

    staff = User(company_id=None, email="platform@example.invalid",
                 is_platform_admin=True)
    staff.set_password("changeme")
    db.session.add(staff)
    db.session.commit()  # would raise IntegrityError before the migration

    assert db.session.get(User, staff.id).company_id is None


def test_the_relaxation_keeps_every_row(app, company):
    build_not_null_users(company.id, [
        (1, "admin@example.invalid", 1), (7, "colleague@example.test", 0),
    ])

    run_migrations()

    rows = read_users()
    assert [row.id for row in rows] == [1, 7]
    assert [row.email for row in rows] == [
        "admin@example.invalid", "colleague@example.test"]
    assert all(row.company_id == company.id for row in rows)


def test_the_relaxation_keeps_the_unique_index(app, company):
    """Dropping the table drops its indexes — the rebuild has to put this
    one back, or two tenants could share an address."""
    build_not_null_users(company.id, [(1, "admin@example.invalid", 1)])
    run_migrations()

    with pytest.raises(sa.exc.IntegrityError):
        db.session.execute(sa.text(
            "INSERT INTO users (id, company_id, email, password_hash,"
            " is_active, is_platform_admin) VALUES (2, :company_id,"
            " 'admin@example.invalid', 'x', 1, 0)"
        ), {"company_id": company.id})
    db.session.rollback()


def test_the_relaxation_runs_once(app, company):
    build_not_null_users(company.id, [(1, "admin@example.invalid", 1)])
    run_migrations()
    before = read_users()

    run_migrations()

    assert read_users() == before


def test_a_fresh_database_needs_no_relaxation(app, company):
    """create_all() already builds the nullable shape, so the check must
    not fire on it — a rebuild per boot would be silent, slow and pointless."""
    assert _users_company_id_is_nullable() is True


def test_the_email_rebuild_lands_on_the_nullable_shape(app, company):
    """Both rebuilds emit `_USERS_TABLE_DDL`, so a database coming from the
    username era skips the second migration entirely."""
    build_legacy_users(company.id, [(1, "admin", None)])

    run_migrations()

    assert _users_company_id_is_nullable() is True


def test_the_email_index_is_unique_afterwards(app, company):
    """The whole reason for a rebuild. Without it the column is merely
    unused rather than unconstrained, and a second tenant's duplicate
    address would sail in."""
    build_legacy_users(company.id, [(1, "admin", None)])
    run_migrations()

    with pytest.raises(sa.exc.IntegrityError):
        db.session.execute(sa.text(
            "INSERT INTO users (id, company_id, email, password_hash, is_active,"
            " is_platform_admin) VALUES (2, :company_id, 'admin@example.invalid',"
            " 'hash-placeholder', 1, 0)"
        ), {"company_id": company.id})
    db.session.rollback()


def test_the_rebuild_runs_once(app, company):
    """run_migrations() runs on every boot. The second pass must find no
    `username` column and leave the table alone."""
    build_legacy_users(company.id, [(1, "admin", None)])
    run_migrations()
    before = read_users()

    run_migrations()

    assert read_users() == before


def test_the_orm_can_read_the_migrated_table(app, company):
    """The rebuild writes DDL by hand, so it can drift from what the model
    expects without any other test noticing."""
    build_legacy_users(company.id, [(1, "admin", "Sign here")])
    run_migrations()
    db.session.expire_all()

    user = db.session.get(User, 1)
    assert user.email == "admin@example.invalid"
    assert user.display_name == "admin"
    assert user.signature == "Sign here"
    assert user.is_active is True
    assert user.is_platform_admin is False
    assert user.is_tenant_user is True
