"""
Reading a contact form out of the body of an email.

The body below is a real Squarespace form submission, kept verbatim
(including the blank lines and the site's own wording) because that is the
thing this feature exists to read. If the parser is ever rewritten, this is
the input it has to keep handling.

The design decision under test throughout: **only the labels the studio
mapped count as labels.** A generic "anything before a colon" parser would
cut the message below in half at "Delivery: end of March", and nothing in
the text distinguishes that from a real field.
"""

from datetime import date

import pytest

from models import Client, SourceOption, db

from communications.models import (
    AUDIT_CLIENT_AUTO_CREATED, AUDIT_CLIENT_MAIL_LINKED,
    FIELD_CITY, FIELD_EMAIL, FIELD_IGNORE, FIELD_INQUIRY, FIELD_MESSAGE,
    FIELD_NAME, FIELD_PHONE, FIELD_POSTAL_CODE, FIELD_PROVINCE, FIELD_SOURCE,
    FIELD_STREET, FIELD_TARGET_GROUPS, FIELD_TARGET_LABELS, RULE_CONVERT,
    RULE_HIDE, AuditLog, AutoCreatedClient, EmailMessage, EmailThread,
    SenderRuleField, utcnow,
)
from communications.services import email_service, sender_rules
from communications.sync import email_sync

from tests import fakes


FORM = "form-submission@squarespace.info"
SITE = "BY MONSIEUR | Leather Atelier"

SQUARESPACE_BODY = f"""Sent via form submission from {SITE}

Name: Haejung Kim

Email: dayanee1004@gmail.com

About: Touch-ups for Luxury Leather Bags

Message: Hi Joe,
I hope you are well.
I am reaching out to see if you can assist with a specialized repair for a men's Mulberry bifold leather wallet. This wallet was a very meaningful gift from my father.
Thank you very much for your time.

File Upload: KakaoTalk_20260715_135524856.jpg

How did you hear about {SITE}?: Google Search

Manage Submissions

Does this submission look like spam? Report it here.
"""

# What a studio would enter in Settings → Integrations for that form.
MAPPING = [
    ("Name", FIELD_NAME),
    ("Email", FIELD_EMAIL),
    ("About", FIELD_INQUIRY),
    ("Message", FIELD_MESSAGE),
    ("File Upload", FIELD_IGNORE),
    (f"How did you hear about {SITE}?", FIELD_SOURCE),
]


@pytest.fixture
def mapped_rule(app, company):
    """A convert rule for the Squarespace relay, with the form mapped."""
    rule = sender_rules.add_rule(company.id, FORM, RULE_CONVERT)
    for label, target in MAPPING:
        sender_rules.add_field(company.id, rule.id, label, target)
    return rule


def deliver(account, body=SQUARESPACE_BODY, thread_id="t-form", sender=FORM):
    with fakes.fake_providers(threads=[fakes.thread(
        thread_id=thread_id, subject=f"Form submission from {SITE}",
        messages=[fakes.message(
            message_id=f"m-{thread_id}", thread_id=thread_id,
            sender=sender, body_text=body,
        )],
    )]):
        return email_sync.sync_account(account)


# --- the parser -----------------------------------------------------------

def test_it_reads_every_mapped_field():
    parsed = sender_rules.parse_fields(SQUARESPACE_BODY, [l for l, _ in MAPPING])
    assert parsed["name"] == "Haejung Kim"
    assert parsed["email"] == "dayanee1004@gmail.com"
    assert parsed["about"] == "Touch-ups for Luxury Leather Bags"
    assert parsed["file upload"] == "KakaoTalk_20260715_135524856.jpg"
    assert parsed[f"how did you hear about {SITE}?".lower()] == "Google Search"


def test_a_multi_line_value_keeps_its_lines():
    parsed = sender_rules.parse_fields(SQUARESPACE_BODY, [l for l, _ in MAPPING])
    message = parsed["message"]
    assert message.startswith("Hi Joe,")
    assert "I hope you are well." in message
    assert message.endswith("Thank you very much for your time.")


def test_the_message_stops_at_the_next_field():
    """"File Upload" is mapped to Ignore precisely so it ends the message —
    without it the rest of the form gets stapled to the enquiry."""
    parsed = sender_rules.parse_fields(SQUARESPACE_BODY, [l for l, _ in MAPPING])
    assert "KakaoTalk" not in parsed["message"]
    assert "Manage Submissions" not in parsed["message"]


