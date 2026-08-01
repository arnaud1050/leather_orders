"""
Gmail payload parsing.

These are the highest-value unit tests in the module: the parsing helpers
are pure functions over shapes Google actually returns, they're where a
silent wrong answer (a missed body, a message filed as incoming when it
was sent) is most likely, and they need no database or network at all.
"""

import base64
from datetime import datetime, timedelta

import pytest

from communications.providers import gmail_provider as gp


def b64(text: str) -> str:
    """Gmail's base64url, unpadded, the way the API returns it."""
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


# --- address headers ------------------------------------------------------

def test_addresses_parses_display_names():
    assert gp._addresses('"Alarie, Marie" <marie@example.com>') == ["marie@example.com"]


def test_addresses_handles_comma_inside_a_quoted_name():
    """The reason getaddresses is used instead of split(","): this input
    yields two bogus recipients under naive splitting."""
    header = '"Alarie, Marie" <marie@example.com>, ryan@example.com'
    assert gp._addresses(header) == ["marie@example.com", "ryan@example.com"]


def test_addresses_lowercases():
    assert gp._addresses("Marie@EXAMPLE.com") == ["marie@example.com"]


@pytest.mark.parametrize("empty", [None, ""])
def test_addresses_of_empty_header(empty):
    assert gp._addresses(empty) == []


# --- base64 ---------------------------------------------------------------

def test_b64url_decode_restores_missing_padding():
    """Gmail strips '=' padding; Python's decoder requires it."""
    assert gp._b64url_decode(b64("abcde")) == b"abcde"


def test_b64url_decode_of_empty():
    assert gp._b64url_decode("") == b""


# --- MIME tree ------------------------------------------------------------

def test_walk_parts_simple_plain_text():
    payload = {"mimeType": "text/plain", "body": {"data": b64("Hello there")}}
    text, html, attachments = gp._walk_parts(payload)
    assert text == "Hello there"
    assert html is None and attachments == []


def test_walk_parts_nested_multipart():
    """multipart/mixed wrapping multipart/alternative is routine, which is
    why this recurses instead of assuming one level."""
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [{
            "mimeType": "multipart/alternative",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": b64("plain body")}},
                {"mimeType": "text/html", "body": {"data": b64("<p>html body</p>")}},
            ],
        }],
    }
    text, html, _ = gp._walk_parts(payload)
    assert text == "plain body"
    assert html == "<p>html body</p>"


def test_walk_parts_keeps_the_first_body_of_each_type():
    """Later text parts are usually quoted history or a signature block."""
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": b64("real body")}},
            {"mimeType": "text/plain", "body": {"data": b64("-- signature")}},
        ],
    }
    text, _, _ = gp._walk_parts(payload)
    assert text == "real body"


def test_walk_parts_collects_attachments():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": b64("see attached")}},
            {"mimeType": "application/pdf", "filename": "mockup.pdf",
             "body": {"attachmentId": "att-1", "size": 2048}},
        ],
    }
    text, _, attachments = gp._walk_parts(payload)
    assert text == "see attached"
    assert len(attachments) == 1
    assert attachments[0].filename == "mockup.pdf"
    assert attachments[0].size_bytes == 2048
    assert attachments[0].provider_attachment_id == "att-1"


def test_walk_parts_ignores_inline_part_without_attachment_id():
    """An inline image has a filename but no attachmentId; treating it as an
    attachment would show phantom rows on every signature-bearing email."""
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [{"mimeType": "image/png", "filename": "logo.png", "body": {"size": 10}}],
    }
    _, _, attachments = gp._walk_parts(payload)
    assert attachments == []


def test_walk_parts_survives_undecodable_bytes():
    """errors="replace" — one bad byte must not lose the whole message."""
    payload = {
        "mimeType": "text/plain",
        "body": {"data": base64.urlsafe_b64encode(b"caf\xe9").decode().rstrip("=")},
    }
    text, _, _ = gp._walk_parts(payload)
    assert text is not None and text.startswith("caf")


