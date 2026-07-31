"""
Connected-mailbox lifecycle: list, connect, toggle, disconnect.

The tenant boundary lives here. Every lookup in this module takes a
`company_id` and filters on it — there is no "get account by id" that
doesn't. That's the shape the rest of the app already uses
(get_order_or_404 / get_client_or_404 in app.py), and it's the reason a
route can't accidentally hand one company another's mailbox by passing an
id from the URL.
"""

from models import db

from communications import config
from communications.models import (
    AUDIT_INTEGRATION_CONNECTED, AUDIT_INTEGRATION_DISCONNECTED,
    EmailAccount, EmailSyncSettings, utcnow,
)
from communications.oauth import google_oauth
from communications.services import audit


def accounts_for(company_id: int) -> list[EmailAccount]:
    return (
        EmailAccount.query.filter_by(company_id=company_id)
        .order_by(EmailAccount.is_default.desc(), EmailAccount.email_address)
        .all()
    )


def get_account(company_id: int, account_id: int) -> EmailAccount | None:
    """One account, or None. Scoped — an id from another tenant reads as
    "doesn't exist", which is what it should look like."""
    return EmailAccount.query.filter_by(id=account_id, company_id=company_id).first()


def default_account(company_id: int) -> EmailAccount | None:
    """The account to send from when nothing else specifies one.

    Falls back to any send-enabled account rather than returning None just
    because nobody ticked "default" — one connected mailbox should Just
    Work without a settings trip.
    """
    accounts = [a for a in accounts_for(company_id) if a.send_enabled]
    if not accounts:
        return None
    return next((a for a in accounts if a.is_default), accounts[0])


def sync_enabled_accounts(company_id: int | None = None) -> list[EmailAccount]:
    """Accounts eligible for a background sync run.

    company_id=None means every tenant — that's the scheduled job's view,
    and the one place a query legitimately isn't tenant-scoped. It still
    can't leak across tenants, because what it returns is fed straight back
    into per-account work that carries its own company_id.
    """
    query = EmailAccount.query.filter_by(sync_enabled=True)
    if company_id is not None:
        query = query.filter_by(company_id=company_id)
    return query.all()


def failing_accounts(company_id: int) -> list[EmailAccount]:
    """Connected accounts whose last sync failed.

    Backs the alert badge on Settings → Integrations. It reads
    `last_sync_error` rather than a separate "healthy" flag for the same
    reason `Invoice.display_status` derives paid-ness: one source of truth
    can't disagree with itself. The column is already set by every sync path
    and cleared by a successful one (see email_sync / jobs), so nothing new
    has to remember to keep a health flag up to date.

    Includes paused accounts on purpose: pausing sync doesn't repair whatever
    went wrong, and the error is still the thing waiting to be dealt with.
    """
    return (
        EmailAccount.query.filter(
            EmailAccount.company_id == company_id,
            EmailAccount.last_sync_error.isnot(None),
            EmailAccount.last_sync_error != "",
        )
        .order_by(EmailAccount.email_address)
        .all()
    )


def connect_google_account(company_id: int, tokens: dict, userinfo: dict) -> EmailAccount:
    """Create or update the EmailAccount for a completed Google grant.

    Reconnecting an address that's already connected updates it in place
    instead of creating a duplicate: the unique constraint on
    (company_id, provider, email_address) would reject the insert anyway,
    and "reconnect to fix a revoked grant" is the common case, not an edge
    one.

    Does not commit — the caller does, so the audit row and the account
    land together.
    """
    email_address = (userinfo.get("email") or "").strip().lower()
    if not email_address:
        raise ValueError("Google did not return an email address for this account.")

    account = EmailAccount.query.filter_by(
        company_id=company_id, provider="gmail", email_address=email_address,
    ).first()
    is_new = account is None
    if is_new:
        account = EmailAccount(
            company_id=company_id, provider="gmail", email_address=email_address,
            # First mailbox connected becomes the default, so a single-inbox
            # studio never has to think about it.
            is_default=not accounts_for(company_id),
        )
        db.session.add(account)

    account.display_name = userinfo.get("name") or account.display_name
    account.access_token = tokens.get("access_token")
    account.refresh_token = tokens.get("refresh_token")  # setter ignores a blank
    account.token_expiry = tokens.get("expiry")
    account.granted_scopes = " ".join(tokens.get("scopes") or [])
    account.sync_enabled = True
    # A successful reconnect clears whatever the account was failing with —
    # otherwise the integrations page keeps showing a stale error against a
    # mailbox that now works.
    account.last_sync_error = None
    account.updated_at = utcnow()

    # Make sure the tenant has sync settings before the first run needs them.
    EmailSyncSettings.for_company(company_id)

    audit.record(
        company_id, AUDIT_INTEGRATION_CONNECTED,
        f"{'Connected' if is_new else 'Reconnected'} Gmail account {email_address}. "
        f"Scopes: {account.granted_scopes or '(none reported)'}",
    )
    return account


def disconnect(company_id: int, account_id: int) -> bool:
    """Revoke the grant at Google and delete the local account.

    Synced threads and messages go with it (cascade). That's the right
    default for a privacy feature — "disconnect" that leaves a copy of
    someone's mail behind isn't a disconnect — and it's why the confirm
    step in the UI says so out loud.
    """
    account = get_account(company_id, account_id)
    if account is None:
        return False

    email_address = account.email_address
    google_oauth.revoke(account)  # best effort; see its docstring
    db.session.delete(account)
    audit.record(
        company_id, AUDIT_INTEGRATION_DISCONNECTED,
        f"Disconnected {email_address}. Synced threads and messages were deleted.",
    )
    return True


def set_flags(company_id: int, account_id: int, **flags) -> EmailAccount | None:
    """Toggle sync_enabled / send_enabled / is_default on one account."""
    account = get_account(company_id, account_id)
    if account is None:
        return None

    if "sync_enabled" in flags:
        account.sync_enabled = bool(flags["sync_enabled"])
    if "send_enabled" in flags:
        account.send_enabled = bool(flags["send_enabled"])
    if flags.get("is_default"):
        # Exactly one default per company — clearing the others here rather
        # than relying on a partial unique index, which SQLite supports but
        # the other databases this is meant to move to spell differently.
        for other in accounts_for(company_id):
            other.is_default = other.id == account.id
    account.updated_at = utcnow()
    return account


def scope_summary(account: EmailAccount) -> list[tuple[str, str]]:
    """(scope, plain-English) pairs for what this account actually granted.

    Reads the stored grant, not what we asked for — a user who unticked
    calendar on the consent screen should see that reflected, not a list
    of permissions the app merely hoped for.
    """
    return [
        (scope, config.SCOPE_DESCRIPTIONS[scope])
        for scope in account.scopes
        if scope in config.SCOPE_DESCRIPTIONS
    ]
