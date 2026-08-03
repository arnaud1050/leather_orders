"""
Gmail and Google Calendar, behind the provider interface.

This is the vendor boundary. Everything Google-shaped — label ids, the
`payload.parts` MIME tree, base64url bodies, RFC 3339 date strings, the
`q` search syntax — is converted here into the neutral dataclasses in
base.py, and nothing outside this file should ever see a raw API response.

Both providers live in one module because they share an identity: one
Google grant covers Gmail and Calendar, so splitting them would mean two
files importing the same credentials helper to talk to the same account.
A Microsoft integration would make the same call for the same reason.
"""

import base64
import logging
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage as MimeMessage
from email.utils import getaddresses, parseaddr

from communications import config
from communications.oauth import google_oauth
from communications.providers.base import (
    CalendarProvider, EmailProvider, FetchedAttachment, FetchedEvent,
    FetchedMessage, FetchedThread, ProviderError, ReauthorizationRequired,
)

logger = logging.getLogger(__name__)

# Gmail labels that mean "this isn't correspondence". Chats, drafts, spam
# and trash all show up in an unfiltered message list and none of them
# belong in a client's email history.
_EXCLUDED_QUERY = "-in:chats -in:drafts -in:spam -in:trash"


class _GoogleBase:
    """Credential handling shared by the two Google providers."""

    def __init__(self, account):
        self.account = account
        self._service = None

    def authenticate(self):
        return google_oauth.credentials_for(self.account)

    def _build(self, api: str, version: str):
        from googleapiclient.discovery import build

        if self._service is None:
            # cache_discovery=False: the default file cache warns loudly
            # under any modern runtime and writes into the working
            # directory, which in Docker is a read-only-ish app dir.
            self._service = build(
                api, version, credentials=self.authenticate(), cache_discovery=False,
            )
        return self._service

    @staticmethod
    def _wrap(exc: Exception, what: str):
        """Turn a Google error into the module's own vocabulary.

        The distinction that matters to callers is retryable vs. not: a
        401/403 from an expired or revoked grant needs a human to
        reconnect, everything else is worth trying again next run.
        """
        from googleapiclient.errors import HttpError

        if isinstance(exc, ReauthorizationRequired):
            return exc
        if isinstance(exc, HttpError) and exc.resp.status in (401, 403):
            return ReauthorizationRequired(
                f"Google denied access while {what} ({exc.resp.status}). "
                "The mailbox may need to be reconnected."
            )
        return ProviderError(f"Google API error while {what}: {exc}")