# --- dates ----------------------------------------------------------------

def test_from_epoch_ms_returns_naive_utc():
    parsed = gp._from_epoch_ms("1769000000000")
    assert parsed.tzinfo is None
    assert parsed.year >= 2026


@pytest.mark.parametrize("bad", [None, "", "not-a-number"])
def test_from_epoch_ms_of_bad_input(bad):
    assert gp._from_epoch_ms(bad) is None


def test_to_rfc3339_marks_utc_explicitly():
    """Without the Z, Google reads the timestamp in the calendar's own zone
    and silently shifts the whole window."""
    assert gp._to_rfc3339(datetime(2026, 7, 28, 14, 30)).endswith("Z")


def test_parse_google_datetime_all_day():
    parsed, all_day = gp._parse_google_datetime({"date": "2026-07-28"})
    assert all_day is True
    assert parsed == datetime(2026, 7, 28)


def test_parse_google_datetime_converts_offset_to_utc():
    parsed, all_day = gp._parse_google_datetime({"dateTime": "2026-07-28T10:00:00-04:00"})
    assert all_day is False
    assert parsed.tzinfo is None
    assert parsed == datetime(2026, 7, 28, 14, 0)


def test_parse_google_datetime_handles_z_suffix():
    parsed, _ = gp._parse_google_datetime({"dateTime": "2026-07-28T10:00:00Z"})
    assert parsed == datetime(2026, 7, 28, 10, 0)


def test_parse_google_datetime_of_missing_node():
    assert gp._parse_google_datetime({}) == (None, False)


def test_midnight_event_is_not_mistaken_for_all_day():
    """Why all_day is a flag rather than inferred from a 00:00 start."""
    parsed, all_day = gp._parse_google_datetime({"dateTime": "2026-07-28T00:00:00Z"})
    assert parsed == datetime(2026, 7, 28, 0, 0)
    assert all_day is False


# --- events ---------------------------------------------------------------

def test_parse_event_maps_every_field():
    parsed = gp._parse_event({
        "id": "e-1", "summary": "Fitting", "description": "Second fitting",
        "location": "Studio", "status": "confirmed",
        "start": {"dateTime": "2026-07-28T10:00:00Z"},
        "end": {"dateTime": "2026-07-28T11:00:00Z"},
        "attendees": [{"email": "Marie@Example.com"}, {"displayName": "no address"}],
    })
    assert parsed.provider_event_id == "e-1"
    assert parsed.title == "Fitting"
    assert parsed.location == "Studio"
    assert parsed.start_time == datetime(2026, 7, 28, 10, 0)
    assert parsed.attendees == ["marie@example.com"]  # lowercased, address-less dropped


def test_parse_event_preserves_cancelled_status():
    """Cancelled events are stored, not dropped — see calendar_sync."""
    assert gp._parse_event({"id": "e", "status": "cancelled"}).status == "cancelled"


def test_event_body_omits_unset_optional_fields():
    body = gp._event_body("Fitting", datetime(2026, 7, 28, 10), datetime(2026, 7, 28, 11),
                          None, None, None, False)
    assert body["summary"] == "Fitting"
    assert "description" not in body and "location" not in body and "attendees" not in body


def test_event_time_all_day_uses_bare_date():
    assert gp._event_time(datetime(2026, 7, 28, 10, 30), all_day=True) == {"date": "2026-07-28"}


# Google's all-day end date is exclusive; ours is the last day the event
# covers. The conversion belongs at this boundary and nowhere else — get it
# wrong and a one-day event silently occupies two cells of the month grid.

def test_all_day_end_goes_out_exclusive():
    node = gp._event_time(datetime(2026, 7, 28), all_day=True, is_end=True)
    assert node == {"date": "2026-07-29"}


