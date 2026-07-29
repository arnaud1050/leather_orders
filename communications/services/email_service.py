"""
The email API the rest of the app calls.

Per §5 of the requirements, nothing outside `communications/` talks to a
provider — it calls in here:

    email_service.threads_for_client(company_id, client_id)
    email_service.send_email(company_id, to=..., subject=..., body_text=...)
    email_service.sync_now(company_id)

Every function takes `company_id` as its first argument and filters on it.
That's not ceremony: these are the functions a route hands URL parameters
to, and a tenant check that lives at the bottom of the stack can't be
forgotten at the top.
"""

import logging
import re

from models import Client, db

from communications.models import (
    AUDIT_CLIENT_CREATED_FROM_EMAIL, AUDIT_EMAIL_SENT, AUDIT_SYNC_RUN,
    DIRECTION_OUTGOING, EmailMessage, EmailThread, utcnow,
)
from communications.providers import email_provider_for
from communications.providers.base import ProviderError
from communications.services import account_service, audit
from communications.sync import email_sync

logger = logging.getLogger(__name__)

# Good enough to reject a typo, not an RFC 5322 parser. Over-strict address
# validation rejects real addresses; this only catches the obvious.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailServiceError(Exception):
    """Something the caller should show the user, not a bug."""


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def threads_for_client(company_id: int, client_id: int) -> list[EmailThread]:
    """One client's conversations, most recent first."""
    return (
        EmailThread.query.filter_by(company_id=company_id, client_id=client_id)
        .order_by(EmailThread.last_message_date.desc().nullslast())
        .all()
    )


def lead_threads(company_id: int, limit: int = 100) -> list[EmailThread]:
    """Conversations matched to nobody on file — the lead inbox.

    Only threads with at least one *incoming* message: an outgoing-only
    thread to an unknown address is us mailing a supplier, not a lead.
    """
    threads = (
        EmailThread.query.filter_by(company_id=company_id, client_id=None)
        .order_by(EmailThread.last_message_date.desc().nullslast())
        .limit(limit)
        .all()
    )
    return [t for t in threads if any(m.is_incoming for m in t.messages)]


def get_thread(company_id: int, thread_id: int) -> EmailThread | None:
    """One thread, tenant-scoped. None reads as 404 to the caller."""
    return EmailThread.query.filter_by(id=thread_id, company_id=company_id).first()


def unread_lead_count(company_id: int) -> int:
    return len(lead_threads(company_id))


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

def send_email(
    company_id: int, to, subject: str, body_text: str,
    cc=None, account_id: int | None = None,
    thread_id: int | None = None, client_id: int | None = None,
) -> EmailMessage:
    """Send a message and record it locally.

    Returns the stored EmailMessage. Raises EmailServiceError for anything
    the user can fix (no mailbox connected, bad address, sending paused)
    and lets ProviderError through for anything they can't.

    **Commits itself**, unlike most of this codebase. Once Gmail has
    accepted the message it has really gone out; a caller that rolled back
    afterwards would leave the app with no record of a mail the client
    already received.
    """
    recipients = _clean_addresses(to)
    if not recipients:
        raise EmailServiceError("At least one valid recipient address is required.")
    cc_addresses = _clean_addresses(cc)
    if not (subject or "").strip() and not (body_text or "").strip():
        raise EmailServiceError("A message needs a subject or a body.")

    account = (
        account_service.get_account(company_id, account_id) if account_id
        else account_service.default_account(company_id)
    )
    if account is None:
        # default_account() only considers send-enabled mailboxes, so
        # "nothing to send from" has two very different causes. Telling
        # someone to connect a mailbox they already have connected would
        # send them to re-run OAuth to fix a checkbox.
        if account_service.accounts_for(company_id):
            raise EmailServiceError(
                "Sending is turned off for every connected mailbox. Re-enable "
                "one under Settings → Integrations."
            )
        raise EmailServiceError(
            "No mailbox is connected for sending. Connect one under "
            "Settings → Integrations."
        )
    if not account.send_enabled:
        raise EmailServiceError(f"Sending from {account.email_address} is turned off.")

    thread = get_thread(company_id, thread_id) if thread_id else None
    if thread is not None and thread.email_account_id != account.id:
        # Replying into a thread that belongs to another mailbox would send
        # from the wrong address and break the conversation on Gmail's side.
        account = account_service.get_account(company_id, thread.email_account_id) or account

    provider = email_provider_for(account)
    reply_to_message_id = _reply_reference(provider, thread)

    sent = provider.send_email(
        to=recipients,
        subject=subject,
        body_text=body_text,
        cc=cc_addresses,
        reply_to_message_id=reply_to_message_id,
        thread_id=thread.provider_thread_id if thread else None,
    )

    stored = _store_sent_message(account, thread, sent, client_id)
    audit.record(
        company_id, AUDIT_EMAIL_SENT,
        f"From {account.email_address} to {', '.join(recipients)} — {subject or '(no subject)'}",
    )
    db.session.commit()
    return stored


def _reply_reference(provider, thread: EmailThread | None) -> str | None:
    """The RFC 822 Message-ID to reply to, if this is a reply.

    Best effort: without it Gmail still threads the message on its own
    side (threadId does that), but other mail clients thread on
    In-Reply-To, so it's worth an extra call. A failure here must not stop
    the send.
    """
    if thread is None or not thread.messages:
        return None
    last = thread.messages[-1]
    try:
        return provider.rfc822_message_id(last.provider_message_id)
    except ProviderError as exc:
        logger.warning("Could not read Message-ID for reply threading: %s", exc)
        return None


