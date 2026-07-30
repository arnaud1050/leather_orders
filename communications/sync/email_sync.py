"""
Mailbox synchronisation and client association.

The flow, per §11 of the requirements:

    fetch new messages -> store messages -> update threads -> associate clients

Two rules shape everything here:

**Idempotence.** Every write is an upsert keyed on the provider's own id
((account, thread_id) and (thread, message_id), both unique constraints).
A sync that runs twice, overlaps a previous window, or crashes halfway and
retries produces the same rows — which is what makes polling safe to do
often and safe to do again after a failure.

**Tenant isolation.** Client matching only ever looks at clients belonging
to the account's own company. Two studios that both email the same person
get their own threads, matched to their own client records, and neither
can see the other's.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from models import Client, db

from communications import config
from communications.models import (
    AUDIT_SYNC_FAILED, DIRECTION_INCOMING, EmailAccount, EmailAttachment,
    EmailMessage, EmailSyncSettings, EmailThread, utcnow,
)
from communications.providers import email_provider_for
from communications.providers.base import ProviderError
from communications.services import audit
from communications.storage import attachment_storage

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """What one account's run did, for the UI and the logs."""

    account_id: int
    email_address: str = ""
    threads_seen: int = 0
    threads_created: int = 0
    messages_created: int = 0
    threads_matched: int = 0
    threads_skipped: int = 0
    threads_resurfaced: int = 0
    attachments_saved: int = 0
    error: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None

    def summary(self) -> str:
        if self.error:
            return f"{self.email_address}: {self.error}"
        return (
            f"{self.email_address}: {self.messages_created} new message(s) "
            f"in {self.threads_created} new thread(s), "
            f"{self.threads_matched} matched to a client"
            + (f", {self.threads_skipped} unmatched discarded" if self.threads_skipped else "")
            + (f", {self.threads_resurfaced} dismissed reopened"
               if self.threads_resurfaced else "")
        )


def sync_account(account, since: datetime | None = None) -> SyncResult:
    """Pull one mailbox into the database.

    Commits on success, rolls back on failure, and records the failure on
    the account either way — a mailbox that quietly stopped syncing three
    weeks ago is the worst outcome available, so the error is stored where
    the UI shows it rather than only logged.

    `since` overrides the automatic window. Left None it's the account's
    last successful sync, or `initial_sync_days` back for a first run.
    """
    result = SyncResult(account_id=account.id, email_address=account.email_address)
    settings = EmailSyncSettings.for_company(account.company_id)
    window_start = since or _window_start(account, settings)

    try:
        provider = email_provider_for(account)
        threads = provider.fetch_threads(
            since=window_start,
            limit=config.MAX_MESSAGES_PER_SYNC,
            include_sent=settings.sync_sent_mail,
        )
    except ProviderError as exc:
        return _record_failure(account, result, str(exc))
    except Exception as exc:  # noqa: BLE001 — a provider bug must not kill the job
        logger.exception("Unexpected error fetching mail for account %s", account.id)
        return _record_failure(account, result, f"Unexpected error: {exc}")

    # One lookup table per run rather than a query per thread: a mailbox
    # sync touches every client, and the client list is small.
    clients_by_email = _client_index(account.company_id)

    try:
        for fetched in threads:
            result.threads_seen += 1
            _store_thread(account, settings, fetched, clients_by_email, result, provider)
        account.last_sync_at = utcnow()
        account.last_sync_error = None
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Error storing mail for account %s", account.id)
        db.session.rollback()
        return _record_failure(account, result, f"Could not store messages: {exc}")

    return result


def _record_failure(account, result: SyncResult, message: str) -> SyncResult:
    result.error = message
    account.last_sync_error = message[:1000]
    account.last_sync_at = utcnow()
    audit.record(account.company_id, AUDIT_SYNC_FAILED, f"{account.email_address}: {message}")
    db.session.commit()
    return result


def _window_start(account, settings: EmailSyncSettings) -> datetime:
    """How far back to read.

    An incremental run overlaps the previous one by an hour. Gmail's
    `after:` has day granularity and messages can be indexed slightly out
    of order; overlap costs a few already-stored messages that upsert to
    no-ops, while a gap loses mail silently.
    """
    if account.last_sync_at:
        return account.last_sync_at - timedelta(hours=1)
    return utcnow() - timedelta(days=settings.initial_sync_days)


def _client_index(company_id: int) -> dict[str, Client]:
    """email -> Client, for one company only.

    The tenant filter here is the whole client-association security story:
    a thread can only ever be matched to a client of the company that owns
    the mailbox.
    """
    clients = Client.query.filter_by(company_id=company_id).all()
    return {
        client.email.strip().lower(): client
        for client in clients
        if client.email and client.email.strip()
    }


def match_client(participants, clients_by_email: dict[str, Client], own_addresses) -> Client | None:
    """Which client, if any, a set of addresses belongs to.

    Phase 1 matching is exact-address (§13). The mailbox's own addresses
    are excluded first — otherwise a studio that has itself on file as a
    client would match every thread to itself.
    """
    for address in participants:
        normalised = (address or "").strip().lower()
        if not normalised or normalised in own_addresses:
            continue
        client = clients_by_email.get(normalised)
        if client is not None:
            return client
    return None