def test_an_unmapped_colon_line_does_not_split_a_message():
    """The whole reason labels are configured rather than guessed."""
    body = (
        "Name: Haejung Kim\n"
        "\n"
        "Message: Could you take a look?\n"
        "Delivery: end of March would be ideal.\n"
        "Budget: whatever it takes.\n"
    )
    parsed = sender_rules.parse_fields(body, ["Name", "Message"])
    assert "Delivery: end of March" in parsed["message"]
    assert "Budget: whatever it takes." in parsed["message"]


def test_a_field_ends_at_a_blank_line():
    """These emails put a blank line between fields and then a footer that
    belongs to nothing. Without this, whichever field is last swallows it —
    and "Google Search" stops matching the source option it names."""
    parsed = sender_rules.parse_fields(SQUARESPACE_BODY, [l for l, _ in MAPPING])
    assert parsed[f"how did you hear about {SITE}?".lower()] == "Google Search"


def test_the_message_may_run_across_blank_lines(app, company, mapped_rule):
    """The exception to the rule above, and the reason it's an exception:
    a customer writing in paragraphs must not be truncated at the first
    one."""
    body = (
        "Name: Haejung Kim\n"
        "\n"
        "Message: First paragraph.\n"
        "\n"
        "Second paragraph.\n"
        "\n"
        "File Upload: photo.jpg\n"
        "\n"
        "Manage Submissions\n"
    )
    fields = sender_rules.client_fields_from(mapped_rule, body)
    assert "First paragraph." in fields["first_message"]
    assert "Second paragraph." in fields["first_message"]
    assert "photo.jpg" not in fields["first_message"]
    assert "Manage Submissions" not in fields["first_message"]


def test_labels_are_matched_loosely():
    """Case and a trailing colon aren't things anyone should have to get
    right by hand when copying a label off an email."""
    body = "NAME: Haejung Kim\n"
    assert sender_rules.parse_fields(body, ["name:"]) == {"name": "Haejung Kim"}


def test_an_indented_body_still_parses():
    """A forwarded form arrives quoted and indented."""
    body = "    Name: Haejung Kim\n    Email: dayanee1004@gmail.com\n"
    parsed = sender_rules.parse_fields(body, ["Name", "Email"])
    assert parsed["name"] == "Haejung Kim"


def test_a_longer_label_wins_over_a_shorter_one():
    """"How did you hear about us?" must not be eaten by a "How" mapping."""
    body = "How: not this\nHow did you hear about us?: Google Search\n"
    parsed = sender_rules.parse_fields(body, ["How", "How did you hear about us?"])
    assert parsed["how did you hear about us?"] == "Google Search"
    assert parsed["how"] == "not this"


def test_a_missing_field_is_simply_absent():
    parsed = sender_rules.parse_fields("Name: Haejung Kim\n", ["Name", "Phone"])
    assert "phone" not in parsed


def test_an_empty_value_is_dropped():
    """A form field nobody filled in shouldn't blank anything out."""
    parsed = sender_rules.parse_fields("Name: Haejung Kim\nPhone:\n", ["Name", "Phone"])
    assert "phone" not in parsed


@pytest.mark.parametrize("body", [None, "", "   "])
def test_an_empty_body_parses_to_nothing(body):
    assert sender_rules.parse_fields(body, ["Name"]) == {}


def test_no_labels_means_no_parsing():
    assert sender_rules.parse_fields(SQUARESPACE_BODY, []) == {}


# --- turning parsed fields into client details ----------------------------

def test_a_full_name_is_split(app, company, mapped_rule):
    fields = sender_rules.client_fields_from(mapped_rule, SQUARESPACE_BODY)
    assert fields["first_name"] == "Haejung"
    assert fields["last_name"] == "Kim"


def test_a_one_word_name_keeps_the_last_name_empty(app, company, mapped_rule):
    fields = sender_rules.client_fields_from(mapped_rule, "Name: Cher\n")
    assert fields["first_name"] == "Cher"
    assert fields["last_name"] == ""


def test_ignored_fields_are_not_returned(app, company, mapped_rule):
    fields = sender_rules.client_fields_from(mapped_rule, SQUARESPACE_BODY)
    assert FIELD_IGNORE not in fields
    assert "KakaoTalk_20260715_135524856.jpg" not in fields.values()


