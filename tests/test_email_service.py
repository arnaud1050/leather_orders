"""The email API the rest of the app calls: sending, reading, lead conversion."""

import pytest

from models import Client, db

from communications.models import (
    AUDIT_CLIENT_CREATED_FROM_EMAIL, AUDIT_EMAIL_SENT, AuditLog, EmailMessage,
    EmailThread,
)
from communications.providers.base import ProviderError
from communications.services import email_service

from tests import fakes


# --- reading --------------------------------------------------------------

def test_threads_for_client_is_scoped_to_the_client(thread, lead_thread, client_record):
    threads = email_service.threads_for_client(client_record.company_id, client_record.id)
    assert [t.id for t in threads] == [thread.id]


def test_threads_for_client_does_not_cross_tenants(thread, client_record, other_company):
    assert email_service.threads_for_client(other_company.id, client_record.id) == []


def test_lead_threads_are_the_unmatched_ones(thread, lead_thread, company):
    assert [t.id for t in email_service.lead_threads(company.id)] == [lead_thread.id]


def test_outgoing_only_thread_is_not_a_lead(company, account):
    """Us mailing a supplier isn't a lead — only threads with something
    incoming qualify."""
    row = EmailThread(
        company_id=company.id, email_account_id=account.id,
        provider_thread_id="t-out", subject="Order of hides",
    )
    db.session.add(row)
    db.session.flush()
    db.session.add(EmailMessage(
        thread_id=row.id, provider_message_id="m-out",
        sender="studio@example.com", recipients="supplier@example.com",
        direction="outgoing",
    ))
    db.session.flush()
    assert email_service.lead_threads(company.id) == []


def test_get_thread_refuses_another_tenants_id(thread, other_company):
    assert email_service.get_thread(other_company.id, thread.id) is None


# --- sending: validation --------------------------------------------------

def test_send_requires_a_recipient(company, account):
    with pytest.raises(email_service.EmailServiceError, match="recipient"):
        email_service.send_email(company.id, to="", subject="Hi", body_text="Hello")


def test_send_rejects_a_malformed_address(company, account):
    with pytest.raises(email_service.EmailServiceError, match="recipient"):
        email_service.send_email(company.id, to="not-an-address", subject="Hi",
                                 body_text="Hello")


def test_send_requires_a_subject_or_a_body(company, account):
    with pytest.raises(email_service.EmailServiceError, match="subject or a body"):
        email_service.send_email(company.id, to="a@example.com", subject="  ",
                                 body_text="  ")


def test_send_without_a_connected_mailbox(company):
    with pytest.raises(email_service.EmailServiceError, match="No mailbox"):
        email_service.send_email(company.id, to="a@example.com", subject="Hi",
                                 body_text="Hello")


def test_send_when_every_mailbox_has_sending_paused(company, account):
    """Distinct from "nothing connected" — telling someone to connect a
    mailbox they already have would send them to re-run OAuth to fix a
    checkbox."""
    account.send_enabled = False
    db.session.flush()
    with pytest.raises(email_service.EmailServiceError, match="turned off for every"):
        email_service.send_email(company.id, to="a@example.com", subject="Hi",
                                 body_text="Hello")


def test_send_from_a_specific_account_with_sending_paused(company, account):
    account.send_enabled = False
    db.session.flush()
    with pytest.raises(email_service.EmailServiceError, match="is turned off"):
        email_service.send_email(company.id, to="a@example.com", subject="Hi",
                                 body_text="Hello", account_id=account.id)


@pytest.mark.parametrize("raw,expected", [
    ("A@Example.com", ["a@example.com"]),
    ("a@example.com, b@example.com", ["a@example.com", "b@example.com"]),
    ("a@example.com, rubbish, b@example.com", ["a@example.com", "b@example.com"]),
    (["a@example.com"], ["a@example.com"]),
    (None, []),
])
def test_clean_addresses(raw, expected):
    assert email_service._clean_addresses(raw) == expected


# --- sending: the happy path ---------------------------------------------

def test_send_stores_the_outgoing_message(company, account, client_record):
    with fakes.fake_providers():
        message = email_service.send_email(
            company.id, to="marie@example.com", subject="Ready Friday",
            body_text="Your briefcase is ready.", client_id=client_record.id,
        )

    assert message.direction == "outgoing"
    assert message.sender == "studio@example.com"
    assert fakes.SENT_LOG[-1]["to"] == ["marie@example.com"]
    stored = EmailThread.query.one()
    assert stored.client_id == client_record.id
    assert stored.messages[0].id == message.id