class GmailProvider(_GoogleBase, EmailProvider):
    name = "gmail"

    def _gmail(self):
        return self._build("gmail", "v1")

    # -- reading ------------------------------------------------------------

    def fetch_threads(self, since=None, limit=None, include_sent=True) -> list[FetchedThread]:
        """Threads with a message newer than `since`.

        Two calls deep on purpose: list message ids matching the query,
        collapse them to distinct thread ids, then fetch each thread whole.
        Fetching threads rather than messages is what makes a reply land in
        the same conversation as the message it answers without us having
        to reconstruct References/In-Reply-To headers ourselves.
        """
        limit = limit or config.MAX_MESSAGES_PER_SYNC
        query = [_EXCLUDED_QUERY]
        if since:
            # Gmail's `after:` takes a date, not a timestamp, and is
            # exclusive of the day given — so a day is subtracted to make
            # sure nothing on the boundary is missed. Overlap costs one
            # redundant thread fetch; a gap costs a lost message.
            query.append(f"after:{(since - timedelta(days=1)).strftime('%Y/%m/%d')}")
        if not include_sent:
            query.append("-in:sent")

        try:
            thread_ids = self._list_thread_ids(" ".join(query), limit)
            return [self._fetch_thread(thread_id) for thread_id in thread_ids]
        except Exception as exc:  # noqa: BLE001 — normalised by _wrap
            raise self._wrap(exc, "listing messages") from exc

    def _list_thread_ids(self, query: str, limit: int) -> list[str]:
        """Distinct thread ids for a query, newest first, capped at `limit`.

        Gmail returns messages newest first, so preserving first-seen order
        while deduping keeps the most recent conversations at the front —
        which is what gets kept when a busy first sync hits the cap.
        """
        service = self._gmail()
        seen: dict[str, None] = {}
        page_token = None
        while len(seen) < limit:
            response = service.users().messages().list(
                userId="me", q=query, maxResults=min(500, limit), pageToken=page_token,
            ).execute()
            for message in response.get("messages", []):
                seen.setdefault(message["threadId"], None)
                if len(seen) >= limit:
                    break
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return list(seen)

    def fetch_messages(self, thread_id: str) -> list[FetchedMessage]:
        return self._fetch_thread(thread_id).messages

    def _fetch_thread(self, thread_id: str) -> FetchedThread:
        try:
            payload = self._gmail().users().threads().get(
                userId="me", id=thread_id, format="full",
            ).execute()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, f"fetching thread {thread_id}") from exc

        messages = [self._parse_message(raw) for raw in payload.get("messages", [])]
        return FetchedThread(
            provider_thread_id=thread_id,
            # The thread's subject is the first message's — later replies
            # carry "Re:" prefixes that would make the same conversation
            # look like a different one each sync.
            subject=messages[0].subject if messages else None,
            messages=messages,
        )

    def fetch_attachment(self, message_id: str, attachment_id: str) -> bytes:
        try:
            payload = self._gmail().users().messages().attachments().get(
                userId="me", messageId=message_id, id=attachment_id,
            ).execute()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "downloading an attachment") from exc
        return _b64url_decode(payload.get("data", ""))

    # -- parsing ------------------------------------------------------------

    def _parse_message(self, raw: dict) -> FetchedMessage:
        payload = raw.get("payload", {})
        headers = {
            header.get("name", "").lower(): header.get("value", "")
            for header in payload.get("headers", [])
        }
        sender_name, sender_address = parseaddr(headers.get("from", ""))
        body_text, body_html, attachments = _walk_parts(payload)

        return FetchedMessage(
            provider_message_id=raw["id"],
            provider_thread_id=raw.get("threadId", ""),
            sender=sender_address.lower() or None,
            sender_name=sender_name or None,
            recipients=_addresses(headers.get("to")),
            cc=_addresses(headers.get("cc")),
            bcc=_addresses(headers.get("bcc")),
            subject=headers.get("subject") or None,
            body_text=body_text,
            body_html=body_html,
            # internalDate over the Date: header — it's Gmail's own receipt
            # time in epoch ms, so it can't be a malformed or spoofed
            # header string, and it's always present.
            received_date=_from_epoch_ms(raw.get("internalDate")),
            direction=self._direction(raw, sender_address),
            attachments=attachments,
        )

    def _direction(self, raw: dict, sender_address: str) -> str:
        """Did this message leave the mailbox or arrive in it?

        The SENT label is authoritative and checked first. The From-address
        comparison is the fallback for aliases and send-as addresses, where
        Gmail labels correctly but the address won't match the account's
        primary one — and vice versa.
        """
        if "SENT" in (raw.get("labelIds") or []):
            return "outgoing"
        if sender_address and sender_address.lower() == (self.account.email_address or "").lower():
            return "outgoing"
        return "incoming"

    # -- sending ------------------------------------------------------------

    def send_email(
        self, to, subject, body_text, cc=None, bcc=None,
        reply_to_message_id=None, thread_id=None,
    ) -> FetchedMessage:
        """Send via Gmail, so it lands in the studio's real Sent Mail.

        Passing `threadId` is what makes a reply thread correctly in the
        recipient's client as well as Gmail's own view — Gmail requires the
        In-Reply-To/References headers to match too, which is why they're
        set from the message being replied to rather than left off.
        """
        message = MimeMessage()
        message["To"] = ", ".join(to) if isinstance(to, (list, tuple)) else to
        message["From"] = self.account.email_address
        message["Subject"] = subject or ""
        if cc:
            message["Cc"] = ", ".join(cc) if isinstance(cc, (list, tuple)) else cc
        if bcc:
            message["Bcc"] = ", ".join(bcc) if isinstance(bcc, (list, tuple)) else bcc
        if reply_to_message_id:
            message["In-Reply-To"] = reply_to_message_id
            message["References"] = reply_to_message_id
        message.set_content(body_text or "")

        body = {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()}
        if thread_id:
            body["threadId"] = thread_id

        try:
            sent = self._gmail().users().messages().send(userId="me", body=body).execute()
            # Read it back rather than synthesising a FetchedMessage from
            # what we submitted: Gmail assigns the id, the thread and the
            # timestamp, and the caller needs the real ones to store a row
            # that a later sync won't duplicate.
            raw = self._gmail().users().messages().get(
                userId="me", id=sent["id"], format="full",
            ).execute()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "sending a message") from exc

        return self._parse_message(raw)

    def trash_thread(self, thread_id: str) -> None:
        """Move the thread to Gmail's Trash — recoverable there for 30 days.

        `threads().trash()` is what `gmail.modify` permits;
        `threads().delete()` would need the unrestricted
        https://mail.google.com/ scope, which this module deliberately never
        requests. A trashed thread also stops coming back on sync, because
        every query carries `-in:trash` (see _EXCLUDED_QUERY).
        """
        try:
            self._gmail().users().threads().trash(userId="me", id=thread_id).execute()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "moving a conversation to Trash") from exc

    def is_trashed(self, thread_id: str) -> bool | None:
        """Whether the thread still carries Gmail's TRASH label.

        `format="minimal"` fetches label ids without message bodies — this
        runs once per locally-trashed thread per sync, so it has no business
        downloading mail nobody asked for.

        A 404 means the thread is gone for good (Gmail purges Trash after
        ~30 days, and the user may have deleted it there outright), which is
        `None`, not `False`: nothing to recover.
        """
        from googleapiclient.errors import HttpError

        try:
            thread = self._gmail().users().threads().get(
                userId="me", id=thread_id, format="minimal",
            ).execute()
        except HttpError as exc:
            if exc.resp.status == 404:
                return None
            raise self._wrap(exc, "checking a conversation in Trash") from exc
        except Exception as exc:  # noqa: BLE001 — normalised by _wrap
            raise self._wrap(exc, "checking a conversation in Trash") from exc

        messages = thread.get("messages") or []
        if not messages:
            return None  # nothing left to read a label off
        # Messages in a thread can disagree (a reply arriving after the rest
        # was trashed). Any one of them out of Trash means the conversation
        # is back in the mailbox.
        return all("TRASH" in (message.get("labelIds") or []) for message in messages)

    def rfc822_message_id(self, provider_message_id: str) -> str | None:
        """The `Message-ID:` header for a stored message.

        Gmail's own id is not the same thing, and it's the RFC 822 one that
        In-Reply-To has to reference for a reply to thread properly in
        every other mail client.
        """
        try:
            raw = self._gmail().users().messages().get(
                userId="me", id=provider_message_id, format="metadata",
                metadataHeaders=["Message-ID"],
            ).execute()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "reading a message id") from exc
        for header in raw.get("payload", {}).get("headers", []):
            if header.get("name", "").lower() == "message-id":
                return header.get("value")
        return None


