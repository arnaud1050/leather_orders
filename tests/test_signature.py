"""
The per-user email signature.

Per **user**, not per company or per mailbox: a signature is written by a
person, and two people sharing one studio@ address each want their own. A
company-level one would be wrong the day a second user exists, and the
reverse is only ever momentarily redundant.

Its job is twofold — sign a hand-typed reply, and stop an AI-drafted one
inventing a sign-off. The second is why it's appended in code rather than
asked of the model: exact by construction, and it can't be paraphrased into
someone else's name.

The regressions these were checked against:
- blanking the signature being treated as "leave it alone" (it's a real
  value here, unlike a key or a prompt)
- the compose box losing a blank line to the HTML parser's first-newline rule
- one user's signature showing up for another
"""

import pytest

from ai import services as ai_services
from models import User, db

from tests.test_ai_reply import FakeVendor, _message, _thread


@pytest.fixture
def signed(user):
    user.signature = "Jane Doe\nMy Awesome Studio"
    db.session.commit()
    return user


# --- The model ------------------------------------------------------------

def test_no_signature_is_an_empty_block(user):
    """Not "\\n\\n" — a user with no signature must leave a message exactly
    as it was before this feature existed."""
    assert user.signature_block == ""


def test_a_signature_is_offset_by_a_blank_line(signed):
    assert signed.signature_block == "\n\nJane Doe\nMy Awesome Studio"


def test_a_whitespace_only_signature_counts_as_none(user):
    user.signature = "   \n  "
    assert user.signature_block == ""


def test_surrounding_whitespace_is_trimmed(user):
    user.signature = "\n\n  Jane Doe  \n\n"
    assert user.signature_block == "\n\nJane Doe"


# --- Saving ---------------------------------------------------------------

def test_saving_a_signature(logged_in, user):
    logged_in.post("/settings/account/signature", data={"signature": "Jane Doe\nMy Awesome Studio"})
    assert db.session.get(User, user.id).signature == "Jane Doe\nMy Awesome Studio"


def test_clearing_a_signature(logged_in, signed):
    """Blank is a real value here, meaning "no signature" — unlike an API
    key, there's nothing destructive about clearing it and no other way to
    say it."""
    logged_in.post("/settings/account/signature", data={"signature": ""})
    assert db.session.get(User, signed.id).signature is None


def test_a_whitespace_only_submission_is_stored_as_none(logged_in, signed):
    """One empty case to test downstream, not two."""
    logged_in.post("/settings/account/signature", data={"signature": "  \n "})
    assert db.session.get(User, signed.id).signature is None


def test_the_account_page_shows_the_saved_signature(logged_in, signed):
    body = logged_in.get("/settings/account").get_data(as_text=True)
    assert "Email signature" in body
    assert "My Awesome Studio" in body


def test_saving_a_signature_requires_a_login(app):
    response = app.test_client().post("/settings/account/signature",
                                      data={"signature": "x"})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_one_user_cannot_see_anothers_signature(logged_in, signed, company):
    """It's on the session's user, so this is really a check that nothing
    reads it off the company instead."""
    other = User(company_id=company.id, username="second")
    other.set_password("changeme")
    other.signature = "Someone Else"
    db.session.add(other)
    db.session.commit()

    body = logged_in.get("/settings/account").get_data(as_text=True)
    assert "My Awesome Studio" in body
    assert "Someone Else" not in body


# --- The compose box ------------------------------------------------------

def test_the_compose_box_is_prefilled(logged_in, signed, thread):
    body = logged_in.get(f"/mail/threads/{thread.id}").get_data(as_text=True)
    assert "Jane Doe\nMy Awesome Studio</textarea>" in body


def test_the_compose_box_keeps_its_blank_line(logged_in, signed, thread):
    """An HTML parser discards the first newline inside a <textarea>, so the
    template writes an extra one. Without it the signature arrives one blank
    line short, sitting directly under the cursor."""
    body = logged_in.get(f"/mail/threads/{thread.id}").get_data(as_text=True)
    assert 'required>\n\n\nJane Doe' in body


def test_the_compose_box_is_empty_without_a_signature(logged_in, user, thread):
    """Exactly how it behaved before this feature."""
    body = logged_in.get(f"/mail/threads/{thread.id}").get_data(as_text=True)
    assert 'required>\n</textarea>' in body


# --- AI drafts ------------------------------------------------------------

def test_a_draft_is_signed(configured_with_signature, vendor_double):
    company, _ = configured_with_signature
    draft = ai_services.suggest_reply(
        company.id, _thread(_message("Hello")), signature="Jane Doe\nMy Awesome Studio")
    assert draft.endswith("\n\nJane Doe\nMy Awesome Studio")


def test_an_unsigned_draft_is_left_alone(configured_with_signature, vendor_double):
    company, _ = configured_with_signature
    draft = ai_services.suggest_reply(company.id, _thread(_message("Hello")))
    assert draft == "Thanks for getting in touch!"
    assert not draft.endswith("\n")


def test_the_signature_is_never_asked_of_the_model(configured_with_signature, vendor_double):
    """`R-11`. Appending in code is what makes it exact — a model asked to
    reproduce a signature can paraphrase it, and a wrong name on an outgoing
    email is worse than none."""
    company, _ = configured_with_signature
    ai_services.suggest_reply(
        company.id, _thread(_message("Hello")), signature="Jane Doe\nMy Awesome Studio")
    call = vendor_double.calls[-1]
    assert "Jane Doe" not in call["instructions"]
    assert "Jane Doe" not in call["conversation"]


def test_the_default_prompt_tells_the_model_not_to_sign_off(configured_with_signature, vendor_double):
    """The other half of the same rule: without this the draft would carry
    an invented sign-off *and* the real signature under it."""
    company, _ = configured_with_signature
    ai_services.suggest_reply(company.id, _thread(_message("Hello")))
    instructions = vendor_double.calls[-1]["instructions"]
    assert "Do not write a sign-off" in instructions


def test_the_route_signs_with_the_logged_in_users_signature(logged_in, signed, thread, vendor_double):
    ai_services.save_reply_settings(signed.company_id, api_key="sk-test-123456789012")
    db.session.commit()
    response = logged_in.post("/ai/suggest-reply", json={"thread_id": thread.id})
    assert response.get_json()["suggestion"].endswith("\n\nJane Doe\nMy Awesome Studio")


def test_the_route_copes_with_no_signature(logged_in, user, thread, vendor_double):
    ai_services.save_reply_settings(user.company_id, api_key="sk-test-123456789012")
    db.session.commit()
    response = logged_in.post("/ai/suggest-reply", json={"thread_id": thread.id})
    assert response.get_json()["suggestion"] == "Thanks for getting in touch!"


# --- The default prompt ---------------------------------------------------

def test_the_default_prompt_is_first_person_singular():
    """A one-person atelier. "We" reads as a company with staff."""
    from ai import config

    prompt = config.DEFAULT_REPLY_PROMPT.lower()
    assert "first person singular" in prompt
    assert "atelier" in prompt
    assert "studio" not in prompt


@pytest.fixture
def configured_with_signature(company):
    ai_services.save_reply_settings(company.id, api_key="sk-test-123456789012")
    db.session.commit()
    return company, None


@pytest.fixture
def vendor_double(monkeypatch):
    fake = FakeVendor()
    monkeypatch.setattr(ai_services.openai_client, "generate_reply", fake)
    return fake
