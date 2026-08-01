"""
Communications data model.

Shares the app's `db` (see the root models.py) rather than owning its own
SQLAlchemy instance — one session, one transaction, so linking a thread to
a Client is an ordinary foreign key and not a cross-database join.

Tenant hierarchy, matching the module requirements:

    Company -> EmailAccount -> EmailThread -> EmailMessage -> EmailAttachment

`company_id` is denormalised onto EmailThread and CalendarEvent even though
it's reachable through the account. That's deliberate: every list query in
the app filters by company first, and a tenant filter that depends on
remembering to join is a tenant filter that eventually gets skipped.

**Naming**: the requirements spell these `gmail_thread_id` /
`gmail_message_id`. They're stored as `provider_thread_id` /
`provider_message_id` here, because §17 also requires that business logic
not depend on Gmail — a column named for one vendor guarantees the next
provider either gets a misleading column or a migration.

Timestamps are naive UTC throughout. Gmail hands back epoch milliseconds
and RFC 3339 strings with offsets; those are normalised to UTC at the
provider boundary (see providers/gmail_provider.py) so nothing downstream
has to reason about which zone a row is in. Display converts back.
"""

import re
from datetime import datetime, timezone
from email.utils import parseaddr

from models import db

from communications import crypto


def utcnow() -> datetime:
    """Naive UTC 'now' — see the module docstring on why naive."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Directions a message can travel. Stored as a string for the same reason
# Order.status is: it's used directly as a CSS class suffix.
DIRECTION_INCOMING = "incoming"
DIRECTION_OUTGOING = "outgoing"

# Why a thread left the lead inbox. See EmailThread.dismissed_reason.
DISMISSED_HIDDEN = "hidden"
DISMISSED_TRASHED = "trashed"
# Hidden by a sender rule rather than by a person. Kept distinct from
# DISMISSED_HIDDEN for one reason that matters: a hidden thread comes back
# when the sender writes again (following up is new signal), and a
# newsletter that arrives every Tuesday would resurface every Tuesday —
# a rule that appears not to work. See SenderRule.
DISMISSED_AUTO = "auto_hidden"

# What a SenderRule does with mail from an address.
RULE_CONVERT = "convert"
RULE_HIDE = "hide"
RULE_LABELS = {
    RULE_CONVERT: "Create a client automatically",
    RULE_HIDE: "Hide from the lead inbox",
}

# Where a labelled line in a form email ends up on the Client.
# `FIELD_IGNORE` stores nothing — it exists so a label the studio doesn't
# care about ("File Upload:") still *terminates* the field above it, which
# is otherwise the difference between a tidy enquiry and one whose message
# has the rest of the form stapled to the end of it.
FIELD_NAME = "name"
FIELD_FIRST_NAME = "first_name"
FIELD_LAST_NAME = "last_name"
FIELD_EMAIL = "email"
FIELD_PHONE = "phone"
FIELD_INQUIRY = "inquiry_type"
FIELD_MESSAGE = "first_message"
FIELD_SOURCE = "source"
FIELD_IGNORE = "ignore"
FIELD_TARGET_LABELS = {
    FIELD_NAME: "Full name (split into first and last)",
    FIELD_FIRST_NAME: "First name",
    FIELD_LAST_NAME: "Last name",
    FIELD_EMAIL: "Email address",
    FIELD_PHONE: "Phone",
    FIELD_INQUIRY: "What it's about",
    FIELD_MESSAGE: "Their message",
    FIELD_SOURCE: "How they heard about us",
    FIELD_IGNORE: "Ignore (but end the field above)",
}


class EmailAccount(db.Model):
    """A mailbox a company has connected, and the tokens to reach it.

    One company can connect several (sales@, hello@); `is_default` picks
    which one the compose form uses when nothing else says otherwise.

    `sync_enabled` / `send_enabled` are per-account switches rather than
    one "connected" flag so a mailbox can be turned into a read-only
    archive without disconnecting it — revoking and re-granting OAuth is a
    much bigger hammer than "stop sending from this address".
    """

    __tablename__ = "email_accounts"
    __table_args__ = (
        db.UniqueConstraint(
            "company_id", "provider", "email_address",
            name="uq_email_account_company_provider_address",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    # Provider key, matching a registered provider (see providers/registry.py).
    # "gmail" today; "microsoft"/"imap" slot in here without a schema change.
    provider = db.Column(db.String(30), nullable=False, default="gmail")
    email_address = db.Column(db.String(255), nullable=False)
    display_name = db.Column(db.String(120))
    is_default = db.Column(db.Boolean, nullable=False, default=False)
    sync_enabled = db.Column(db.Boolean, nullable=False, default=True)
    send_enabled = db.Column(db.Boolean, nullable=False, default=True)

    # Fernet ciphertext, never plaintext — see crypto.py. Read and written
    # through the access_token / refresh_token properties below; assigning
    # these columns directly is a bug.
    access_token_encrypted = db.Column(db.Text)
    refresh_token_encrypted = db.Column(db.Text)
    token_expiry = db.Column(db.DateTime)
    # Space-separated list of what Google actually granted. Users can
    # deselect scopes on the consent screen, so this records the real
    # grant rather than what we asked for — it's what has_scope() checks
    # before offering calendar or send features.
    granted_scopes = db.Column(db.Text)

    # Sync bookkeeping. last_sync_error is kept (rather than only logged)
    # so a mailbox that quietly stopped syncing shows up in the UI.
    last_sync_at = db.Column(db.DateTime)
    last_sync_error = db.Column(db.Text)
    last_calendar_sync_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    company = db.relationship("Company")
    threads = db.relationship(
        "EmailThread", back_populates="account", cascade="all, delete-orphan",
    )
    calendar_events = db.relationship(
        "CalendarEvent", back_populates="account", cascade="all, delete-orphan",
    )

    # -- token access -------------------------------------------------------
    #
    # Plaintext exists only in memory, only for the length of an API call.

    @property
    def access_token(self) -> str | None:
        return crypto.decrypt(self.access_token_encrypted)

    @access_token.setter
    def access_token(self, value: str | None) -> None:
        self.access_token_encrypted = crypto.encrypt(value)

    @property
    def refresh_token(self) -> str | None:
        return crypto.decrypt(self.refresh_token_encrypted)

    @refresh_token.setter
    def refresh_token(self, value: str | None) -> None:
        # Google only returns a refresh token on the *first* consent for a
        # given client/account pair. A re-auth that omits it must not wipe
        # the one we already hold, or the account silently stops refreshing.
        if value:
            self.refresh_token_encrypted = crypto.encrypt(value)

    @property
    def scopes(self) -> list[str]:
        return (self.granted_scopes or "").split()

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    @property
    def is_expired(self) -> bool:
        """True when the access token needs refreshing before the next call.

        A minute of slack: a token with 5 seconds left is expired for any
        practical purpose, and the round trip alone can outlast it.
        """
        if self.token_expiry is None:
            return True
        return utcnow() >= self.token_expiry.replace(second=0, microsecond=0)

    @property
    def status_label(self) -> str:
        """One word for the integrations page."""
        if self.last_sync_error:
            return "error"
        if not self.sync_enabled:
            return "paused"
        return "connected"


class EmailSyncSettings(db.Model):
    """Per-tenant sync configuration. One row per company, created on demand
    (see for_company) so a company that never opens the page still syncs
    with sane defaults rather than not at all."""

    __tablename__ = "email_sync_settings"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(
        db.Integer, db.ForeignKey("companies.id"), nullable=False, unique=True,
    )
    sync_enabled = db.Column(db.Boolean, nullable=False, default=True)
    sync_frequency = db.Column(db.Integer, nullable=False, default=15)  # minutes
    sync_sent_mail = db.Column(db.Boolean, nullable=False, default=True)
    sync_attachments = db.Column(db.Boolean, nullable=False, default=False)
    # How far back the *first* sync of a new account reaches. Later syncs
    # are incremental from last_sync_at, so this only ever applies once per
    # account — it's a "how much history do you want" dial, not a window.
    initial_sync_days = db.Column(db.Integer, nullable=False, default=90)
    # Whether to keep messages that match no client. Off means the app only
    # ever stores mail it can tie to someone on file, which is the
    # privacy-minimising default; on turns unmatched inbound mail into the
    # lead inbox (see routes.leads).
    keep_unmatched = db.Column(db.Boolean, nullable=False, default=True)

    company = db.relationship("Company")

    @classmethod
    def for_company(cls, company_id: int) -> "EmailSyncSettings":
        """This company's settings, creating the default row if needed.

        Does not commit — the caller's transaction decides, same as
        everywhere else in the app.
        """
        settings = cls.query.filter_by(company_id=company_id).first()
        if settings is None:
            settings = cls(company_id=company_id)
            db.session.add(settings)
            db.session.flush()
        return settings


class EmailThread(db.Model):
    """A conversation, as the provider groups it.

    `client_id` is nullable on purpose: an unmatched thread is not a
    failure, it's the lead inbox. Threads only get a client once an address
    on them matches a Client.email for the same company (see
    sync/email_sync.py) or someone converts it by hand.
    """

    __tablename__ = "email_threads"
    __table_args__ = (
        db.UniqueConstraint(
            "email_account_id", "provider_thread_id",
            name="uq_email_thread_account_provider_id",
        ),
        db.Index("ix_email_threads_company_last_message", "company_id", "last_message_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    email_account_id = db.Column(
        db.Integer, db.ForeignKey("email_accounts.id"), nullable=False,
    )
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"))
    provider_thread_id = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(500))
    last_message_date = db.Column(db.DateTime)
    # Reserved for the AI module (§3 of the requirements). Nothing writes it
    # in Phase 1; it's here so adding summaries later isn't a migration on a
    # table that by then has real volume in it.
    summary = db.Column(db.Text)

    # Triaged out of the lead inbox. The row is *kept* — deleting it would
    # only mean the next sync downloads the thread again and it reappears,
    # which is a dismiss button that looks broken.
    dismissed_at = db.Column(db.DateTime)
    # "hidden" (local only, reversible) or "trashed" (also moved to the
    # provider's Trash). Recorded because the two aren't equally undoable:
    # hidden can be restored from here, trashed has to be recovered in
    # Gmail, and the UI must not offer a button that silently does nothing.
    dismissed_reason = db.Column(db.String(20))
    # When someone first opened this conversation. Backs the "New" pill,
    # which is about *this thread* having been read — so it's stored per
    # thread rather than derived from one "last looked at the inbox"
    # timestamp, which marked everything read whether or not it was.
    #
    # Company-wide, not per user: a studio triages one shared inbox, and
    # the badge beside it counts what's still waiting for anyone to deal
    # with (see EmailThread.is_awaiting_triage). Two markers disagreeing
    # about what's been handled is worse than either rule on its own.
    opened_at = db.Column(db.DateTime)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    company = db.relationship("Company")
    account = db.relationship("EmailAccount", back_populates="threads")
    client = db.relationship("Client")
    messages = db.relationship(
        "EmailMessage", back_populates="thread", cascade="all, delete-orphan",
        order_by="EmailMessage.received_date",
    )

    @property
    def is_lead(self) -> bool:
        """No client on file for anyone in this conversation."""
        return self.client_id is None

    @property
    def is_dismissed(self) -> bool:
        return self.dismissed_at is not None

    @property
    def is_unopened(self) -> bool:
        """Nobody has read this conversation yet — what "New" means."""
        return self.opened_at is None

    @property
    def is_awaiting_triage(self) -> bool:
        """A lead nobody has dealt with yet — what the badge counts.

        "Dealt with" is one of the three things that actually resolve a
        lead: converted to a client, hidden, or trashed. Reading it isn't
        one of them, which is the point — opening the inbox to see what's
        there mustn't clear the reminder that it still needs doing.
        """
        return self.is_lead and not self.is_dismissed

    @property
    def was_trashed(self) -> bool:
        """Dismissed by moving it to the provider's Trash, not just hidden."""
        return self.dismissed_reason == DISMISSED_TRASHED

    @property
    def was_auto_hidden(self) -> bool:
        """Hidden by a sender rule rather than by a person."""
        return self.dismissed_reason == DISMISSED_AUTO

    def dismiss(self, reason: str = "hidden") -> None:
        self.dismissed_at = utcnow()
        self.dismissed_reason = reason

    def restore(self) -> None:
        self.dismissed_at = None
        self.dismissed_reason = None

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def latest_message(self) -> "EmailMessage | None":
        return self.messages[-1] if self.messages else None

    @property
    def display_subject(self) -> str:
        return self.subject or "(no subject)"

    @property
    def counterparty(self) -> str | None:
        """The other side of the conversation — the first incoming sender,
        falling back to the first recipient of an outgoing message.

        This is what the lead inbox shows and what "create a client from
        this thread" pre-fills, so it wants the human's address, not ours.
        """
        for message in self.messages:
            if message.direction == DIRECTION_INCOMING and message.sender:
                return message.sender
        for message in self.messages:
            if message.recipients:
                return message.recipient_list[0] if message.recipient_list else None
        return None