def test_an_unmapped_rule_returns_nothing(app, company):
    """So it falls back to the sender's address exactly as it did before
    mapping existed."""
    rule = sender_rules.add_rule(company.id, FORM, RULE_CONVERT)
    assert sender_rules.client_fields_from(rule, SQUARESPACE_BODY) == {}


# --- end to end -----------------------------------------------------------

def test_the_client_is_the_person_not_the_relay(app, company, account, mapped_rule):
    """The point of the whole feature."""
    result = deliver(account)

    assert result.clients_auto_created == 1
    client = Client.query.filter_by(company_id=company.id).one()
    assert client.name == "Haejung Kim"
    assert client.email == "dayanee1004@gmail.com"
    assert Client.query.filter_by(email=FORM).count() == 0


def test_the_enquiry_details_land_on_the_client(app, company, account, mapped_rule):
    deliver(account)
    client = Client.query.filter_by(company_id=company.id).one()

    assert client.inquiry_type == "Touch-ups for Luxury Leather Bags"
    assert client.first_message.startswith("Hi Joe,")
    assert "Mulberry bifold" in client.first_message


def test_the_source_is_matched_to_an_existing_option(app, company, account, mapped_rule):
    option = SourceOption(company_id=company.id, label="Google Search")
    db.session.add(option)
    db.session.commit()

    deliver(account)
    client = Client.query.filter_by(company_id=company.id).one()
    assert [s.label for s in client.sources] == ["Google Search"]


def test_an_unknown_source_is_ignored_not_invented(app, company, account, mapped_rule):
    """An arbitrary string off a public form must not be able to create
    options that then appear on everyone's client page and in analytics."""
    deliver(account)

    assert SourceOption.query.filter_by(company_id=company.id).count() == 0
    assert Client.query.filter_by(company_id=company.id).one().sources == []


def test_the_thread_is_linked_to_the_person(app, company, account, mapped_rule):
    """So the conversation shows up under Haejung Kim, even though it
    arrived from Squarespace."""
    deliver(account)
    client = Client.query.filter_by(company_id=company.id).one()
    assert len(email_service.threads_for_client(company.id, client.id)) == 1


def test_a_second_enquiry_reuses_the_client(app, company, account, mapped_rule):
    deliver(account)
    deliver(account, thread_id="t-form-2")
    assert Client.query.filter_by(company_id=company.id).count() == 1


def test_a_second_enquiry_does_not_overwrite_edited_details(
    app, company, account, mapped_rule,
):
    """The rule runs unattended, so it fills blanks and never overwrites:
    a phone number someone corrected on the client page beats whatever was
    retyped into a web form."""
    deliver(account)
    client = Client.query.filter_by(company_id=company.id).one()
    client.phone = "555-0100"
    client.inquiry_type = "Wallet repair (confirmed by phone)"
    db.session.commit()

    deliver(account, thread_id="t-form-2")

    assert client.phone == "555-0100"
    assert client.inquiry_type == "Wallet repair (confirmed by phone)"


def test_a_blank_field_is_filled_in_on_a_later_enquiry(app, company, account, mapped_rule):
    sender_rules.add_field(company.id, mapped_rule.id, "Phone", FIELD_PHONE)
    deliver(account)
    client = Client.query.filter_by(company_id=company.id).one()
    assert not client.phone

    deliver(account, body=SQUARESPACE_BODY + "\nPhone: 555-0199\n", thread_id="t-form-2")
    assert client.phone == "555-0199"


def test_a_form_with_no_email_falls_back_to_the_sender(app, company, account, mapped_rule):
    """Better a client under the relay's address, which someone can fix,
    than no client and a silent failure."""
    deliver(account, body="Name: Haejung Kim\n")
    client = Client.query.filter_by(company_id=company.id).one()
    assert client.email == FORM
    assert client.name == "Haejung Kim"


def test_the_enquiry_is_announced_exactly_once(app, company, account, mapped_rule):
    """A form submission is one event, and the Clients link says so once.
    Both purple badges firing — "1" beside "1" — was the app counting the
    arrival and the enquiry as two things. The unread one wins (N-10a): it's
    the half with work attached."""
    deliver(account)

    assert email_service.unread_client_mail_count(company.id) == 1
    assert sender_rules.unseen_client_count(company.id) == 0


# --- the address block ----------------------------------------------------
#
# Four more targets, three of them free text. `province` is the one with
# consequences: it is two characters wide and selects the tax rate, so it is
# normalised through the host's `normalize_province` and **dropped** when it
# doesn't resolve, rather than stored as typed.