def _store_thread(account, settings, fetched, clients_by_email, result, provider) -> None:
    """Upsert one thread and its messages."""
    own_addresses = {(account.email_address or "").lower()}
    participants = [
        address for message in fetched.messages for address in message.participants
    ]
    client = match_client(participants, clients_by_email, own_addresses)

    thread = EmailThread.query.filter_by(
        email_account_id=account.id, provider_thread_id=fetched.provider_thread_id,
    ).first()

    if thread is None:
        # Nothing on file for anyone in this conversation, and the company
        # opted out of keeping unknown senders — don't store it at all.
        # Note this is checked only for *new* threads: a thread already
        # stored isn't retroactively deleted by flipping the setting, which
        # would silently destroy history someone may be reading.
        if client is None and not settings.keep_unmatched:
            result.threads_skipped += 1
            return
        thread = EmailThread(
            company_id=account.company_id,
            email_account_id=account.id,
            provider_thread_id=fetched.provider_thread_id,
        )
        db.session.add(thread)
        result.threads_created += 1

    thread.subject = fetched.subject or thread.subject
    thread.last_message_date = fetched.last_message_date or thread.last_message_date
    # Only ever *adds* a client, never clears one. A thread matched by hand
    # (see "create a client from this email") has a client the address
    # index may not know about, and a later sync must not undo that.
    if client is not None and thread.client_id != client.id:
        thread.client_id = client.id
        result.threads_matched += 1
    thread.updated_at = utcnow()
    db.session.flush()  # assigns thread.id for the messages below

    for message in fetched.messages:
        _store_message(account, settings, thread, message, result, provider)


def _store_message(account, settings, thread, fetched, result, provider) -> None:
    existing = EmailMessage.query.filter_by(
        thread_id=thread.id, provider_message_id=fetched.provider_message_id,
    ).first()
    if existing is not None:
        return  # already stored; a message's content doesn't change after receipt

    message = EmailMessage(
        thread_id=thread.id,
        provider_message_id=fetched.provider_message_id,
        sender=fetched.sender,
        sender_name=fetched.sender_name,
        recipients=", ".join(fetched.recipients) or None,
        cc=", ".join(fetched.cc) or None,
        bcc=", ".join(fetched.bcc) or None,
        subject=fetched.subject,
        body_text=fetched.body_text,
        body_html=fetched.body_html,
        received_date=fetched.received_date,
        direction=fetched.direction,
        has_attachments=bool(fetched.attachments),
    )
    db.session.add(message)
    db.session.flush()
    result.messages_created += 1

    # A dismissed sender writing again is new signal, so the thread comes
    # back to the inbox — a bespoke enquiry hidden by mistake gets a second
    # chance instead of going silent forever. Only for genuinely new incoming
    # mail: this function returns early on a message it has already stored,
    # so re-syncing an old window can't resurrect the same thread repeatedly.
    #
    # Not for trashed threads. Their mail is in Gmail's Trash and the sync
    # query excludes it (-in:trash), so a new message here would mean someone
    # recovered it in Gmail — but if that ever changes, un-hiding something
    # the user explicitly threw away would be the wrong way to be wrong.
    if (
        fetched.direction == DIRECTION_INCOMING
        and thread.is_dismissed
        and not thread.was_trashed
    ):
        thread.restore()
        result.threads_resurfaced += 1

    for attachment in fetched.attachments:
        _store_attachment(account, settings, message, attachment, result, provider)


def _store_attachment(account, settings, message, fetched, result, provider) -> None:
    """Metadata always, bytes only when the company asked for them.

    Attachments dwarf message text and most of them are signature images,
    so downloading is opt-in per tenant (EmailSyncSettings.sync_attachments).
    The row exists either way, so the UI can show "3 attachments" without
    having stored a byte.
    """
    row = EmailAttachment(
        message_id=message.id,
        provider_attachment_id=fetched.provider_attachment_id,
        filename=fetched.filename,
        mime_type=fetched.mime_type,
        size_bytes=fetched.size_bytes,
    )
    db.session.add(row)

    if not settings.sync_attachments or not fetched.provider_attachment_id:
        return
    try:
        data = provider.fetch_attachment(
            message.provider_message_id, fetched.provider_attachment_id,
        )
        row.stored_filename = attachment_storage.save(
            account.company_id, fetched.filename, data,
        )
        result.attachments_saved += 1
    except Exception as exc:  # noqa: BLE001
        # One unreadable attachment must not lose the message it came with.
        logger.warning("Could not download attachment %s: %s", fetched.filename, exc)
        result.errors.append(f"Attachment {fetched.filename}: {exc}")


def rematch_unassigned(company_id: int) -> int:
    """Re-run client matching over threads that have no client yet.

    Called after a client is created or has their email changed: threads
    that arrived before the client existed should attach to them without
    waiting for the person to send another message. Returns how many
    threads were newly matched. Does not commit.
    """
    clients_by_email = _client_index(company_id)
    if not clients_by_email:
        return 0

    own_addresses = {
        account.email_address.lower()
        for account in EmailAccount.query.filter_by(company_id=company_id).all()
        if account.email_address
    }

    matched = 0
    orphans = EmailThread.query.filter_by(company_id=company_id, client_id=None).all()
    for thread in orphans:
        participants = [
            address
            for message in thread.messages
            for address in _message_participants(message)
        ]
        client = match_client(participants, clients_by_email, own_addresses)
        if client is not None:
            thread.client_id = client.id
            matched += 1
    return matched


def _message_participants(message: EmailMessage) -> list[str]:
    """Every address on a stored message — the DB-side twin of
    FetchedMessage.participants."""
    addresses = [message.sender or ""]
    for field_value in (message.recipients, message.cc, message.bcc):
        addresses.extend((field_value or "").split(","))
    return [address.strip().lower() for address in addresses if address and address.strip()]
