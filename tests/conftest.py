"""
Shared fixtures.

**The environment setup below must run before `app` is imported**, and that
is not a style preference. `app.py` calls `db.create_all()` at module level
inside an app context, so Flask-SQLAlchemy builds and caches its engine
during the import — setting `SQLALCHEMY_DATABASE_URI` afterwards looks like
it works and silently writes to the real `data/atelier.db`. Hence the
`DATABASE_URL` env var, set here at module scope, plus the assertion in
`_app` as a second line of defence.

Isolation strategy: one SQLite file per test session, schema dropped and
recreated per test. A rollback-per-test fixture was tried first and does
not work here — the services deliberately commit (sending mail and
creating calendar events are irreversible externally, so their local
record must not be lost by a caller rolling back), and those commits
escaped the wrapping transaction and leaked rows between tests. Recreating
the schema is slower in principle and imperceptible in practice at this
table count.
"""

import os
import tempfile

# --- must precede `import app` -------------------------------------------
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="atelier-test-")
os.close(_DB_FD)
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH.replace("\\", "/")
os.environ["SECRET_KEY"] = "test-secret-key"
# A real Fernet key, so tests exercise the production path rather than the
# SECRET_KEY-derived dev fallback. Fixed rather than generated so a failure
# is reproducible.
os.environ["COMMS_ENCRYPTION_KEY"] = "8ZQq1kzXKQ0Y9m4pJvVQ0j7cQ0K9zX2mQ8dHn1tYb3E="
# The AI module's own box, for the same reason — and deliberately a
# *different* key from the one above, so a test that accidentally encrypted
# under the wrong box would fail rather than round-trip.
os.environ["AI_ENCRYPTION_KEY"] = "pQ3vN8xR1sT6yU9wA2dF5gH0jK4lZ7mB1nC8vX3zQ5E="
# Set explicitly rather than popped. app.py calls load_dotenv() during import,
# and while that never *overrides* an existing variable (which is what keeps
# the values above authoritative), it does fill in ones we left unset — so a
# developer's .env containing RUN_SCHEDULER=1 would otherwise start a real
# scheduler inside the test suite.
os.environ["RUN_SCHEDULER"] = "0"
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id"
os.environ["GOOGLE_CLIENT_SECRET"] = "test-client-secret"
os.environ["GOOGLE_REDIRECT_URI"] = "http://localhost:5000/integrations/google/callback"
# -------------------------------------------------------------------------

import pytest  # noqa: E402

import app as app_module  # noqa: E402
from models import Client, Company, Order, OrderLine, User, db  # noqa: E402

from billing.services import invoicing  # noqa: E402

from communications.models import EmailAccount, EmailMessage, EmailThread, utcnow  # noqa: E402

ALL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify "
    "https://www.googleapis.com/auth/gmail.send "
    "https://www.googleapis.com/auth/calendar"
)
MAIL_ONLY_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify "
    "https://www.googleapis.com/auth/gmail.send"
)


@pytest.fixture(scope="session")
def _app():
    flask_app = app_module.app
    assert _DB_PATH.replace("\\", "/") in flask_app.config["SQLALCHEMY_DATABASE_URI"], (
        "Tests are pointed at the real database. DATABASE_URL must be set "
        "before `import app` — see this file's docstring."
    )
    flask_app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with flask_app.app_context():
        db.drop_all()
        db.create_all()
    yield flask_app
    with flask_app.app_context():
        db.session.remove()
        db.engine.dispose()
    try:
        os.unlink(_DB_PATH)
    except OSError:
        pass  # Windows keeps the handle briefly; the temp dir cleans it up


@pytest.fixture
def app(_app):
    """App context over an empty database."""
    with _app.app_context():
        db.session.remove()
        db.drop_all()
        db.create_all()
        yield _app
        db.session.remove()