ADDRESS_MAPPING = [
    ("Name", FIELD_NAME),
    ("Email", FIELD_EMAIL),
    ("Street", FIELD_STREET),
    ("City", FIELD_CITY),
    ("Province", FIELD_PROVINCE),
    ("Postal code", FIELD_POSTAL_CODE),
]


@pytest.fixture
def address_rule(app, company):
    rule = sender_rules.add_rule(company.id, FORM, RULE_CONVERT)
    for label, target in ADDRESS_MAPPING:
        sender_rules.add_field(company.id, rule.id, label, target)
    return rule


def address_body(province="Quebec", **overrides):
    fields = {
        "Name": "Haejung Kim",
        "Email": "haejung@example.com",
        "Street": "1240 rue Saint-Denis",
        "City": "Montréal",
        "Province": province,
        "Postal code": "h2x 3j5",
    }
    fields.update(overrides)
    return "\n\n".join(f"{label}: {value}" for label, value in fields.items()) + "\n"


def only_client(company):
    return Client.query.filter_by(company_id=company.id).one()


def test_the_address_lands_on_the_client(app, company, account, address_rule):
    deliver(account, body=address_body())
    client = only_client(company)
    assert client.street == "1240 rue Saint-Denis"
    assert client.city == "Montréal"


def test_the_postal_code_is_uppercased(app, company, account, address_rule):
    """Matching what /clients/<id>/edit already does with the same field."""
    deliver(account, body=address_body())
    assert only_client(company).postal_code == "H2X 3J5"


@pytest.mark.parametrize("written", ["QC", "quebec", "Québec", "QUEBEC"])
def test_a_province_is_normalised_however_it_was_written(
    app, company, account, address_rule, written,
):
    deliver(account, body=address_body(province=written))
    assert only_client(company).province == "QC"


def test_an_unrecognised_province_is_dropped_not_stored(
    app, company, account, address_rule,
):
    """"Out of country" truncated to "Ou" would match no tax rule and bill
    them GST-only, with nothing on screen looking wrong."""
    deliver(account, body=address_body(province="Out of country"))
    client = only_client(company)
    assert client.province is None
    # The rest of the address still lands — one bad line doesn't lose the lot.
    assert client.city == "Montréal"


def test_a_dropped_province_leaves_the_tax_honest(
    app, company, account, address_rule,
):
    """The point of dropping it: the order charges nothing and says why.

    And it says the *right* why — `no_buyer_province` ("nobody has told us"),
    not `unknown_province` ("there's something on file we can't read"). Storing
    the raw text would produce the second, which reads as bad data rather than
    as a question still to be answered.
    """
    from models import Order, OrderLine

    deliver(account, body=address_body(province="Out of country"))
    order = Order(client_id=only_client(company).id, item="Wallet",
                  start=date(2026, 8, 1), due=date(2026, 8, 20), status="confirmed")
    db.session.add(order)
    db.session.flush()
    db.session.add(OrderLine(
        order_id=order.id, description="Repair", quantity=1, unit_price=100.0,
    ))
    db.session.flush()

    assert order.tax_lines == []
    assert order.tax_status == "no_buyer_province"


def test_the_address_fills_blanks_only(app, company, account, address_rule):
    """More consequential here than for phone: a returning client who moved
    keeps the address on file until somebody changes it by hand. An
    unattended rule must not rewrite a billing address."""
    existing = Client(
        company_id=company.id, first_name="Haejung", last_name="Kim",
        email="haejung@example.com",
        street="99 Old Street", city="Toronto", province="ON",
        postal_code="M6J 1G6",
    )
    db.session.add(existing)
    db.session.flush()

    deliver(account, body=address_body())

    assert existing.street == "99 Old Street"
    assert existing.city == "Toronto"
    assert existing.province == "ON"
    assert existing.postal_code == "M6J 1G6"


def test_a_missing_address_line_is_filled_in_later(
    app, company, account, address_rule,
):
    """The other half of fill-blanks-only: it still completes a record
    nobody has finished."""
    deliver(account, body=address_body(province="Out of country"))
    client = only_client(company)
    assert client.province is None

    deliver(account, body=address_body(province="QC"), thread_id="t-form-2")
    assert client.province == "QC"