def _store_sent_message(account, thread, sent, client_id) -> EmailMessage:
    """Record an outgoing message, creating its thread if it's a new one.

    Stored immediately rather than waiting for the next sync so the message
    appears in the client's history the moment it's sent — and keyed on the
    provider's ids, so when the sync does come round it recognises it and
    doesn't duplicate it.
    """
    if thread is None:
        thread = EmailThread.query.filter_by(
            email_account_id=account.id, provider_thread_id=sent.provider_thread_id,
        ).first()
    if thread is None:
        thread = EmailThread(
            company_id=account.company_id,
            email_account_id=account.id,
            provider_thread_id=sent.provider_thread_id,
            subject=sent.subject,
            client_id=client_id,
        )
        db.session.add(thread)
        db.session.flush()
    elif thread.client_id is None and client_id:
        thread.client_id = client_id

    thread.last_message_date = sent.received_date or utcnow()
    thread.updated_at = utcnow()

    existing = EmailMessage.query.filter_by(
        thread_id=thread.id, provider_message_id=sent.provider_message_id,
    ).first()
    if existing is not None:
        return existing

    message = EmailMessage(
        thread_id=thread.id,
        provider_message_id=sent.provider_message_id,
        sender=sent.sender or account.email_address,
        sender_name=sent.sender_name or account.display_name,
        recipients=", ".join(sent.recipients) or None,
        cc=", ".join(sent.cc) or None,
        subject=sent.subject,
        body_text=sent.body_text,
        body_html=sent.body_html,
        received_date=sent.received_date or utcnow(),
        direction=DIRECTION_OUTGOING,
        has_attachments=bool(sent.attachments),
    )
    db.session.add(message)
    db.session.flush()
    return message


def _clean_addresses(value) -> list[str]:
    """Split and validate a comma-separated address field."""
    if not value:
        return []
    parts = value if isinstance(value, (list, tuple)) else str(value).split(",")
    cleaned = []
    for part in parts:
        address = part.strip().lower()
        if address and _EMAIL_RE.match(address):
            cleaned.append(address)
    return cleaned


# ---------------------------------------------------------------------------
# Syncing
# ---------------------------------------------------------------------------

def sync_now(company_id: int) -> list:
    """Sync every enabled mailbox for one company, and report per account.

    This is what the "Sync now" button calls. Same function the scheduled
    job uses per tenant, so the manual and automatic paths can't drift.
    """
    accounts = account_service.sync_enabled_accounts(company_id)
    results = [email_sync.sync_account(account) for account in accounts]
    if results:
        audit.record(
            company_id, AUDIT_SYNC_RUN,
            "; ".join(result.summary() for result in results),
        )
        db.session.commit()
    return results


# ---------------------------------------------------------------------------
# Leads
# ---------------------------------------------------------------------------

def create_client_from_thread(company_id: int, thread_id: int, **overrides) -> Client:
    """Turn an unmatched conversation into a client, and attach it.

    Deliberately explicit rather than automatic on sync: an inbox contains
    newsletters, suppliers and spam, and silently minting a Client for each
    would make the client list useless. §3 lists automatic creation as a
    future capability — this is the manual half of it, and the same
    function an automatic rule would call later.

    Also re-runs matching across the company's other orphan threads, since
    the new client's address may appear in more than one of them.
    """
    thread = get_thread(company_id, thread_id)
    if thread is None:
        raise EmailServiceError("That conversation no longer exists.")
    if thread.client_id is not None:
        raise EmailServiceError("That conversation is already linked to a client.")

    email_address = (overrides.get("email") or thread.counterparty or "").strip().lower()
    if not email_address:
        raise EmailServiceError("No sender address found on this conversation.")

    existing = Client.query.filter_by(company_id=company_id).filter(
        db.func.lower(Client.email) == email_address
    ).first()
    if existing is not None:
        client = existing
    else:
        first_name, last_name = _split_name(
            overrides.get("first_name"), overrides.get("last_name"), thread,
        )
        client = Client(
            company_id=company_id,
            first_name=first_name,
            last_name=last_name,
            email=email_address,
            phone="",
            # The message that started the conversation, kept as the lead's
            # first contact — the same field the contact-form webhook fills
            # (see Client.first_message in models.py), so both routes into
            # the app produce a client that reads the same.
            first_message=_first_incoming_body(thread),
        )
        db.session.add(client)
        db.session.flush()

    thread.client_id = client.id
    also_matched = email_sync.rematch_unassigned(company_id)
    audit.record(
        company_id, AUDIT_CLIENT_CREATED_FROM_EMAIL,
        f"{client.name} <{email_address}> from thread {thread.display_subject!r}"
        + (f"; {also_matched} other conversation(s) matched" if also_matched else ""),
    )
    db.session.commit()
    return client


def _split_name(first_name, last_name, thread: EmailThread) -> tuple[str, str]:
    """A name for the new client.

    Falls back to the sender's display name, then to the local part of the
    address. Both halves are required by the Client model, so something has
    to be there — a placeholder someone can correct beats refusing to
    create the client.
    """
    first_name = (first_name or "").strip()
    last_name = (last_name or "").strip()
    if first_name and last_name:
        return first_name, last_name

    display = ""
    for message in thread.messages:
        if message.is_incoming and message.sender_name:
            display = message.sender_name.strip()
            break
    if not display:
        display = (thread.counterparty or "").split("@")[0].replace(".", " ").title()

    parts = display.split()
    if len(parts) >= 2:
        return first_name or parts[0], last_name or " ".join(parts[1:])
    return first_name or (display or "Unknown"), last_name or "(from email)"


def _first_incoming_body(thread: EmailThread) -> str | None:
    for message in thread.messages:
        if message.is_incoming and message.body_text:
            return message.body_text[:5000]
    return None