def test_all_day_end_comes_back_inclusive():
    parsed = gp._parse_event({
        "id": "e-1", "summary": "Market",
        "start": {"date": "2026-07-28"}, "end": {"date": "2026-07-29"},
    })
    assert parsed.all_day is True
    assert parsed.start_time == datetime(2026, 7, 28)
    assert parsed.end_time == datetime(2026, 7, 28)


def test_a_timed_events_end_is_untouched():
    """Only all-day ends are exclusive — a timed event's end is the real end."""
    parsed = gp._parse_event({
        "id": "e-1",
        "start": {"dateTime": "2026-07-28T10:00:00Z"},
        "end": {"dateTime": "2026-07-28T11:00:00Z"},
    })
    assert parsed.end_time == datetime(2026, 7, 28, 11, 0)


# --- message parsing / direction ------------------------------------------

class _Account:
    email_address = "studio@example.com"


def parse(raw):
    provider = gp.GmailProvider.__new__(gp.GmailProvider)
    provider.account = _Account()
    return provider._parse_message(raw)


def raw_message(headers, label_ids=(), body="Hello", message_id="m-1"):
    return {
        "id": message_id, "threadId": "t-1", "labelIds": list(label_ids),
        "internalDate": "1769000000000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": k, "value": v} for k, v in headers.items()],
            "body": {"data": b64(body)},
        },
    }


def test_parse_message_extracts_headers_and_body():
    parsed = parse(raw_message({
        "From": "Marie Alarie <marie@example.com>",
        "To": "studio@example.com",
        "Cc": "assistant@example.com",
        "Subject": "Briefcase timeline",
    }))
    assert parsed.sender == "marie@example.com"
    assert parsed.sender_name == "Marie Alarie"
    assert parsed.recipients == ["studio@example.com"]
    assert parsed.cc == ["assistant@example.com"]
    assert parsed.subject == "Briefcase timeline"
    assert parsed.body_text == "Hello"


def test_headers_are_matched_case_insensitively():
    """Gmail's header casing is not guaranteed."""
    parsed = parse(raw_message({"FROM": "marie@example.com", "SUBJECT": "Hi"}))
    assert parsed.sender == "marie@example.com"
    assert parsed.subject == "Hi"


def test_direction_incoming_by_default():
    assert parse(raw_message({"From": "marie@example.com"})).direction == "incoming"


def test_direction_outgoing_from_sent_label():
    parsed = parse(raw_message({"From": "alias@example.com"}, label_ids=["SENT"]))
    assert parsed.direction == "outgoing"


def test_direction_outgoing_when_from_matches_the_mailbox():
    """Fallback for send-as aliases where the SENT label is missing."""
    parsed = parse(raw_message({"From": "Studio <STUDIO@example.com>"}))
    assert parsed.direction == "outgoing"


def test_participants_includes_both_sides():
    """Client matching runs on this: an outgoing message to a new client is
    just as much evidence of who a thread is with as an incoming one."""
    parsed = parse(raw_message({
        "From": "studio@example.com", "To": "marie@example.com",
        "Cc": "Assistant@Example.com",
    }, label_ids=["SENT"]))
    assert set(parsed.participants) == {
        "studio@example.com", "marie@example.com", "assistant@example.com",
    }


def test_message_with_no_headers_does_not_crash():
    """Drafts and odd Gmail rows can come back essentially empty."""
    parsed = parse({"id": "m", "threadId": "t", "payload": {}})
    assert parsed.sender is None and parsed.subject is None


# --- thread assembly ------------------------------------------------------

def test_thread_subject_comes_from_the_first_message():
    """Later replies carry "Re:" prefixes, which would make one conversation
    look like a new one on every sync."""
    provider = gp.GmailProvider.__new__(gp.GmailProvider)
    provider.account = _Account()
    provider._service = None
    payload = {"messages": [
        raw_message({"Subject": "Briefcase timeline"}, message_id="m-1"),
        raw_message({"Subject": "Re: Briefcase timeline"}, message_id="m-2"),
    ]}
    messages = [provider._parse_message(raw) for raw in payload["messages"]]
    from communications.providers.base import FetchedThread

    assembled = FetchedThread("t-1", messages[0].subject, messages)
    assert assembled.subject == "Briefcase timeline"


