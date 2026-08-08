"""
The "an integration stopped syncing" badge.

Derived from `EmailAccount.last_sync_error`, which every sync path already
sets and a successful one clears — so the badge can't disagree with what the
integrations page says, and nothing has to remember to keep a health flag up
to date. Same reasoning as the new-leads badge and `Invoice.display_status`.
"""

from models import User, db

from communications.models import EmailAccount
from communications.services import account_service

from tests import fakes


def fail(account, message="invalid_grant: token revoked"):
    account.last_sync_error = message
    db.session.commit()


# --- the service ----------------------------------------------------------

def test_a_healthy_account_is_not_failing(company, account):
    assert account_service.failing_accounts(company.id) == []


def test_an_account_with_a_sync_error_is_failing(company, account):
    fail(account)
    assert account_service.failing_accounts(company.id) == [account]


def test_a_paused_account_still_counts(company, account):
    """Turning sync off doesn't repair whatever broke."""
    fail(account)
    account.sync_enabled = False
    db.session.commit()
    assert account_service.failing_accounts(company.id) == [account]


def test_an_empty_error_string_is_not_a_failure(company, account):
    account.last_sync_error = ""
    db.session.commit()
    assert account_service.failing_accounts(company.id) == []


def test_failures_are_tenant_scoped(company, other_company, account):
    theirs = EmailAccount(
        company_id=other_company.id, provider="gmail",
        email_address="theirs@example.com", last_sync_error="broken",
    )
    db.session.add(theirs)
    db.session.commit()

    assert account_service.failing_accounts(company.id) == []
    assert account_service.failing_accounts(other_company.id) == [theirs]


def test_a_successful_sync_clears_the_failure(company, account):
    fail(account)
    with fakes.fake_providers(threads=[fakes.thread()]):
        from communications.sync import email_sync
        email_sync.sync_account(account)

    assert account_service.failing_accounts(company.id) == []


def test_a_failed_sync_raises_the_failure(company, account):
    with fakes.fake_providers(email_error=fakes.provider_error("Gmail is down")):
        from communications.sync import email_sync
        email_sync.sync_account(account)

    assert account_service.failing_accounts(company.id) == [account]


# --- the badge in the UI --------------------------------------------------

def test_no_badge_while_everything_syncs(logged_in, account):
    """No decoration at all when nothing is wrong."""
    assert "nav-badge--alert" not in logged_in.get("/").get_data(as_text=True)


def test_the_badge_shows_in_the_top_nav_on_any_page(logged_in, account):
    """Visible from the timeline: a broken integration shouldn't need going
    looking for, which is the whole point of putting it in the top nav."""
    fail(account)
    body = logged_in.get("/").get_data(as_text=True)
    assert "nav-badge--alert" in body
    assert "1 integration stopped syncing" in body


def test_the_badge_shows_on_the_settings_sub_nav(logged_in, account):
    fail(account)
    body = logged_in.get("/settings/general").get_data(as_text=True)
    # Twice: once on the top nav's Settings link, once beside Integrations.
    assert body.count("nav-badge--alert") == 2


def test_the_badge_sits_inside_the_email_calendar_link(logged_in, account):
    """The renamed tab keeps its badge. "Integrations" became
    "Email/Calendar" when the AI category arrived beside it, and a label
    change that quietly left the alert behind on the old markup would be
    invisible until the day something actually stopped syncing."""
    fail(account)
    body = logged_in.get("/settings/general").get_data(as_text=True)
    link = body.split('href="/settings/integrations"')[1].split("</a>")[0]
    assert "Email/Calendar" in link
    assert "nav-badge--alert" in link


def test_the_badge_shows_on_the_new_ai_settings_page_too(logged_in, account):
    """Every settings category shares one nav, so a module-owned page can't
    be the one place the alert goes missing."""
    fail(account)
    body = logged_in.get("/settings/ai").get_data(as_text=True)
    assert body.count("nav-badge--alert") == 2


def test_the_badge_reaches_the_integrations_page_itself(logged_in, account):
    """The module owns that template but shares _settings_nav.html, so the
    trail from the badge to the page explaining it can't break."""
    fail(account)
    body = logged_in.get("/settings/integrations").get_data(as_text=True)
    assert "nav-badge--alert" in body


def test_the_badge_counts_several_failures(logged_in, company, account):
    second = EmailAccount(
        company_id=company.id, provider="gmail", email_address="two@example.com",
    )
    db.session.add(second)
    fail(account)
    fail(second)

    body = logged_in.get("/").get_data(as_text=True)
    assert "2 integrations stopped syncing" in body


def test_another_tenants_failure_is_not_shown(logged_in, other_company):
    theirs = EmailAccount(
        company_id=other_company.id, provider="gmail",
        email_address="theirs@example.com", last_sync_error="broken",
    )
    db.session.add(theirs)
    db.session.commit()

    assert "nav-badge--alert" not in logged_in.get("/").get_data(as_text=True)


def test_the_login_page_renders_without_a_user(app):
    """The badge callable runs wherever base.html does, including logged out."""
    assert app.test_client().get("/login").status_code == 200


def test_a_second_user_sees_the_same_alert(app, company, account):
    """Unlike the lead badge, this one isn't personal — a mailbox is broken
    for everyone at the studio, not just whoever last looked."""
    fail(account)
    second = User(company_id=company.id, username="colleague")
    second.set_password("changeme")
    db.session.add(second)
    db.session.commit()

    with app.test_client() as client:
        client.post(
            "/login", data={"username": "colleague", "password": "changeme"},
            follow_redirects=True,
        )
        assert "nav-badge--alert" in client.get("/").get_data(as_text=True)
