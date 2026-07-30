"""
In-memory provider doubles.

Tests must never touch Google. These implement the same interfaces as
GmailProvider / GoogleCalendarProvider and are injected with the
`fake_providers` helper below, which patches the *registry lookup* rather
than the modules that call it — one seam instead of five, and it fails
loudly if a new call site forgets to go through the registry.

They also record what they were asked to do (`sent`, `created`, `calls`),
which is how tests assert on things that only exist as an outbound API
call — thread ids on a reply, patch bodies on an event update.
"""

from contextlib import contextmanager
from datetime import timedelta

from communications.models import utcnow
from communications.providers import base
from communications.providers.base import (
    FetchedAttachment, FetchedEvent, FetchedMessage, FetchedThread, ProviderError,
)


class FakeEmailProvider(base.EmailProvider):
    name = "gmail"

    #: Threads the next fetch_threads() returns. Set per test.
    threads: list = []
    #: Raise this instead of returning, to exercise failure paths.
    fail_with: Exception | None = None

    def __init__(self, account):
        super().__init__(account)
        self.sent = []
        self.calls = []

    def authenticate(self):
        return object()

    def fetch_threads(self, since=None, limit=None, include_sent=True):
        # Recorded on the module-level log as well as the instance: a
        # provider is constructed per call, so an instance attribute
        # wouldn't survive to the assertion.
        call = {"since": since, "limit": limit, "include_sent": include_sent}
        self.calls.append(call)
        FETCH_LOG.append(call)
        if type(self).fail_with:
            raise type(self).fail_with
        return list(type(self).threads)

    def fetch_messages(self, thread_id):
        for thread in type(self).threads:
            if thread.provider_thread_id == thread_id:
                return thread.messages
        return []

    def fetch_attachment(self, message_id, attachment_id):
        self.calls.append(("fetch_attachment", message_id, attachment_id))
        if type(self).fail_with:
            raise type(self).fail_with
        return b"attachment-bytes"

    def rfc822_message_id(self, provider_message_id):
        return f"<{provider_message_id}@mail.example.com>"

    def trash_thread(self, thread_id):
        TRASH_LOG.append(thread_id)
        if type(self).fail_with:
            raise type(self).fail_with

    def send_email(self, to, subject, body_text, cc=None, bcc=None,
                   reply_to_message_id=None, thread_id=None):
        record = {
            "to": list(to), "subject": subject, "body_text": body_text,
            "cc": list(cc or []), "reply_to_message_id": reply_to_message_id,
            "thread_id": thread_id,
        }
        self.sent.append(record)
        SENT_LOG.append(record)
        if type(self).fail_with:
            raise type(self).fail_with
        return FetchedMessage(
            provider_message_id=f"m-sent-{len(SENT_LOG)}",
            provider_thread_id=thread_id or f"t-new-{len(SENT_LOG)}",
            sender=self.account.email_address,
            recipients=list(to), cc=list(cc or []), subject=subject,
            body_text=body_text, received_date=utcnow(), direction="outgoing",
        )


class FakeCalendarProvider(base.CalendarProvider):
    name = "gmail"

    events: list = []
    fail_with: Exception | None = None

    def __init__(self, account):
        super().__init__(account)

    def authenticate(self):
        return object()

    def list_events(self, start, end):
        CALENDAR_LOG.append(("list_events", start, end))
        if type(self).fail_with:
            raise type(self).fail_with
        return list(type(self).events)

    def create_event(self, title, start, end, description=None, location=None,
                     attendees=None, all_day=False):
        CALENDAR_LOG.append(("create_event", title, start, end, attendees, all_day))
        if type(self).fail_with:
            raise type(self).fail_with
        return FetchedEvent(
            provider_event_id=f"e-new-{len(CALENDAR_LOG)}", title=title,
            start_time=start, end_time=end, description=description,
            location=location, all_day=all_day, attendees=list(attendees or []),
        )

    def update_event(self, provider_event_id, **fields):
        CALENDAR_LOG.append(("update_event", provider_event_id, fields))
        if type(self).fail_with:
            raise type(self).fail_with
        return FetchedEvent(
            provider_event_id=provider_event_id,
            title=fields.get("title", "Updated"),
            start_time=fields.get("start", utcnow()),
            end_time=fields.get("end", utcnow() + timedelta(hours=1)),
            description=fields.get("description"),
            location=fields.get("location"),
        )