def test_sending_into_a_thread_replies_rather_than_starting_a_new_one(
    company, account, client_record, thread,
):
    with fakes.fake_providers():
        email_service.send_email(
            company.id, to="marie@example.com", subject="Re: Briefcase timeline",
            body_text="Friday.", thread_id=thread.id,
        )

    assert EmailThread.query.count() == 1
    assert thread.message_count == 2
    call = fakes.SENT_LOG[-1]
    assert call["thread_id"] == "t-1"
    # In-Reply-To must reference the RFC 822 Message-ID, not Gmail's own id,
    # or other mail clients won't thread the reply.
    assert call["reply_to_message_id"] == "<m-1@mail.example.com>"


def test_a_reply_that_cannot_read_the_message_id_still_sends(
    company, account, client_record, thread,
):
    """Threading is a nicety; failing the send over it is not acceptable."""
    class NoMessageId(fakes.FakeEmailProvider):
        def rfc822_message_id(self, provider_message_id):
            raise ProviderError("metadata unavailable")

    with fakes.fake_providers():
        email_service.email_provider_for = NoMessageId
        email_service.send_email(
            company.id, to="marie@example.com", subject="Re: x",
            body_text="Friday.", thread_id=thread.id,
        )

    assert fakes.SENT_LOG[-1]["reply_to_message_id"] is None


def test_send_records_an_audit_entry(company, account, client_record):
    with fakes.fake_providers():
        email_service.send_email(company.id, to="marie@example.com",
                                 subject="Ready", body_text="Hello")

    entry = AuditLog.query.filter_by(event=AUDIT_EMAIL_SENT).one()
    assert "marie@example.com" in entry.detail
    assert entry.company_id == company.id


def test_cc_addresses_are_passed_through(company, account):
    with fakes.fake_providers():
        email_service.send_email(company.id, to="a@example.com", subject="Hi",
                                 body_text="Hello", cc="b@example.com, junk")
    assert fakes.SENT_LOG[-1]["cc"] == ["b@example.com"]


def test_sending_is_idempotent_against_a_later_sync(company, account, client_record):
    """The sent message is stored immediately, keyed on the provider's ids,
    so the sync that later sees it recognises it instead of duplicating."""
    with fakes.fake_providers():
        message = email_service.send_email(company.id, to="marie@example.com",
                                           subject="Ready", body_text="Hello")
        stored_again = email_service._store_sent_message(
            account, EmailThread.query.one(),
            fakes.FakeEmailProvider(account).send_email(
                to=["marie@example.com"], subject="Ready", body_text="Hello",
                thread_id="t-new-1",
            ),
            None,
        )
    assert EmailMessage.query.count() >= 1
    assert stored_again.thread_id == message.thread_id


def test_reply_uses_the_thread_s_own_account_not_the_default(
    company, account, thread, client_record,
):
    """Replying from the wrong address breaks the conversation on Gmail's
    side, so the thread's account wins over the company default."""
    from communications.models import EmailAccount

    second = EmailAccount(
        company_id=company.id, provider="gmail",
        email_address="other@example.com", is_default=True,
    )
    second.access_token = "a"
    second.refresh_token = "r"
    account.is_default = False
    db.session.add(second)
    db.session.flush()

    with fakes.fake_providers():
        message = email_service.send_email(
            company.id, to="marie@example.com", subject="Re: x",
            body_text="Friday.", thread_id=thread.id,
        )
    assert message.sender == "studio@example.com"


# --- lead conversion ------------------------------------------------------

def test_create_client_from_thread(company, lead_thread):
    client = email_service.create_client_from_thread(company.id, lead_thread.id)

    assert client.email == "stranger@example.com"
    assert client.first_name == "Jean" and client.last_name == "Tremblay"
    assert client.company_id == company.id
    assert db.session.get(EmailThread, lead_thread.id).client_id == client.id


def test_conversion_keeps_the_first_message_as_the_lead_s_enquiry(company, lead_thread):
    """Same field the contact-form webhook fills, so both routes into the
    app produce a client that reads the same."""
    client = email_service.create_client_from_thread(company.id, lead_thread.id)
    assert client.first_message == "Hello, do you make messenger bags?"