def test_every_target_the_picker_offers_is_one_the_mapping_can_apply(
    app, company, account,
):
    """A label in the dropdown that nothing writes is a mapping that silently
    does nothing — which looks identical to a form that didn't parse."""
    rule = sender_rules.add_rule(company.id, FORM, RULE_CONVERT)
    writable = set(FIELD_TARGET_LABELS) - {FIELD_IGNORE, FIELD_NAME}
    for target in sorted(writable):
        sender_rules.add_field(company.id, rule.id, f"Label {target}", target)

    body = "\n\n".join(f"Label {target}: x" for target in sorted(writable)) + "\n"
    parsed = sender_rules.client_fields_from(rule, body)
    assert set(parsed) == writable


def test_the_picker_groups_cover_every_target_exactly_once():
    """The grouped dropdown is built from FIELD_TARGET_GROUPS, so a target
    added to the labels and forgotten here would vanish from the UI."""
    grouped = [target for _, targets in FIELD_TARGET_GROUPS for target in targets]
    assert sorted(grouped) == sorted(FIELD_TARGET_LABELS)
    assert len(grouped) == len(set(grouped))


def test_the_settings_page_offers_the_address_fields(logged_in, company, address_rule):
    body = logged_in.get("/settings/integrations").get_data(as_text=True)
    assert '<optgroup label="Address">' in body
    for target in (FIELD_STREET, FIELD_CITY, FIELD_PROVINCE, FIELD_POSTAL_CODE):
        assert f'value="{target}"' in body


# --- a returning customer -------------------------------------------------
#
# The common case once the studio has been running a while: somebody
# already on the roster fills in the contact form again. It arrives from
# the same relay, so it's unmatched and the rule fires exactly as it did
# the first time — but the address in the body is a client. Nothing should
# be created; the conversation belongs on the record that exists.

def test_a_returning_customer_gets_the_thread_on_their_record(
    app, company, account, mapped_rule,
):
    existing = Client(company_id=company.id, first_name="Haejung", last_name="Kim",
                      email="dayanee1004@gmail.com")
    db.session.add(existing)
    db.session.commit()

    deliver(account)

    assert Client.query.filter_by(company_id=company.id).count() == 1
    assert len(email_service.threads_for_client(company.id, existing.id)) == 1


def test_a_returning_customer_does_not_raise_the_new_client_badge(
    app, company, account, mapped_rule,
):
    """The badge means "somebody appeared on your roster while you weren't
    looking". Nobody did — so raising it would send someone off to look for
    a client that has been there for a year."""
    db.session.add(Client(company_id=company.id, first_name="Haejung",
                          last_name="Kim", email="dayanee1004@gmail.com"))
    db.session.commit()

    deliver(account)

    assert sender_rules.unseen_client_count(company.id) == 0
    assert AutoCreatedClient.query.filter_by(company_id=company.id).count() == 0


def test_a_returning_customers_mail_shows_as_unread_client_mail(
    app, company, account, mapped_rule,
):
    """What *does* announce it, and the right badge for the job: their
    enquiry is now unread mail from a client."""
    existing = Client(company_id=company.id, first_name="Haejung", last_name="Kim",
                      email="dayanee1004@gmail.com")
    db.session.add(existing)
    db.session.commit()

    deliver(account)

    assert email_service.unread_client_mail_count(company.id, existing.id) == 1


def test_the_link_is_audited_as_a_link_not_a_creation(
    app, company, account, mapped_rule,
):
    """"Created" would be a false claim, and the question someone asks
    later — why is this thread on this client — is a different one."""
    db.session.add(Client(company_id=company.id, first_name="Haejung",
                          last_name="Kim", email="dayanee1004@gmail.com"))
    db.session.commit()

    deliver(account)

    assert AuditLog.query.filter_by(event=AUDIT_CLIENT_AUTO_CREATED).count() == 0
    entry = AuditLog.query.filter_by(event=AUDIT_CLIENT_MAIL_LINKED).one()
    assert FORM in entry.detail          # the rule that did it


def test_the_sync_summary_counts_it_as_matched_not_created(
    app, company, account, mapped_rule,
):
    db.session.add(Client(company_id=company.id, first_name="Haejung",
                          last_name="Kim", email="dayanee1004@gmail.com"))
    db.session.commit()

    result = deliver(account)

    assert result.clients_auto_created == 0
    assert result.threads_matched == 1