def test_fetched_thread_last_message_date_is_the_maximum():
    from communications.providers.base import FetchedMessage, FetchedThread

    early = datetime(2026, 7, 1, 9, 0)
    late = early + timedelta(days=3)
    assembled = FetchedThread("t", "s", [
        FetchedMessage("m1", "t", received_date=late),
        FetchedMessage("m2", "t", received_date=early),
    ])
    assert assembled.last_message_date == late


def test_fetched_thread_with_no_dates():
    from communications.providers.base import FetchedThread

    assert FetchedThread("t", "s", []).last_message_date is None


# --- Trash state ----------------------------------------------------------
#
# What `is_trashed` reports decides whether a conversation the user threw
# away comes back into the lead inbox, so the label reading is worth pinning
# without going near the network. The `_gmail()` call is stubbed; everything
# below it is the real method.

class _FakeThreads:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.asked = []

    def get(self, userId, id, format=None):  # noqa: A002 — Google's own kwarg name
        self.asked.append({"id": id, "format": format})
        return self

    def execute(self):
        if self.error:
            raise self.error
        return self.response


def trash_provider(response=None, error=None):
    provider = gp.GmailProvider.__new__(gp.GmailProvider)
    provider.account = _Account()
    threads = _FakeThreads(response, error)

    class _Users:
        def threads(inner):
            return threads

    class _Service:
        def users(inner):
            return _Users()

    provider._gmail = lambda: _Service()
    provider._threads_stub = threads
    return provider


def http_error(status):
    """A googleapiclient HttpError with the status the method branches on."""
    from googleapiclient.errors import HttpError

    class _Resp:
        def __init__(self, code):
            self.status = code
            self.reason = "nope"

    return HttpError(_Resp(status), b"{}")


def test_a_thread_still_in_trash_reads_as_trashed():
    provider = trash_provider({"messages": [{"labelIds": ["TRASH"]}]})
    assert provider.is_trashed("t-1") is True


def test_a_thread_back_in_the_inbox_reads_as_recovered():
    provider = trash_provider({"messages": [{"labelIds": ["INBOX", "UNREAD"]}]})
    assert provider.is_trashed("t-1") is False


def test_any_message_out_of_trash_means_the_conversation_is_back():
    """Messages in a thread can disagree — a reply arriving after the rest
    was trashed lands in the inbox."""
    provider = trash_provider({"messages": [
        {"labelIds": ["TRASH"]}, {"labelIds": ["INBOX"]},
    ]})
    assert provider.is_trashed("t-1") is False


def test_a_missing_thread_reads_as_unknown():
    """404 is "purged or deleted outright" — nothing to recover, which is
    None rather than False so the sync leaves it dismissed."""
    provider = trash_provider(error=http_error(404))
    assert provider.is_trashed("t-1") is None


def test_a_thread_with_no_messages_reads_as_unknown():
    provider = trash_provider({"messages": []})
    assert provider.is_trashed("t-1") is None


def test_other_http_errors_are_raised_as_provider_errors():
    from communications.providers.base import ProviderError

    provider = trash_provider(error=http_error(500))
    with pytest.raises(ProviderError):
        provider.is_trashed("t-1")


def test_a_revoked_grant_still_asks_for_reconnection():
    from communications.providers.base import ReauthorizationRequired

    provider = trash_provider(error=http_error(401))
    with pytest.raises(ReauthorizationRequired):
        provider.is_trashed("t-1")


def test_the_trash_check_does_not_download_message_bodies():
    """It runs once per trashed thread per sync — fetching mail nobody asked
    for would make a cheap reconciliation an expensive one."""
    provider = trash_provider({"messages": [{"labelIds": ["TRASH"]}]})
    provider.is_trashed("t-1")
    assert provider._threads_stub.asked == [{"id": "t-1", "format": "minimal"}]
