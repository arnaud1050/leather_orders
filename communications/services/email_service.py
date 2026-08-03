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
    AUDIT_CLIENT_AUTO_CREATED, AUDIT_CLIENT_CREATED_FROM_EMAIL,
    AUDIT_CLIENT_MAIL_LINKED,
    AUDIT_EMAIL_SENT, AUDIT_SYNC_RUN, AUDIT_THREAD_TRASHED, DIRECTION_INCOMING,
    DIRECTION_OUTGOING, DISMISSED_HIDDEN, DISMISSED_TRASHED, AutoCreatedClient,
    EmailMessage, EmailThread, utcnow,
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

    Shares `_lead_thread_query` with the badge count deliberately. When this
    filtered in Python and the badge filtered in SQL, the two could report
    different numbers for the same inbox.
    """
    return (
        _lead_thread_query(company_id)
        .order_by(EmailThread.last_message_date.desc().nullslast())
        .limit(limit)
        .all()
    )


def dismissed_lead_threads(company_id: int, limit: int = 100) -> list[EmailThread]:
    """Leads that were hidden or trashed — the "show dismissed" view.

    Ordered by when they were dismissed rather than by message date, since
    what someone looking at this list wants is "what did I just get rid of".
    """
    return (
        _lead_thread_query(company_id, dismissed=True)
        .order_by(EmailThread.dismissed_at.desc())
        .limit(limit)
        .all()
    )


def get_thread(company_id: int, thread_id: int) -> EmailThread | None:
    """One thread, tenant-scoped. None reads as 404 to the caller."""
    return EmailThread.query.filter_by(id=thread_id, company_id=company_id).first()


# ---------------------------------------------------------------------------
# The lead badge, and the "New" pill.
#
# The badge counts leads **awaiting triage** — not leads that arrived since
# someone last looked. Looking is not doing: a badge that cleared the moment
# you opened the inbox stopped reminding you about the enquiry you hadn't
# answered yet, which is the only thing it was there for. It falls to zero
# exactly when the work is done: a lead converted to a client (it has a
# client, so it isn't a lead), hidden, or trashed.
#
# Derived, never stored — the same query the list itself runs, so the number
# beside the link and the rows behind it can't disagree. Same reasoning as
# Order.total and Invoice.display_status elsewhere in the app.
#
# The "New" pill is a separate question — has *this conversation* been read —
# and so it reads a per-thread `opened_at` rather than the badge's rule. A
# lead can be read and still be waiting (pill gone, badge still counting),
# which is exactly the state the old single-timestamp design couldn't express.
# ---------------------------------------------------------------------------

def _lead_thread_query(company_id: int, dismissed: bool = False):
    """Threads that count as leads: no client, something incoming, not triaged away.

    `messages.any(...)` compiles to an EXISTS, so the badge is one COUNT
    query rather than loading every thread and its messages — this runs on
    every page render (see the context processor in routes.py).

    `dismissed=True` returns the triaged-away ones instead, for the "show
    dismissed" view. One query either way so the two lists can't disagree
    about what a lead is.
    """
    query = EmailThread.query.filter(
        EmailThread.company_id == company_id,
        EmailThread.client_id.is_(None),
        EmailThread.messages.any(EmailMessage.direction == DIRECTION_INCOMING),
    )
    if dismissed:
        return query.filter(EmailThread.dismissed_at.isnot(None))
    return query.filter(EmailThread.dismissed_at.is_(None))


def pending_lead_count(company_id: int) -> int:
    """How many leads are still waiting to be dealt with.

    Literally the length of the lead inbox: same query, so the badge and
    the list it points at can never disagree about what's outstanding.
    """
    return _lead_thread_query(company_id).count()


def mark_thread_opened(company_id: int, thread_id: int) -> None:
    """Record that a conversation has been read. Commits.

    Stamps two things, and they are not the same thing:

    - `EmailThread.opened_at`, **only on the first open** — the "New" pill
      answers "has anyone looked at this yet", so re-reading a thread
      shouldn't rewrite when it stopped being new.
    - `read_at` on every unread **incoming** message, **every time** — which
      is what makes a later reply count as unread mail until someone opens
      the thread again. That's the whole point for a client conversation,
      which unlike a lead stays alive for years.

    Called from a GET route. Writing on a GET is normally worth avoiding,
    but this is idempotent, destroys nothing, and opening the conversation
    is the only place "read" could honestly be recorded.
    """
    thread = get_thread(company_id, thread_id)
    if thread is None:
        return

    now = utcnow()
    changed = False
    if thread.opened_at is None:
        thread.opened_at = now
        changed = True
    for message in thread.messages:
        if message.is_unread:
            message.read_at = now
            changed = True

    if changed:
        db.session.commit()


# ---------------------------------------------------------------------------
# Unread mail from clients.
#
# A separate question from the lead badge, and the gap this fills: a lead
# arriving is loud (it's in the lead inbox, it's counted, it has a pill),
# but a client — someone already on file, i.e. someone the studio has real
# work with — could write and nothing anywhere said so. Their thread just
# quietly updated on a page nobody had reason to open.
#
# Counted per *message*, not per thread, because the interesting event is
# "they wrote again", and a client thread is long-lived: replies land in a
# conversation that has been opened many times before.
# ---------------------------------------------------------------------------

def _unread_client_query(company_id: int):
    """Unread incoming messages on threads belonging to a client.

    Dismissed threads are excluded: hiding a conversation — by hand or by a
    sender rule — means "don't tell me about this", and a rule on a domain
    that happens to also be a client shouldn't leak back in as a count.
    """
    return (
        db.session.query(EmailThread.client_id, db.func.count(EmailMessage.id))
        .join(EmailMessage, EmailMessage.thread_id == EmailThread.id)
        .filter(
            EmailThread.company_id == company_id,
            EmailThread.client_id.isnot(None),
            EmailThread.dismissed_at.is_(None),
            EmailMessage.direction == DIRECTION_INCOMING,
            EmailMessage.read_at.is_(None),
        )
        .group_by(EmailThread.client_id)
    )


def unread_counts_by_client(company_id: int) -> dict[int, int]:
    """{client_id: unread messages}, for the client roster.

    One grouped query rather than a count per row: the clients page renders
    every client, and a query each would be the classic N+1 that only shows
    up once someone has a few hundred of them.
    """
    return dict(_unread_client_query(company_id).all())


def unread_client_mail_count(company_id: int, client_id: int | None = None) -> int:
    """Unread mail from clients — all of them, or one.

    Built from the same query as the per-client counts, so the nav badge,
    the roster and a single client's Emails tab are three views of one
    number rather than three tallies that can disagree — the same reasoning
    as the lead badge sharing `_lead_thread_query` with the lead list.

    `client_id` is filtered inside the tenant-scoped query, so an id from
    another company yields zero rather than someone else's mail.
    """
    query = _unread_client_query(company_id)
    if client_id is not None:
        query = query.filter(EmailThread.client_id == client_id)
    return sum(count for _, count in query.all())


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

def dismiss_thread(company_id: int, thread_id: int) -> EmailThread:
    """Hide a lead from the inbox without touching the mailbox. Commits.

    The row is kept, not deleted — deleting it would only mean the next sync
    re-downloads the thread and it reappears. Fully reversible from the
    "dismissed" view, which is what makes this the right default action for
    triage: being wrong costs nothing.
    """
    thread = get_thread(company_id, thread_id)
    if thread is None:
        raise EmailServiceError("That conversation no longer exists.")
    thread.dismiss(DISMISSED_HIDDEN)
    db.session.commit()
    return thread


def restore_thread(company_id: int, thread_id: int) -> EmailThread:
    """Put a hidden lead back in the inbox. Commits.

    Deliberately refuses a trashed thread: the mail is in Gmail's Trash, and
    un-trashing is a Gmail action. Restoring it here would show it in Leads
    again while the message stayed trashed — a button that appears to work
    and doesn't.
    """
    thread = get_thread(company_id, thread_id)
    if thread is None:
        raise EmailServiceError("That conversation no longer exists.")
    if thread.was_trashed:
        raise EmailServiceError(
            "That conversation was moved to Gmail's Trash. Recover it in Gmail "
            "and it will come back on the next sync."
        )
    thread.restore()
    db.session.commit()
    return thread


def trash_thread(company_id: int, thread_id: int) -> EmailThread:
    """Move a conversation to the provider's Trash, and dismiss it here.

    Recoverable, never permanent — see EmailProvider.trash_thread. The
    provider call happens first: if Gmail refuses, nothing local changes, so
    the app never claims to have trashed mail that's still sitting in the
    inbox. Commits, for the same reason send_email does.
    """
    thread = get_thread(company_id, thread_id)
    if thread is None:
        raise EmailServiceError("That conversation no longer exists.")

    account = account_service.get_account(company_id, thread.email_account_id)
    if account is None:
        raise EmailServiceError("The mailbox this conversation came from is no longer connected.")

    email_provider_for(account).trash_thread(thread.provider_thread_id)

    thread.dismiss(DISMISSED_TRASHED)
    audit.record(
        company_id, AUDIT_THREAD_TRASHED,
        f"Moved {thread.display_subject!r} from {account.email_address} to Trash. "
        "Recoverable in Gmail for 30 days.",
    )
    db.session.commit()
    return thread


def client_with_email(company_id: int, email_address: str) -> Client | None:
    """Whoever is already on file at this address, if anyone.

    Case-insensitively, and inside the company: two studios can each have a
    client at the same address and neither may see the other's.
    """
    address = (email_address or "").strip().lower()
    if not address:
        return None
    return (
        Client.query.filter_by(company_id=company_id)
        .filter(db.func.lower(Client.email) == address)
        .first()
    )


def create_client_from_thread(
    company_id: int, thread_id: int, commit: bool = True,
    attributed_to: str = "", **overrides
) -> Client:
    """Turn an unmatched conversation into a client, and attach it.

    Still not something sync does to any old sender: an inbox is full of
    newsletters, suppliers and spam, and silently minting a Client per
    address would make the roster useless. What changed is that a company
    can now name the senders where the answer is never in doubt — see
    `SenderRule` — and `auto_create_client()` below is what calls this for
    them. Everything else stays a deliberate click.

    **An address already on file is reused, never duplicated** — the
    conversation is simply attached to that client, which is what a person
    would do with a repeat enquiry. `_apply_details` then fills whatever
    the form answered and the record hasn't got, and overwrites nothing.
    The audit line says *linked* rather than *created* in that case: the
    two are different things to have to answer for later.

    Also re-runs matching across the company's other orphan threads, since
    the new client's address may appear in more than one of them.

    `commit=False` is for the sync, which owns a transaction covering every
    thread in the run and must be able to roll the whole thing back.
    `attributed_to` names what did this ("rule @squarespace.info"), for the
    log — the automatic path is otherwise indistinguishable from a click.
    """
    thread = get_thread(company_id, thread_id)
    if thread is None:
        raise EmailServiceError("That conversation no longer exists.")
    if thread.client_id is not None:
        raise EmailServiceError("That conversation is already linked to a client.")

    email_address = (overrides.get("email") or thread.counterparty or "").strip().lower()
    if not email_address:
        raise EmailServiceError("No sender address found on this conversation.")

    existing = client_with_email(company_id, email_address)
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
            #
            # An override wins: a sender rule that has mapped the form's
            # "Message:" field knows what the person actually wrote, where
            # the raw body is the whole form with its own footer attached.
            first_message=(
                (overrides.get("first_message") or "").strip()
                or _first_incoming_body(thread)
            ),
        )
        db.session.add(client)
        db.session.flush()

    _apply_details(client, overrides)
    thread.client_id = client.id
    also_matched = email_sync.rematch_unassigned(company_id)
    audit.record(
        company_id,
        AUDIT_CLIENT_MAIL_LINKED if existing is not None else AUDIT_CLIENT_CREATED_FROM_EMAIL,
        f"{client.name} <{email_address}> from thread {thread.display_subject!r}"
        + (f" by {attributed_to}" if attributed_to else "")
        + (f"; {also_matched} other conversation(s) matched" if also_matched else ""),
    )
    if commit:
        db.session.commit()
    return client


def _apply_details(client: Client, overrides: dict) -> None:
    """Fill in phone / what-it's-about / message / source from a form.

    **Only fills blanks.** These arrive from a sender rule's field mapping,
    which runs unattended — so it may complete a record nobody has touched,
    and must never overwrite something a person typed. A second enquiry from
    an existing client is the common case, and their phone number on file
    beats whatever they retyped into a web form.

    `source` is matched against the company's existing `SourceOption`s and,
    failing that, falls back to whichever option the company has marked
    `is_other` (see /settings/clients) — the raw text becomes
    `Client.other_source_detail`, the same "Please specify" box a person
    filling in the client page by hand would see. Deliberately not creating
    a brand new option: an arbitrary string from a public form should not be
    able to invent options that then show up in everyone's client page and
    the analytics breakdown.
    """
    from models import SourceOption  # host model; see the note in CLAUDE.md

    for field in ("phone", "inquiry_type", "first_message"):
        value = (overrides.get(field) or "").strip()
        if value and not (getattr(client, field) or "").strip():
            setattr(client, field, value)

    label = (overrides.get("source") or "").strip()
    if not label:
        return
    option = SourceOption.query.filter(
        SourceOption.company_id == client.company_id,
        db.func.lower(SourceOption.label) == label.lower(),
    ).first()
    if option is None:
        option = SourceOption.query.filter_by(
            company_id=client.company_id, is_other=True
        ).first()
        if option is not None and not (client.other_source_detail or "").strip():
            client.other_source_detail = label[:200]
    if option is not None and option not in client.sources:
        client.sources.append(option)


def auto_create_client(company_id: int, thread, rule) -> tuple[Client | None, bool]:
    """Convert a thread because a sender rule said to. Does not commit.

    Returns `(client, was_created)`. The second half is the part worth
    having: **a repeat enquiry from somebody already on file is the normal
    case**, not an error and not a new client. The form is sent from the
    same relay address every time, so the thread arrives unmatched and the
    rule fires exactly as it did the first time — but the address the
    mapping pulls out of the body already belongs to a client. What should
    happen then is what a person would do: attach the conversation to that
    client and leave everything else alone.

    So the reused case links the thread, fills any blanks the form
    answered (`_apply_details`, which never overwrites), and stops there —
    **no `AutoCreatedClient` row**. That badge means "the app added
    somebody to your roster while you weren't looking"; nobody was added,
    so raising it would send someone to a client page to look for a client
    that has been there for a year. The unread-mail badge is what covers
    this case, and it already does: a thread on a client with unread mail
    is precisely what it counts.

    `(None, False)` means there was nothing to convert — a thread with no
    readable sender, or one already matched. A rule firing on mail the app
    can't turn into a client is not an error: it's one thread left in the
    lead inbox for a person to look at, which is where it would have been
    anyway.

    If the rule carries a field mapping, the client is built from what the
    **form said** rather than from who sent it — which is the difference
    between "Haejung Kim <dayanee1004@gmail.com>" and a client named after
    Squarespace. An unmapped rule behaves exactly as it did before mapping
    existed.
    """
    from communications.services import sender_rules

    if thread.client_id is not None:
        return None, False
    overrides = sender_rules.client_fields_from(rule, _first_incoming_body(thread))
    # Asked *before* converting: afterwards the thread is linked either way
    # and the two cases are indistinguishable.
    existing = client_with_email(
        company_id, overrides.get("email") or thread.counterparty or "",
    )
    try:
        client = create_client_from_thread(
            company_id, thread.id, commit=False,
            attributed_to=f"rule {rule.pattern}", **overrides,
        )
    except EmailServiceError as exc:
        # E.g. no sender address on the conversation. Worth a line in the
        # log, not worth failing a sync that has already stored the mail.
        logger.info("Sender rule %s could not convert a thread: %s", rule.pattern, exc)
        return None, False

    if existing is not None:
        # Linked, not created — already audited as such inside the call
        # above, and deliberately with no AutoCreatedClient row.
        return client, False

    db.session.add(AutoCreatedClient(
        company_id=company_id, client_id=client.id, thread_id=thread.id,
    ))
    audit.record(
        company_id, AUDIT_CLIENT_AUTO_CREATED,
        f"{client.name} <{client.email}> created by rule {rule.pattern} "
        f"from {thread.display_subject!r}",
    )
    return client, True


def _split_name(first_name, last_name, thread: EmailThread) -> tuple[str, str]:
    """A name for the new client: whatever was submitted, else the suggestion.

    The fallback is `EmailThread.suggested_name`, which is also what the form
    prefills its name boxes with — deliberately one property rather than two
    copies of the rule, so what you see in the box is what you get if you
    leave it alone. This still has to fall back at all, because the automatic
    sender-rule path submits no form.
    """
    first_name = (first_name or "").strip()
    last_name = (last_name or "").strip()
    if first_name and last_name:
        return first_name, last_name

    suggested_first, suggested_last = thread.suggested_name
    return first_name or suggested_first, last_name or suggested_last


def _first_incoming_body(thread: EmailThread) -> str | None:
    for message in thread.messages:
        if message.is_incoming and message.body_text:
            return message.body_text[:5000]
    return None
