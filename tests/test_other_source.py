"""
The "Other" catch-all on Settings > Clients > "How did you hear about us?".

At most one SourceOption per company can be marked is_other (see
set_other_source_option in app.py). Checking that option on the client page
reveals a free-text box (Client.other_source_detail) — the "please specify"
pattern most contact forms already use. The same fallback has to work when a
form arrives by email and its answer doesn't match any configured option
(see _apply_details in communications/services/email_service.py).
"""

from models import Client, SourceOption, db

from communications.models import (
    FIELD_EMAIL, FIELD_NAME, FIELD_SOURCE, RULE_CONVERT, SenderRuleField,
)
from communications.services import sender_rules
from communications.sync import email_sync

from tests import fakes


def _add_option(company, label, is_other=False):
    option = SourceOption(company_id=company.id, label=label, is_other=is_other)
    db.session.add(option)
    db.session.commit()
    return option


# --- settings: marking/unmarking "Other" -----------------------------------

def test_set_other_marks_the_option(logged_in, company):
    option = _add_option(company, "Something else")
    logged_in.post(f"/settings/sources/{option.id}/set-other")
    assert db.session.get(SourceOption, option.id).is_other is True


def test_set_other_toggles_off_on_a_second_click(logged_in, company):
    option = _add_option(company, "Something else")
    logged_in.post(f"/settings/sources/{option.id}/set-other")
    logged_in.post(f"/settings/sources/{option.id}/set-other")
    assert db.session.get(SourceOption, option.id).is_other is False


def test_only_one_option_can_be_other_at_a_time(logged_in, company):
    first = _add_option(company, "Other")
    second = _add_option(company, "Something else")
    logged_in.post(f"/settings/sources/{first.id}/set-other")

    logged_in.post(f"/settings/sources/{second.id}/set-other")

    assert db.session.get(SourceOption, first.id).is_other is False
    assert db.session.get(SourceOption, second.id).is_other is True


def test_set_other_is_tenant_scoped(logged_in, company, other_company):
    option = _add_option(other_company, "Other")
    response = logged_in.post(f"/settings/sources/{option.id}/set-other")
    assert response.status_code == 404


def test_settings_page_shows_which_option_has_the_text_box(logged_in, company):
    option = _add_option(company, "Other")
    logged_in.post(f"/settings/sources/{option.id}/set-other")

    html = logged_in.get("/settings/clients").get_data(as_text=True)
    assert "has a text box" in html
    assert "Remove text box" in html


# --- client page: the free-text box -----------------------------------------

def test_client_page_shows_the_detail_box_when_an_option_is_other(logged_in, company, client_record):
    option = _add_option(company, "Other")
    logged_in.post(f"/settings/sources/{option.id}/set-other")

    html = logged_in.get(f"/clients/{client_record.id}").get_data(as_text=True)
    assert "Please specify" in html


def test_client_page_has_no_detail_box_without_an_other_option(logged_in, client_record):
    html = logged_in.get(f"/clients/{client_record.id}").get_data(as_text=True)
    assert "Please specify" not in html


def test_saving_the_other_detail(logged_in, company, client_record):
    option = _add_option(company, "Other")
    logged_in.post(f"/settings/sources/{option.id}/set-other")

    logged_in.post(f"/clients/{client_record.id}/edit", data={
        "first_name": client_record.first_name,
        "last_name": client_record.last_name,
        "source_ids": [str(option.id)],
        "other_source_detail": "Referred by a supplier",
    })

    assert client_record.other_source_detail == "Referred by a supplier"
    assert option in client_record.sources


def test_the_detail_is_cleared_when_other_is_unchecked(logged_in, company, client_record):
    option = _add_option(company, "Other")
    logged_in.post(f"/settings/sources/{option.id}/set-other")
    client_record.sources.append(option)
    client_record.other_source_detail = "Referred by a supplier"
    db.session.commit()

    logged_in.post(f"/clients/{client_record.id}/edit", data={
        "first_name": client_record.first_name,
        "last_name": client_record.last_name,
    })

    assert client_record.other_source_detail is None


# --- automatic handling of email --------------------------------------------

FORM = "form-submission@squarespace.info"


def deliver(account, body, thread_id="t-other"):
    with fakes.fake_providers(threads=[fakes.thread(
        thread_id=thread_id, subject="Form submission",
        messages=[fakes.message(
            message_id=f"m-{thread_id}", thread_id=thread_id,
            sender=FORM, body_text=body,
        )],
    )]):
        return email_sync.sync_account(account)


def _mapped_rule(company):
    rule = sender_rules.add_rule(company.id, FORM, RULE_CONVERT)
    sender_rules.add_field(company.id, rule.id, "Name", FIELD_NAME)
    sender_rules.add_field(company.id, rule.id, "Email", FIELD_EMAIL)
    sender_rules.add_field(company.id, rule.id, "How did you hear about us", FIELD_SOURCE)
    return rule


def test_an_unmatched_source_falls_back_to_the_other_option(app, company, account):
    _add_option(company, "Other", is_other=True)
    _mapped_rule(company)

    deliver(account, "Name: Haejung Kim\nHow did you hear about us: A friend told me\n")

    client = Client.query.filter_by(company_id=company.id).one()
    assert [s.label for s in client.sources] == ["Other"]
    assert client.other_source_detail == "A friend told me"


def test_a_matching_option_still_wins_over_other(app, company, account):
    _add_option(company, "Other", is_other=True)
    _add_option(company, "Google Search")
    _mapped_rule(company)

    deliver(account, "Name: Haejung Kim\nHow did you hear about us: Google Search\n")

    client = Client.query.filter_by(company_id=company.id).one()
    assert [s.label for s in client.sources] == ["Google Search"]
    assert client.other_source_detail is None


def test_without_an_other_option_an_unmatched_source_is_still_ignored(app, company, account):
    """Same as before this feature existed — no is_other option means no
    fallback, so an arbitrary string just gets dropped."""
    _mapped_rule(company)

    deliver(account, "Name: Haejung Kim\nHow did you hear about us: A friend told me\n")

    client = Client.query.filter_by(company_id=company.id).one()
    assert client.sources == []
    assert client.other_source_detail is None


def test_the_other_detail_only_fills_a_blank(app, company, account):
    """Runs unattended, same "only fills blanks" rule as every other field
    a sender rule can populate. Two deliveries from the same real address
    (mapped out of the body, not the relay's own) land on the same client —
    same shape as test_a_blank_field_is_filled_in_on_a_later_enquiry in
    test_form_mapping.py."""
    option = _add_option(company, "Other", is_other=True)
    _mapped_rule(company)

    deliver(
        account,
        "Name: Haejung Kim\nEmail: haejung@example.com\n"
        "How did you hear about us: A friend told me\n",
        thread_id="t-other-1",
    )
    client = Client.query.filter_by(company_id=company.id).one()
    assert client.other_source_detail == "A friend told me"
    client.other_source_detail = "Already on file"
    db.session.commit()

    deliver(
        account,
        "Name: Haejung Kim\nEmail: haejung@example.com\n"
        "How did you hear about us: Something new\n",
        thread_id="t-other-2",
    )

    assert client.other_source_detail == "Already on file"
    assert option in client.sources