def test_a_returning_customers_enquiry_leaves_the_lead_inbox(
    app, company, account, mapped_rule,
):
    """It has an owner now, so it isn't a lead — same as any converted
    thread."""
    db.session.add(Client(company_id=company.id, first_name="Haejung",
                          last_name="Kim", email="dayanee1004@gmail.com"))
    db.session.commit()

    deliver(account)
    assert email_service.lead_threads(company.id) == []


def test_the_form_fills_a_returning_customers_blanks_only(
    app, company, account, mapped_rule,
):
    """Same rule as a second enquiry from a client the app created: fill
    what's missing, overwrite nothing."""
    existing = Client(company_id=company.id, first_name="Haejung", last_name="Kim",
                      email="dayanee1004@gmail.com",
                      inquiry_type="Belt (quoted last year)")
    db.session.add(existing)
    db.session.commit()

    deliver(account)

    assert existing.inquiry_type == "Belt (quoted last year)"
    assert existing.first_message.startswith("Hi Joe,")


def test_a_returning_customer_matched_by_name_is_still_a_separate_client(
    app, company, account, mapped_rule,
):
    """Matching is on the address and only the address. Two people share a
    name far more often than an inbox, and merging the wrong two client
    records is not something a sync should be able to do."""
    db.session.add(Client(company_id=company.id, first_name="Haejung",
                          last_name="Kim", email="different@example.com"))
    db.session.commit()

    deliver(account)
    assert Client.query.filter_by(company_id=company.id).count() == 2


# --- reading the conversation afterwards ----------------------------------
#
# F-15 puts the thread on the right client. These are about everything the
# page then *says* about it: the enquiry arrived from a no-reply relay, so
# every address and every name shown has to be the customer's, or the studio
# answers a robot and reads the conversation as being with Squarespace.

def relayed_thread(company):
    return EmailThread.query.filter_by(
        company_id=company.id, provider_thread_id="t-form").one()


def test_the_contact_address_is_the_customer_not_the_relay(
    app, company, account, mapped_rule,
):
    """F-16 at the source. `counterparty` stays what the header said — it's
    still how the lead inbox describes an unconverted thread — and the two
    only diverge here."""
    deliver(account)
    thread = relayed_thread(company)

    assert thread.counterparty == FORM
    assert thread.contact_address == "dayanee1004@gmail.com"


def test_the_reply_box_addresses_the_customer(logged_in, company, account, mapped_rule):
    """The bug this section exists for: pressing Send on a form submission
    used to answer Squarespace's no-reply address."""
    deliver(account)
    body = logged_in.get(f"/mail/threads/{relayed_thread(company).id}").get_data(
        as_text=True)

    assert 'name="to" value="dayanee1004@gmail.com"' in body
    assert f'name="to" value="{FORM}"' not in body


def test_the_thread_header_names_the_customers_address(
    logged_in, company, account, mapped_rule,
):
    """M-1's rationale, one step further: an address printed beside "Haejung
    Kim" reads as hers."""
    deliver(account)
    body = logged_in.get(f"/mail/threads/{relayed_thread(company).id}").get_data(
        as_text=True)

    assert "dayanee1004@gmail.com" in body
    assert FORM not in body


def test_the_message_is_attributed_to_the_customer(app, company, account, mapped_rule):
    """F-17. The form submitted it; she wrote it."""
    deliver(account)
    message = relayed_thread(company).messages[0]

    assert message.sender == FORM          # the header is kept as it arrived
    assert message.sender_label == "Haejung Kim"
    assert message.sender_display == "Haejung Kim"


def test_the_thread_page_prints_the_customers_name_over_the_message(
    logged_in, company, account, mapped_rule,
):
    deliver(account)
    body = logged_in.get(f"/mail/threads/{relayed_thread(company).id}").get_data(
        as_text=True)
    assert '<span class="message__sender">Haejung Kim</span>' in body


def test_the_clients_emails_tab_lists_it_under_her_address(
    logged_in, company, account, mapped_rule,
):
    deliver(account)
    client = Client.query.filter_by(company_id=company.id).one()
    body = logged_in.get(f"/clients/{client.id}/emails").get_data(as_text=True)

    assert "dayanee1004@gmail.com" in body
    assert FORM not in body