class GoogleCalendarProvider(_GoogleBase, CalendarProvider):
    name = "gmail"  # same account/grant as GmailProvider

    def _calendar(self):
        return self._build("calendar", "v3")

    def list_events(self, start, end) -> list[FetchedEvent]:
        try:
            response = self._calendar().events().list(
                calendarId="primary",
                timeMin=_to_rfc3339(start),
                timeMax=_to_rfc3339(end),
                # Recurring events expanded into real occurrences —
                # otherwise a weekly meeting is one row with a recurrence
                # rule we'd have to evaluate ourselves to draw a calendar.
                singleEvents=True,
                orderBy="startTime",
                maxResults=250,
            ).execute()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "listing calendar events") from exc
        return [_parse_event(item) for item in response.get("items", [])]

    def create_event(
        self, title, start, end, description=None, location=None,
        attendees=None, all_day=False, notify=False,
    ) -> FetchedEvent:
        try:
            created = self._calendar().events().insert(
                calendarId="primary",
                body=_event_body(title, start, end, description, location, attendees, all_day),
                sendUpdates=_send_updates(notify),
            ).execute()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "creating a calendar event") from exc
        return _parse_event(created)

    def update_event(self, provider_event_id: str, **fields) -> FetchedEvent:
        """Patch, not update: PATCH sends only the fields being changed, so
        editing a title can't blank out a field we never loaded.

        `attendees` is the exception and has to be handled with care — patching
        it *replaces* the array rather than merging, so a caller passing a
        partial list uninvites the rest. It's therefore only sent when the key
        is present at all, and callers are expected to pass the full list they
        want the event to end up with (see the docstring on the base class).
        """
        body = {}
        if "title" in fields:
            body["summary"] = fields["title"]
        if "description" in fields:
            body["description"] = fields["description"]
        if "location" in fields:
            body["location"] = fields["location"]
        if "attendees" in fields:
            body["attendees"] = [
                {"email": address} for address in (fields["attendees"] or [])
            ]
        all_day = fields.get("all_day", False)
        if fields.get("start"):
            body["start"] = _event_time(fields["start"], all_day)
        if fields.get("end"):
            body["end"] = _event_time(fields["end"], all_day, is_end=True)

        try:
            updated = self._calendar().events().patch(
                calendarId="primary", eventId=provider_event_id, body=body,
                sendUpdates=_send_updates(fields.get("notify", False)),
            ).execute()
        except Exception as exc:  # noqa: BLE001
            raise self._wrap(exc, "updating a calendar event") from exc
        return _parse_event(updated)


# ---------------------------------------------------------------------------
# Payload helpers. Free functions rather than methods — none of them touch
# the account, and keeping them separate makes them testable without a
# credential.
# ---------------------------------------------------------------------------

def _addresses(header_value: str | None) -> list[str]:
    """Every address in a To/Cc/Bcc header, lowercased.

    getaddresses handles the cases a split(",") gets wrong — quoted display
    names containing commas, and group syntax.
    """
    if not header_value:
        return []
    return [addr.lower() for _, addr in getaddresses([header_value]) if addr]