def _address_only(value: str | None) -> str:
    """The bare address out of a possibly `Name <addr>` string, lowercased.

    For comparison only — never for display, which keeps whatever the header
    actually said.
    """
    return parseaddr(value or "")[1].strip().lower()


# The line a mail client writes above the history it quotes. Matching it (as
# well as the ">" lines themselves) is what lets the attribution disappear
# along with the quote it introduces, instead of being left dangling. English
# and French, since the studio's clients write in both.
_QUOTE_ATTRIBUTION = re.compile(
    r"^(on\b.*\bwrote\s*:"
    r"|le\b.*\ba\s+écrit\s*:"
    r"|-{2,}\s*(original message|forwarded message|message d'origine).*"
    r"|_{5,})$",
    re.IGNORECASE,
)

# Gmail hard-wraps that attribution, so "wrote:" routinely lands on the next
# line ("On Tue, ... Arnaud Rouillot <a@b>\nwrote:"). Each candidate line is
# therefore also tested joined with the couple that follow it, or the wrapped
# form matches nothing and the whole quote survives.
_QUOTE_ATTRIBUTION_MAX_LINES = 3


def _starts_an_attribution(lines: list[str], index: int) -> bool:
    """Whether the quote's attribution begins at `lines[index]`, wrapped or not."""
    for span in range(1, _QUOTE_ATTRIBUTION_MAX_LINES + 1):
        joined = " ".join(part.strip() for part in lines[index:index + span]).strip()
        if _QUOTE_ATTRIBUTION.match(joined):
            return True
    return False