def test_a_returning_customers_thread_reads_the_same_way(
    logged_in, company, account, mapped_rule,
):
    """The other conversion path (`R-16`): nothing is created, the thread is
    linked to the client already on file. Same relay, same display."""
    existing = Client(company_id=company.id, first_name="Haejung", last_name="Kim",
                      email="dayanee1004@gmail.com")
    db.session.add(existing)
    db.session.commit()

    deliver(account)
    thread = relayed_thread(company)

    assert thread.contact_address == "dayanee1004@gmail.com"
    assert thread.messages[0].sender_label == "Haejung Kim"


def test_a_form_with_no_email_still_reads_as_the_relay_address(
    app, company, account, mapped_rule,
):
    """F-11's fallback client is created *under* the relay address, so that
    is genuinely the only address there is. Showing it is correct — and the
    name is still hers, which is what makes the record findable."""
    deliver(account, body="Name: Haejung Kim\n")
    thread = relayed_thread(company)

    assert thread.contact_address == FORM
    assert thread.messages[0].sender_label == "Haejung Kim"


# --- what is *not* a relay ------------------------------------------------
#
# F-18. The relabelling is driven by the studio's own convert rule and
# nothing looser, because every looser rule mislabels somebody.

def test_an_ordinary_client_thread_is_untouched(app, company, account, thread):
    """No rules at all. The `thread` fixture is a client writing from their
    own address — the overwhelmingly common case."""
    assert thread.contact_address == "marie@example.com"
    assert thread.messages[0].sender_label == "Marie Alarie"


def test_a_third_party_on_a_client_thread_keeps_their_own_name(
    app, company, account, thread, mapped_rule,
):
    """The reason a convert rule is the test rather than "isn't the linked
    client": an architect cc'd into the conversation wrote this, and printing
    the client's name over it would be a fabrication."""
    db.session.add(EmailMessage(
        thread_id=thread.id, provider_message_id="m-third-party",
        sender="architect@example.com", sender_name="Luc Bergeron",
        recipients="studio@example.com", subject="Briefcase timeline",
        body_text="Adding a note on the dimensions.", received_date=utcnow(),
        direction="incoming",
    ))
    db.session.commit()

    assert thread.messages[-1].sender_label == "Luc Bergeron"


def test_a_hide_rule_is_not_a_relay(app, company, account, thread):
    """"Ignore this sender" says nothing about who wrote what it sends."""
    sender_rules.add_rule(company.id, "newsletter@example.com", RULE_HIDE)
    db.session.add(EmailMessage(
        thread_id=thread.id, provider_message_id="m-newsletter",
        sender="newsletter@example.com", sender_name="Leather Weekly",
        recipients="studio@example.com", body_text="This week in leather.",
        received_date=utcnow(), direction="incoming",
    ))
    db.session.commit()

    assert thread.messages[-1].sender_label == "Leather Weekly"


def test_a_relayed_lead_nobody_converted_still_shows_the_relay(
    app, company, account,
):
    """No convert rule fired, so there's no client to name and nothing to
    correct the header with. The lead inbox has to show what arrived."""
    with fakes.fake_providers(threads=[fakes.thread(
        thread_id="t-form", messages=[fakes.message(
            message_id="m-t-form", thread_id="t-form", sender=FORM,
            body_text=SQUARESPACE_BODY,
        )],
    )]):
        email_sync.sync_account(account)

    thread = relayed_thread(company)
    assert thread.client_id is None
    assert thread.contact_address == FORM
    assert thread.messages[0].sender_label == FORM


def test_outgoing_mail_is_still_you(app, company, account, mapped_rule):
    """M-2 doesn't move: a reply we sent through the relay's thread is ours,
    whatever the rules say about the address it's going to."""
    deliver(account)
    thread = relayed_thread(company)
    db.session.add(EmailMessage(
        thread_id=thread.id, provider_message_id="m-reply",
        sender="studio@example.com", sender_name="Studio",
        recipients="dayanee1004@gmail.com", body_text="Happy to help.",
        received_date=utcnow(), direction="outgoing",
    ))
    db.session.commit()

    assert thread.messages[-1].sender_label == "You"


def test_the_ai_transcript_names_the_customer(app, company, account, mapped_rule):
    """The same misattribution reaching a prompt: a draft that opens by
    greeting Squarespace is worse than no draft."""
    import app as app_module

    deliver(account)
    conversation = app_module._thread_conversation(
        company.id, relayed_thread(company).id)

    assert conversation["counterparty"] == "dayanee1004@gmail.com"
    assert conversation["messages"][0]["sender"] == "Haejung Kim"