def _with_billing_profile(row, prefix, **letterhead):
    """Company plus its billing profile.

    The invoice letterhead (prefix, address, registration numbers) belongs
    to the billing module, not to Company — so it's set through that
    module's API rather than as constructor kwargs.
    """
    db.session.add(row)
    db.session.flush()
    invoicing.update_profile(row.id, row.name, invoice_prefix=prefix, **letterhead)
    db.session.flush()
    return row


@pytest.fixture
def company(app):
    return _with_billing_profile(
        Company(name="By Monsieur"), "BM", gst_number="123 RT0001")


@pytest.fixture
def other_company(app):
    """A second tenant. Every isolation test needs one of these."""
    return _with_billing_profile(Company(name="Other Studio"), "OS")


@pytest.fixture
def user(company):
    row = User(company_id=company.id, username="admin")
    row.set_password("changeme")
    db.session.add(row)
    db.session.flush()
    return row


@pytest.fixture
def client_record(company):
    """A Client with a known email, so matching has something to match."""
    row = Client(
        company_id=company.id, first_name="Marie", last_name="Alarie",
        email="marie@example.com", phone="514-555-0142", province="QC",
    )
    db.session.add(row)
    db.session.flush()
    return row


@pytest.fixture
def account(company):
    row = EmailAccount(
        company_id=company.id, provider="gmail",
        email_address="studio@example.com", display_name="Studio",
        is_default=True, granted_scopes=ALL_SCOPES,
    )
    row.access_token = "access-token"
    row.refresh_token = "refresh-token"
    db.session.add(row)
    db.session.flush()
    return row


@pytest.fixture
def thread(company, account, client_record):
    """A stored thread with one incoming message, matched to a client."""
    row = EmailThread(
        company_id=company.id, email_account_id=account.id,
        client_id=client_record.id, provider_thread_id="t-1",
        subject="Briefcase timeline", last_message_date=utcnow(),
    )
    db.session.add(row)
    db.session.flush()
    db.session.add(EmailMessage(
        thread_id=row.id, provider_message_id="m-1",
        sender="marie@example.com", sender_name="Marie Alarie",
        recipients="studio@example.com", subject="Briefcase timeline",
        body_text="Any update on the briefcase?", received_date=utcnow(),
        direction="incoming",
    ))
    db.session.flush()
    return row


@pytest.fixture
def lead_thread(company, account):
    """A thread from an address no Client has — the lead-inbox case."""
    row = EmailThread(
        company_id=company.id, email_account_id=account.id,
        provider_thread_id="t-lead", subject="Messenger bag enquiry",
        last_message_date=utcnow(),
    )
    db.session.add(row)
    db.session.flush()
    db.session.add(EmailMessage(
        thread_id=row.id, provider_message_id="m-lead",
        sender="stranger@example.com", sender_name="Jean Tremblay",
        recipients="studio@example.com", subject="Messenger bag enquiry",
        body_text="Hello, do you make messenger bags?",
        received_date=utcnow(), direction="incoming",
    ))
    db.session.flush()
    return row


@pytest.fixture
def order(client_record):
    from datetime import date

    row = Order(
        client_id=client_record.id, item="Full-grain briefcase",
        start=date(2026, 7, 1), due=date(2026, 7, 15), status="confirmed",
    )
    db.session.add(row)
    db.session.flush()
    db.session.add(OrderLine(
        order_id=row.id, description="Briefcase", quantity=1, unit_price=760.0,
    ))
    db.session.flush()
    return row


@pytest.fixture
def logged_in(app, user):
    """A test client with an authenticated session."""
    with app.test_client() as test_client:
        test_client.post(
            "/login", data={"username": "admin", "password": "changeme"},
            follow_redirects=True,
        )
        yield test_client


@pytest.fixture
def csrf(logged_in):
    """A valid CSRF token for the logged-in session.

    Fetched by visiting a page that mints one, rather than writing the
    session directly — that way the tests exercise the same path the
    templates do.
    """
    logged_in.get("/settings/integrations")
    with logged_in.session_transaction() as session:
        return session.get("comms_csrf_token")
