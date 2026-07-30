"""
The provider contract.

Two interfaces and three dataclasses. The dataclasses are the important
half: they're the vocabulary the rest of the app speaks, and they contain
nothing a non-Gmail provider couldn't supply. Anything Gmail-specific
(label ids, history ids, `payload.parts` trees) is the provider's private
business and must not appear here.

A provider is constructed with an EmailAccount and is responsible for
producing valid credentials from it — including refreshing an expired
access token. Callers never handle tokens.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class FetchedAttachment:
    """Attachment metadata as the provider reports it. Bytes are fetched
    separately and on demand — see EmailProvider.fetch_attachment."""

    filename: str
    mime_type: str | None = None
    size_bytes: int | None = None
    provider_attachment_id: str | None = None


@dataclass
class FetchedMessage:
    """One message, normalised.

    `direction` is the provider's call, not the caller's: only the provider
    knows which addresses belong to the connected mailbox. Dates are naive
    UTC (see communications/models.py).
    """

    provider_message_id: str
    provider_thread_id: str
    sender: str | None = None
    sender_name: str | None = None
    recipients: list[str] = field(default_factory=list)
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    subject: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    received_date: datetime | None = None
    direction: str = "incoming"
    attachments: list[FetchedAttachment] = field(default_factory=list)

    @property
    def participants(self) -> list[str]:
        """Every address on the message, lowercased.

        This is what client matching runs against, so it has to include
        both sides: an outgoing message to a new client is just as much
        evidence of who the thread is with as an incoming one from them.
        """
        everyone = [self.sender, *self.recipients, *self.cc, *self.bcc]
        return [address.lower() for address in everyone if address]


@dataclass
class FetchedThread:
    """A conversation and its messages."""

    provider_thread_id: str
    subject: str | None = None
    messages: list[FetchedMessage] = field(default_factory=list)

    @property
    def last_message_date(self) -> datetime | None:
        dates = [m.received_date for m in self.messages if m.received_date]
        return max(dates) if dates else None


@dataclass
class FetchedEvent:
    """A calendar event, normalised."""

    provider_event_id: str
    title: str | None = None
    description: str | None = None
    location: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    all_day: bool = False
    status: str = "confirmed"
    attendees: list[str] = field(default_factory=list)


class ProviderError(Exception):
    """Anything the provider couldn't do.

    One exception type on purpose: callers (sync jobs, the send form)
    handle "it didn't work" the same way regardless of whether the cause
    was a 401, a quota, or a socket timeout. The message carries the
    detail, and it's what ends up in EmailAccount.last_sync_error.
    """


class ReauthorizationRequired(ProviderError):
    """Credentials are gone or revoked — no retry will fix it.

    Split out from ProviderError because the *response* differs: this one
    means "tell the user to reconnect", not "try again in 15 minutes".
    """


class EmailProvider(ABC):
    """What every email backend must be able to do."""

    #: Key stored in EmailAccount.provider, and the registry lookup key.
    name: str = ""

    def __init__(self, account):
        self.account = account

    @abstractmethod
    def authenticate(self):
        """Return ready-to-use credentials, refreshing if needed.

        Implementations must persist a refreshed access token back onto the
        account — a refresh that isn't saved means every request pays for
        another one. Raise ReauthorizationRequired if the grant is gone.
        """

    @abstractmethod
    def fetch_threads(self, since=None, limit=None, include_sent=True) -> list[FetchedThread]:
        """Threads with activity since `since` (naive UTC), newest first."""

    @abstractmethod
    def fetch_messages(self, thread_id: str) -> list[FetchedMessage]:
        """Every message in one thread."""

    @abstractmethod
    def send_email(
        self, to, subject, body_text, cc=None, bcc=None,
        reply_to_message_id=None, thread_id=None,
    ) -> FetchedMessage:
        """Send, and return the sent message as the provider recorded it.

        Returning the sent message (rather than None) is what lets the
        caller store it in the right thread without waiting for the next
        sync to discover it.
        """

    @abstractmethod
    def fetch_attachment(self, message_id: str, attachment_id: str) -> bytes:
        """Raw bytes of one attachment."""

    @abstractmethod
    def trash_thread(self, thread_id: str) -> None:
        """Move a whole thread to the provider's Trash.

        **Trash, not delete.** Implementations must use a recoverable
        operation: the module deliberately holds no scope that can destroy
        mail permanently (see config.GMAIL_SCOPES), and a triage button in
        an order-planning app has no business being the thing that loses a
        studio's correspondence for good.
        """


class CalendarProvider(ABC):
    """What every calendar backend must be able to do."""

    name: str = ""

    def __init__(self, account):
        self.account = account

    @abstractmethod
    def authenticate(self):
        """As EmailProvider.authenticate."""

    @abstractmethod
    def list_events(self, start, end) -> list[FetchedEvent]:
        """Events overlapping the [start, end] window (naive UTC)."""

    @abstractmethod
    def create_event(
        self, title, start, end, description=None, location=None,
        attendees=None, all_day=False,
    ) -> FetchedEvent:
        """Create an event and return it as stored."""

    @abstractmethod
    def update_event(self, provider_event_id: str, **fields) -> FetchedEvent:
        """Patch an existing event. Only the fields given are changed."""