# --- managing the mapping -------------------------------------------------

def test_labels_are_stored_as_typed_minus_the_colon(app, company):
    rule = sender_rules.add_rule(company.id, FORM, RULE_CONVERT)
    field = sender_rules.add_field(company.id, rule.id, "  Name:  ", FIELD_NAME)
    assert field.label == "Name"


def test_a_duplicate_label_is_refused(app, company):
    rule = sender_rules.add_rule(company.id, FORM, RULE_CONVERT)
    sender_rules.add_field(company.id, rule.id, "Name", FIELD_NAME)
    with pytest.raises(sender_rules.SenderRuleError, match="already mapped"):
        sender_rules.add_field(company.id, rule.id, "name", FIELD_EMAIL)


def test_a_blank_label_is_refused(app, company):
    rule = sender_rules.add_rule(company.id, FORM, RULE_CONVERT)
    with pytest.raises(sender_rules.SenderRuleError):
        sender_rules.add_field(company.id, rule.id, "   ", FIELD_NAME)


def test_an_unknown_target_is_refused(app, company):
    rule = sender_rules.add_rule(company.id, FORM, RULE_CONVERT)
    with pytest.raises(sender_rules.SenderRuleError):
        sender_rules.add_field(company.id, rule.id, "Name", "shoe_size")


def test_removing_a_field_stops_it_being_read(app, company, account, mapped_rule):
    email_field = next(f for f in mapped_rule.fields if f.target == FIELD_EMAIL)
    sender_rules.delete_field(company.id, email_field.id)

    deliver(account)
    assert Client.query.filter_by(company_id=company.id).one().email == FORM


def test_removing_a_rule_removes_its_fields(app, company, mapped_rule):
    sender_rules.delete_rule(company.id, mapped_rule.id)
    assert SenderRuleField.query.count() == 0


def test_fields_are_tenant_scoped(app, company, other_company, mapped_rule):
    field = mapped_rule.fields[0]
    with pytest.raises(sender_rules.SenderRuleError):
        sender_rules.delete_field(other_company.id, field.id)
    with pytest.raises(sender_rules.SenderRuleError):
        sender_rules.add_field(other_company.id, mapped_rule.id, "Phone", FIELD_PHONE)


# --- the settings UI ------------------------------------------------------

def test_the_mapping_shows_on_the_integrations_page(logged_in, company, mapped_rule):
    body = logged_in.get("/settings/integrations").get_data(as_text=True)
    assert "How did you hear about" in body
    assert "Full name (split into first and last)" in body


def test_an_unmapped_rule_says_what_will_happen(logged_in, company):
    sender_rules.add_rule(company.id, FORM, RULE_CONVERT)
    body = logged_in.get("/settings/integrations").get_data(as_text=True)
    assert "Nothing is mapped yet" in body


def test_adding_a_field_from_the_page(logged_in, csrf, company):
    rule = sender_rules.add_rule(company.id, FORM, RULE_CONVERT)
    logged_in.post(f"/integrations/rules/{rule.id}/fields", data={
        "csrf_token": csrf, "label": "Name", "target": FIELD_NAME,
    })
    assert SenderRuleField.query.one().label == "Name"


def test_removing_a_field_from_the_page(logged_in, csrf, company, mapped_rule):
    field = mapped_rule.fields[0]
    logged_in.post(f"/integrations/rules/fields/{field.id}/delete",
                   data={"csrf_token": csrf})
    assert SenderRuleField.query.filter_by(id=field.id).count() == 0


def test_a_duplicate_label_is_reported_not_raised(logged_in, csrf, company, mapped_rule):
    response = logged_in.post(f"/integrations/rules/{mapped_rule.id}/fields", data={
        "csrf_token": csrf, "label": "Name", "target": FIELD_NAME,
    })
    assert response.status_code == 302
    assert "already mapped" in logged_in.get(
        "/settings/integrations").get_data(as_text=True)


@pytest.mark.parametrize("path", [
    "/integrations/rules/1/fields", "/integrations/rules/fields/1/delete",
])
def test_field_routes_require_a_csrf_token(logged_in, path):
    assert logged_in.post(path).status_code == 400


@pytest.mark.parametrize("path", [
    "/integrations/rules/1/fields", "/integrations/rules/fields/1/delete",
])
def test_field_routes_require_a_login(app, path):
    assert app.test_client().post(path).status_code in (302, 400)