def _b64url_decode(data: str) -> bytes:
    """Gmail's base64url, which omits padding that Python requires."""
    if not data:
        return b""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _walk_parts(payload: dict) -> tuple[str | None, str | None, list[FetchedAttachment]]:
    """Flatten Gmail's MIME tree into (text, html, attachments).

    The tree is arbitrarily deep — multipart/mixed wrapping
    multipart/alternative wrapping the actual bodies is routine — so this
    recurses rather than assuming a shape. The *first* text/plain and
    text/html found win: in an alternative part they're the same content in
    two formats, and later parts are usually quoted history or a signature.
    """
    text_body: str | None = None
    html_body: str | None = None
    attachments: list[FetchedAttachment] = []

    def visit(part: dict) -> None:
        nonlocal text_body, html_body

        mime_type = part.get("mimeType", "")
        body = part.get("body", {})
        filename = part.get("filename") or ""

        if filename and body.get("attachmentId"):
            attachments.append(FetchedAttachment(
                filename=filename,
                mime_type=mime_type or None,
                size_bytes=body.get("size"),
                provider_attachment_id=body["attachmentId"],
            ))
        elif mime_type == "text/plain" and text_body is None and body.get("data"):
            text_body = _b64url_decode(body["data"]).decode("utf-8", errors="replace")
        elif mime_type == "text/html" and html_body is None and body.get("data"):
            html_body = _b64url_decode(body["data"]).decode("utf-8", errors="replace")

        for child in part.get("parts", []) or []:
            visit(child)

    visit(payload)
    return text_body, html_body, attachments


def _from_epoch_ms(value) -> datetime | None:
    """Epoch milliseconds to naive UTC (the models' convention)."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _to_rfc3339(value: datetime) -> str:
    """Naive UTC out to the wire, explicitly marked as UTC.

    Without the Z, Google interprets the timestamp in the calendar's own
    timezone, which silently shifts every window by a few hours.
    """
    return value.replace(microsecond=0).isoformat() + "Z"


def _parse_google_datetime(node: dict) -> tuple[datetime | None, bool]:
    """A Google event start/end node to (naive UTC, is_all_day).

    All-day events carry `date` (no time); timed ones carry `dateTime` with
    an offset. Flagged rather than inferred, since a real 00:00 event and
    an all-day event look identical once converted.
    """
    if not node:
        return None, False
    if node.get("date"):
        return datetime.fromisoformat(node["date"]), True
    raw = node.get("dateTime")
    if not raw:
        return None, False
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed, False


def _parse_event(item: dict) -> FetchedEvent:
    start, all_day = _parse_google_datetime(item.get("start", {}))
    end, _ = _parse_google_datetime(item.get("end", {}))
    # Google's all-day end is exclusive (see _event_time). Stored inclusive, or
    # a one-day event renders on two days of the month grid.
    if all_day and end:
        end -= timedelta(days=1)
    return FetchedEvent(
        provider_event_id=item["id"],
        title=item.get("summary"),
        description=item.get("description"),
        location=item.get("location"),
        start_time=start,
        end_time=end,
        all_day=all_day,
        status=item.get("status", "confirmed"),
        attendees=[
            attendee["email"].lower()
            for attendee in item.get("attendees", []) or []
            if attendee.get("email")
        ],
    )


def _event_time(value: datetime, all_day: bool, is_end: bool = False) -> dict:
    """A start/end node for the wire.

    Google's all-day `end.date` is **exclusive** — a one-day event ends the
    following morning. Converting here (and back in _parse_event) keeps that
    quirk inside the vendor boundary: everything above this file stores and
    shows the last day the event actually covers.
    """
    if all_day:
        day = value.date() + timedelta(days=1) if is_end else value.date()
        return {"date": day.isoformat()}
    return {"dateTime": _to_rfc3339(value), "timeZone": "UTC"}


def _send_updates(notify) -> str:
    """Google's flag for "email the guests about this".

    Spelled out rather than left to the API's default, which is exactly the
    surprise this replaces: `insert` defaults to sending nothing, so attaching
    an attendee looked like inviting them while no mail ever went out.

    "all" rather than "externalOnly": the studio's own staff on an appointment
    should hear about it the same as a client does.
    """
    return "all" if notify else "none"


def _event_body(title, start, end, description, location, attendees, all_day) -> dict:
    body = {
        "summary": title,
        "start": _event_time(start, all_day),
        "end": _event_time(end, all_day, is_end=True),
    }
    if description:
        body["description"] = description
    if location:
        body["location"] = location
    if attendees:
        body["attendees"] = [{"email": address} for address in attendees]
    return body