#: Everything the fakes were asked to send/do this test, cleared by the
#: fixture. Module-level because a provider is constructed per call, so an
#: instance attribute wouldn't survive to the assertion.
SENT_LOG: list = []
CALENDAR_LOG: list = []
FETCH_LOG: list = []
TRASH_LOG: list = []


@contextmanager
def fake_providers(threads=None, events=None, email_error=None, calendar_error=None):
    """Point the provider registry at the fakes for the duration of a block.

    Patches `registry.email_provider_for` *and* the names already imported
    into the modules that use them — Python binds `from x import y` at
    import time, so patching only the registry would miss every existing
    call site.
    """
    from communications import providers as providers_pkg
    from communications.providers import registry
    from communications.services import calendar_service, email_service
    from communications.sync import calendar_sync, email_sync

    FakeEmailProvider.threads = list(threads or [])
    FakeEmailProvider.fail_with = email_error
    FakeCalendarProvider.events = list(events or [])
    FakeCalendarProvider.fail_with = calendar_error
    SENT_LOG.clear()
    CALENDAR_LOG.clear()
    FETCH_LOG.clear()
    TRASH_LOG.clear()

    email_factory = FakeEmailProvider
    calendar_factory = FakeCalendarProvider

    targets_email = [providers_pkg, registry, email_sync, email_service]
    targets_calendar = [providers_pkg, registry, calendar_sync, calendar_service]
    saved = []
    for module in targets_email:
        saved.append((module, "email_provider_for", module.email_provider_for))
        module.email_provider_for = email_factory
    for module in targets_calendar:
        saved.append((module, "calendar_provider_for", module.calendar_provider_for))
        module.calendar_provider_for = calendar_factory
    try:
        yield
    finally:
        for module, name, original in saved:
            setattr(module, name, original)
        FakeEmailProvider.threads = []
        FakeEmailProvider.fail_with = None
        FakeCalendarProvider.events = []
        FakeCalendarProvider.fail_with = None


# --- builders -------------------------------------------------------------
#
# Keep the tests readable: a test about client matching shouldn't be 15
# lines of dataclass construction.

def message(message_id="m-1", thread_id="t-1", sender="marie@example.com",
            recipients=("studio@example.com",), subject="Briefcase timeline",
            body_text="Any update?", direction="incoming", received_date=None,
            sender_name=None, cc=(), attachments=()):
    return FetchedMessage(
        provider_message_id=message_id, provider_thread_id=thread_id,
        sender=sender, sender_name=sender_name, recipients=list(recipients),
        cc=list(cc), subject=subject, body_text=body_text,
        received_date=received_date or utcnow(), direction=direction,
        attachments=list(attachments),
    )


def thread(thread_id="t-1", subject="Briefcase timeline", messages=None):
    return FetchedThread(
        provider_thread_id=thread_id, subject=subject,
        messages=list(messages) if messages is not None else [message(thread_id=thread_id)],
    )


def attachment(filename="mockup.pdf", size=2048, attachment_id="att-1"):
    return FetchedAttachment(
        filename=filename, mime_type="application/pdf", size_bytes=size,
        provider_attachment_id=attachment_id,
    )


def event(event_id="e-1", title="Fitting", start=None, end=None,
          all_day=False, status="confirmed", attendees=()):
    start = start or utcnow().replace(hour=10, minute=0, second=0, microsecond=0)
    return FetchedEvent(
        provider_event_id=event_id, title=title, start_time=start,
        end_time=end or start + timedelta(hours=1), all_day=all_day,
        status=status, attendees=list(attendees),
    )


def provider_error(message_text="boom"):
    return ProviderError(message_text)