def test_conversion_accepts_name_overrides(company, lead_thread):
    client = email_service.create_client_from_thread(
        company.id, lead_thread.id, first_name="Jean-Luc", last_name="Tremblay",
    )
    assert client.first_name == "Jean-Luc"


def test_conversion_links_other_threads_from_the_same_address(company, account, lead_thread):
    second = EmailThread(
        company_id=company.id, email_account_id=account.id,
        provider_thread_id="t-lead-2", subject="Following up",
    )
    db.session.add(second)
    db.session.flush()
    db.session.add(EmailMessage(
        thread_id=second.id, provider_message_id="m-lead-2",
        sender="stranger@example.com", direction="incoming",
    ))
    db.session.flush()

    client = email_service.create_client_from_thread(company.id, lead_thread.id)
    assert db.session.get(EmailThread, second.id).client_id == client.id


def test_conversion_reuses_an_existing_client_with_that_address(company, account, lead_thread):
    """Otherwise a second conversation mints a duplicate client."""
    existing = Client(
        company_id=company.id, first_name="Jean", last_name="Tremblay",
        email="stranger@example.com",
    )
    db.session.add(existing)
    db.session.flush()

    client = email_service.create_client_from_thread(company.id, lead_thread.id)
    assert client.id == existing.id
    assert Client.query.count() == 1


def test_conversion_records_an_audit_entry(company, lead_thread):
    email_service.create_client_from_thread(company.id, lead_thread.id)
    entry = AuditLog.query.filter_by(event=AUDIT_CLIENT_CREATED_FROM_EMAIL).one()
    assert "stranger@example.com" in entry.detail


def test_converting_an_already_linked_thread_is_refused(company, thread):
    with pytest.raises(email_service.EmailServiceError, match="already linked"):
        email_service.create_client_from_thread(company.id, thread.id)


def test_converting_another_tenants_thread_is_refused(other_company, lead_thread):
    with pytest.raises(email_service.EmailServiceError, match="no longer exists"):
        email_service.create_client_from_thread(other_company.id, lead_thread.id)


def test_conversion_without_a_sender_address_is_refused(company, account):
    empty = EmailThread(
        company_id=company.id, email_account_id=account.id,
        provider_thread_id="t-empty",
    )
    db.session.add(empty)
    db.session.flush()
    with pytest.raises(email_service.EmailServiceError, match="No sender address"):
        email_service.create_client_from_thread(company.id, empty.id)


# --- name derivation ------------------------------------------------------

def test_split_name_uses_the_sender_display_name(company, lead_thread):
    assert email_service._split_name(None, None, lead_thread) == ("Jean", "Tremblay")


def test_split_name_handles_a_multi_word_surname(company, account):
    row = EmailThread(company_id=company.id, email_account_id=account.id,
                      provider_thread_id="t-n")
    db.session.add(row)
    db.session.flush()
    db.session.add(EmailMessage(
        thread_id=row.id, provider_message_id="m-n", sender="x@example.com",
        sender_name="Marie de la Fontaine", direction="incoming",
    ))
    db.session.flush()
    assert email_service._split_name(None, None, row) == ("Marie", "de la Fontaine")


def test_split_name_falls_back_to_the_address_local_part(company, account):
    """Both name halves are NOT NULL on Client, so a placeholder someone can
    correct beats refusing to create the client at all."""
    row = EmailThread(company_id=company.id, email_account_id=account.id,
                      provider_thread_id="t-nameless")
    db.session.add(row)
    db.session.flush()
    db.session.add(EmailMessage(
        thread_id=row.id, provider_message_id="m-nameless",
        sender="marie.alarie@example.com", sender_name=None, direction="incoming",
    ))
    db.session.flush()
    assert email_service._split_name(None, None, row) == ("Marie", "Alarie")


# --- sync_now -------------------------------------------------------------

def test_sync_now_runs_every_enabled_account(company, account, client_record):
    with fakes.fake_providers(threads=[fakes.thread()]):
        results = email_service.sync_now(company.id)
    assert len(results) == 1 and results[0].ok


def test_sync_now_skips_paused_accounts(company, account):
    account.sync_enabled = False
    db.session.flush()
    with fakes.fake_providers(threads=[fakes.thread()]):
        assert email_service.sync_now(company.id) == []


def test_sync_now_with_no_accounts(other_company):
    assert email_service.sync_now(other_company.id) == []