class EmailMessage(db.Model):
    """A single message within a thread.

    Both body_text and body_html are kept. Text is what gets quoted into a
    reply and what any future AI reads; HTML is what a human should see
    when the sender bothered to format something. Storing only one means
    reconstructing the other badly.
    """

    __tablename__ = "email_messages"
    __table_args__ = (
        db.UniqueConstraint(
            "thread_id", "provider_message_id",
            name="uq_email_message_thread_provider_id",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    thread_id = db.Column(db.Integer, db.ForeignKey("email_threads.id"), nullable=False)
    provider_message_id = db.Column(db.String(255), nullable=False)
    sender = db.Column(db.String(255))
    sender_name = db.Column(db.String(255))
    # Comma-separated addresses, as they appear in the header. A join table
    # would be the "correct" shape, but nothing in Phase 1 queries by
    # recipient — it's display data — and three extra tables to render a
    # To: line isn't a trade worth making yet.
    recipients = db.Column(db.Text)
    cc = db.Column(db.Text)
    bcc = db.Column(db.Text)
    subject = db.Column(db.String(500))
    body_text = db.Column(db.Text)
    body_html = db.Column(db.Text)
    received_date = db.Column(db.DateTime)
    direction = db.Column(db.String(10), nullable=False, default=DIRECTION_INCOMING)
    has_attachments = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    thread = db.relationship("EmailThread", back_populates="messages")
    attachments = db.relationship(
        "EmailAttachment", back_populates="message", cascade="all, delete-orphan",
    )

    @property
    def is_incoming(self) -> bool:
        return self.direction == DIRECTION_INCOMING

    @property
    def recipient_list(self) -> list[str]:
        return [part.strip() for part in (self.recipients or "").split(",") if part.strip()]

    @property
    def cc_list(self) -> list[str]:
        return [part.strip() for part in (self.cc or "").split(",") if part.strip()]

    @property
    def sender_label(self) -> str:
        """Who to print above the message.

        "You" for anything we sent: the mailbox it went out from is the
        studio's own address, and printing it there read as though it were the
        client's. Incoming mail shows the person's name alone — their address
        is already in the thread header — falling back to the address when the
        header carried no name.
        """
        if not self.is_incoming:
            return "You"
        return self.sender_name or self.sender or "(unknown sender)"

    @property
    def other_recipients(self) -> list[str]:
        """To/Cc addresses beyond the two ends of the conversation.

        A message between the studio's mailbox and one client has nothing to
        say in a To: line — both ends are named on the page already. A third
        party does, so those are the only ones listed ("Also sent to"). The
        thread's counterparty counts as implied even on a lead with no client
        row yet, for the same reason.
        """
        implied = {
            _address_only(self.thread.account.email_address),
            _address_only(self.thread.counterparty),
        }
        if self.thread.client:
            implied.add(_address_only(self.thread.client.email))
        implied.discard("")

        seen: set[str] = set()
        others: list[str] = []
        for raw in self.recipient_list + self.cc_list:
            address = _address_only(raw)
            if not address or address in implied or address in seen:
                continue
            seen.add(address)
            others.append(raw)
        return others

    @property
    def body_display(self) -> str:
        """`body_text` with the quoted history trimmed off.

        Every mail client quotes the whole prior conversation into a reply,
        and this app already renders those messages in their own right just
        above — so showing the quote too means reading the thread once per
        message. Trimming stops at the first quote marker (a ">" line, or the
        attribution that introduces one) and drops everything after it, since
        the quote is always at the end.

        Falls back to the untrimmed body when trimming leaves nothing: a
        forward whose entire content is quoted still has to be readable.
        """
        text = self.body_text or ""
        lines = text.splitlines()
        kept: list[str] = []
        for index, line in enumerate(lines):
            if line.strip().startswith(">") or _starts_an_attribution(lines, index):
                break
            kept.append(line)
        return "\n".join(kept).strip() or text

    @property
    def preview(self) -> str:
        """First line-ish of the body, for list rows.

        Built from body_display, so a one-word reply previews as that word
        rather than as the first 140 characters of what it quoted.
        """
        text = " ".join(self.body_display.split())
        return text[:140] + ("…" if len(text) > 140 else "")


class EmailAttachment(db.Model):
    """Attachment metadata, and optionally the bytes.

    Phase 1 stores metadata always and bytes only when
    EmailSyncSettings.sync_attachments is on — a mailbox's attachments are
    far larger than its text, and most of them are signatures and logos.
    `stored_filename` is null until the bytes are actually fetched; see
    storage/attachment_storage.py.
    """

    __tablename__ = "email_attachments"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("email_messages.id"), nullable=False)
    provider_attachment_id = db.Column(db.Text)
    filename = db.Column(db.String(255))
    mime_type = db.Column(db.String(120))
    size_bytes = db.Column(db.Integer)
    stored_filename = db.Column(db.String(255))

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    message = db.relationship("EmailMessage", back_populates="attachments")

    @property
    def is_downloaded(self) -> bool:
        return bool(self.stored_filename)

    @property
    def size_display(self) -> str:
        size = self.size_bytes or 0
        for unit in ("B", "KB", "MB"):
            if size < 1024 or unit == "MB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} MB"


class CalendarEvent(db.Model):
    """A calendar event mirrored from the provider.

    Read-mostly in Phase 1: sync pulls events in, and they are the only thing
    the month view renders (orders live on the timeline). `client_id` is
    nullable and set the same way threads are — by matching an attendee
    against Client.email.
    """

    __tablename__ = "calendar_events"
    __table_args__ = (
        db.UniqueConstraint(
            "email_account_id", "provider_event_id",
            name="uq_calendar_event_account_provider_id",
        ),
        db.Index("ix_calendar_events_company_start", "company_id", "start_time"),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    email_account_id = db.Column(
        db.Integer, db.ForeignKey("email_accounts.id"), nullable=False,
    )
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"))
    provider_event_id = db.Column(db.String(255), nullable=False)
    title = db.Column(db.String(500))
    description = db.Column(db.Text)
    location = db.Column(db.String(500))
    start_time = db.Column(db.DateTime)
    end_time = db.Column(db.DateTime)
    # Google returns all-day events as a bare date with no time. Flagged
    # rather than inferred from a midnight start, which a real 00:00 event
    # would also look like.
    all_day = db.Column(db.Boolean, nullable=False, default=False)
    # Google's own state: "confirmed" / "tentative" / "cancelled". A
    # cancelled event is kept rather than deleted so a sync can't lose an
    # event the user is still looking at; the UI filters them out.
    status = db.Column(db.String(20), default="confirmed")

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    company = db.relationship("Company")
    account = db.relationship("EmailAccount", back_populates="calendar_events")
    client = db.relationship("Client")

    @property
    def is_cancelled(self) -> bool:
        return self.status == "cancelled"


class SenderRule(db.Model):
    """What to do, automatically, with mail from a given address.

    Two actions, and they exist for opposite reasons. **Hide** is for the
    senders that make the lead inbox useless — newsletters, suppliers,
    receipts — where the work is deciding once instead of every week.
    **Convert** is for a sender that is only ever a genuine enquiry, the
    website's contact form being the case this was built for: a form
    submission is already a lead by construction, so making someone confirm
    that by hand adds nothing.

    Matched on the **sender address of an incoming message**, exactly, or by
    domain when the pattern starts with `@` (`@squarespace.info`) — a form
    relay and a newsletter provider both send from a whole domain, and
    listing every individual address they use is a losing game.

    Rules apply to **new threads only**. Adding a rule doesn't retroactively
    hide or convert history someone may be reading, the same reasoning as
    `EmailSyncSettings.keep_unmatched`.
    """

    __tablename__ = "sender_rules"
    __table_args__ = (
        db.UniqueConstraint("company_id", "pattern", name="uq_sender_rule_company_pattern"),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    # Stored lowercased; either "someone@example.com" or "@example.com".
    pattern = db.Column(db.String(255), nullable=False)
    action = db.Column(db.String(20), nullable=False)
    # Free text, so a rule someone added six months ago still explains itself.
    note = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    company = db.relationship("Company")
    fields = db.relationship(
        "SenderRuleField", back_populates="rule",
        cascade="all, delete-orphan", order_by="SenderRuleField.id",
    )

    @property
    def is_domain_rule(self) -> bool:
        return self.pattern.startswith("@")

    @property
    def action_label(self) -> str:
        return RULE_LABELS.get(self.action, self.action)

    def matches(self, address: str | None) -> bool:
        address = (address or "").strip().lower()
        if not address:
            return False
        if self.is_domain_rule:
            return address.endswith(self.pattern)
        return address == self.pattern


class SenderRuleField(db.Model):
    """One "this label in the body means this" mapping, for one rule.

    A contact form arrives as a labelled block, and only the studio knows
    what its own form calls things:

        Name: Haejung Kim
        Email: dayanee1004@gmail.com
        About: Touch-ups for Luxury Leather Bags
        Message: Hi Joe, …
        How did you hear about BY MONSIEUR | Leather Atelier?: Google Search

    So the labels are **data, not code**. Hardcoding this one form's would
    work exactly once — the next site, or a rewording of the same site,
    would silently produce clients named after the relay again.

    A row per mapping rather than a JSON blob on the rule: these are edited
    one at a time in the UI, and a malformed blob would take the whole rule
    down rather than one line of it.

    **The mapped labels are also the parser's entire vocabulary.** Nothing
    else in the body is treated as a label, so a message containing
    "Delivery: end of March" doesn't get chopped in half. See
    `sender_rules.parse_fields`.
    """

    __tablename__ = "sender_rule_fields"
    __table_args__ = (
        db.UniqueConstraint("rule_id", "label", name="uq_sender_rule_field_label"),
    )

    id = db.Column(db.Integer, primary_key=True)
    rule_id = db.Column(
        db.Integer, db.ForeignKey("sender_rules.id"), nullable=False,
    )
    # As it appears in the email, minus the colon. Matched case-insensitively.
    label = db.Column(db.String(200), nullable=False)
    target = db.Column(db.String(30), nullable=False)

    rule = db.relationship("SenderRule", back_populates="fields")

    @property
    def target_label(self) -> str:
        return FIELD_TARGET_LABELS.get(self.target, self.target)


class AutoCreatedClient(db.Model):
    """A client the app created by itself, and whether anyone has seen it.

    Backs the purple badge on Clients. A person converting a lead by hand
    already knows the client exists; one created while nobody was looking
    needs saying, because it landed in the roster without a decision.

    A row per client rather than one "last looked" timestamp per company:
    the badge has to survive someone visiting the client list for an
    unrelated reason five minutes before the sync ran, and a marker
    compared against "now" can't tell those apart.

    **Acknowledged, not deleted**, when someone opens the client list — the
    row is the record that this client arrived automatically, which is worth
    keeping once the badge is gone.
    """

    __tablename__ = "auto_created_clients"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False)
    thread_id = db.Column(db.Integer, db.ForeignKey("email_threads.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    # Null until someone has looked at the client list since it appeared.
    seen_at = db.Column(db.DateTime)

    company = db.relationship("Company")
    client = db.relationship("Client")
    thread = db.relationship("EmailThread")


# `LeadReadState` used to live here: one "when did this user last look at
# the inbox" timestamp per (company, user), backing both the badge and the
# "New" pill. Both questions turned out to be about the *thread*, not about
# the visit — a badge that cleared on arrival reminded you of nothing, and a
# marker moved by opening the list marked every thread in it read. Both now
# read off EmailThread (`is_awaiting_triage`, `opened_at`). Existing
# databases keep an unused `lead_read_states` table; nothing reads it, and
# dropping a table is not something a boot-time migration should do.


class AuditLog(db.Model):
    """Security-relevant events, per §16 of the requirements.

    Deliberately append-only in practice — nothing in the app updates or
    deletes a row. `detail` is free text rather than structured JSON
    because this is read by humans investigating "who connected that
    mailbox", not queried by machines.
    """

    __tablename__ = "communication_audit_logs"
    __table_args__ = (
        db.Index("ix_audit_company_created", "company_id", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), nullable=False)
    # Nullable: a background job acts on behalf of the company, not a user.
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    event = db.Column(db.String(60), nullable=False)
    detail = db.Column(db.Text)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    company = db.relationship("Company")
    user = db.relationship("User")


# Audit event names. Constants rather than bare strings so a typo in a
# caller is an AttributeError instead of an event nobody can search for.
AUDIT_INTEGRATION_CONNECTED = "integration_connected"
AUDIT_INTEGRATION_DISCONNECTED = "integration_disconnected"
AUDIT_EMAIL_SENT = "email_sent"
AUDIT_SYNC_RUN = "sync_run"
AUDIT_SYNC_FAILED = "sync_failed"
AUDIT_CLIENT_CREATED_FROM_EMAIL = "client_created_from_email"
AUDIT_THREAD_TRASHED = "thread_trashed"
# Kept distinct from AUDIT_CLIENT_CREATED_FROM_EMAIL: "the app did this on
# its own" is a different thing to answer for than "someone clicked it", and
# an audit log that can't tell them apart can't answer either question.
AUDIT_CLIENT_AUTO_CREATED = "client_auto_created"
AUDIT_SENDER_RULE_CHANGED = "sender_rule_changed"

AUDIT_EVENT_LABELS = {
    AUDIT_INTEGRATION_CONNECTED: "Integration connected",
    AUDIT_INTEGRATION_DISCONNECTED: "Integration disconnected",
    AUDIT_EMAIL_SENT: "Email sent",
    AUDIT_SYNC_RUN: "Sync run",
    AUDIT_SYNC_FAILED: "Sync failed",
    AUDIT_CLIENT_CREATED_FROM_EMAIL: "Client created from email",
    AUDIT_THREAD_TRASHED: "Conversation moved to Trash",
    AUDIT_CLIENT_AUTO_CREATED: "Client created automatically",
    AUDIT_SENDER_RULE_CHANGED: "Automatic mail rule changed",
}
