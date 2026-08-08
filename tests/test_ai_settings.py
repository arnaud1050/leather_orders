"""
AI settings: what's stored, what's never stored in the clear, and the rules
that stop a settings form from destroying what it doesn't render.

The regressions these were checked against:
- storing the key as plaintext (the round-trip test passes either way; the
  ciphertext-column test is the one that catches it)
- treating a blank key field as "clear the key", which would wipe a saved
  key every time someone edited only the prompt
- deriving availability from a stored flag instead of the key's presence
"""

import pytest

from ai import config, crypto, services
from ai.models import AISettings
from models import Company, db


@pytest.fixture
def settings(company):
    return services.settings_for(company.id)


def test_settings_are_created_empty_on_first_read(company):
    """Same "created rather than None" contract as billing's profile_for —
    nothing above has to special-case "not set up yet"."""
    assert AISettings.query.filter_by(company_id=company.id).first() is None
    settings = services.settings_for(company.id)
    assert settings.company_id == company.id
    assert settings.has_text_key is False
    assert settings.has_image_key is False
    assert settings.reply_prompt == config.DEFAULT_REPLY_PROMPT
    assert settings.render_prompt == config.DEFAULT_RENDER_PROMPT


def test_settings_are_created_once_not_per_read(company):
    first = services.settings_for(company.id)
    second = services.settings_for(company.id)
    assert first.id == second.id
    assert AISettings.query.filter_by(company_id=company.id).count() == 1


def test_each_company_gets_its_own_settings(company, other_company):
    services.save_reply_settings(company.id, api_key="sk-ours")
    services.save_reply_settings(other_company.id, api_key="sk-theirs")
    assert services.settings_for(company.id).text_api_key == "sk-ours"
    assert services.settings_for(other_company.id).text_api_key == "sk-theirs"


# --- Keys at rest ---------------------------------------------------------

def test_keys_round_trip(company):
    services.save_reply_settings(company.id, api_key="sk-test-123456789012")
    services.save_render_settings(company.id, api_key="AIza-test-9876543210")
    settings = services.settings_for(company.id)
    assert settings.text_api_key == "sk-test-123456789012"
    assert settings.image_api_key == "AIza-test-9876543210"


def test_the_stored_column_is_ciphertext_not_the_key(company):
    """The test that actually defends the claim. A round-trip passes just
    as happily against a plaintext column."""
    services.save_reply_settings(company.id, api_key="sk-test-123456789012")
    stored = db.session.execute(
        db.text("SELECT text_api_key_encrypted FROM ai_settings WHERE company_id = :c"),
        {"c": company.id},
    ).scalar()
    assert stored
    assert "sk-test-123456789012" not in stored


def test_a_key_encrypted_under_the_ai_box_is_not_readable_by_the_mail_box(company):
    """Separate salts and separate env vars, so the two purposes can't read
    each other's secrets even on one machine."""
    from communications import crypto as comms_crypto

    services.save_reply_settings(company.id, api_key="sk-test-123456789012")
    ciphertext = services.settings_for(company.id).text_api_key_encrypted
    with pytest.raises(comms_crypto.TokenDecryptionError):
        comms_crypto.decrypt(ciphertext)


def test_a_rotated_key_fails_loudly(company, monkeypatch):
    services.save_reply_settings(company.id, api_key="sk-test-123456789012")
    settings = services.settings_for(company.id)
    monkeypatch.setenv("AI_ENCRYPTION_KEY", "cH8kV2nQ5xL9pR3tY7wA1sD4fG6hJ0kM8nB2vC5xZ1E=")
    with pytest.raises(crypto.KeyDecryptionError):
        _ = settings.text_api_key


def test_the_settings_page_still_works_after_a_key_rotation(company, monkeypatch):
    """The state the page most needs to describe. `has_text_key` reads the
    column and `text_key_hint` swallows the failure, so the page renders
    "a key is saved, and it's unreadable" instead of 500ing."""
    services.save_reply_settings(company.id, api_key="sk-test-123456789012")
    settings = services.settings_for(company.id)
    monkeypatch.setenv("AI_ENCRYPTION_KEY", "cH8kV2nQ5xL9pR3tY7wA1sD4fG6hJ0kM8nB2vC5xZ1E=")
    assert settings.has_text_key is True
    assert settings.text_key_hint is None


def test_the_hint_reveals_only_the_tail(company):
    services.save_reply_settings(company.id, api_key="sk-proj-abcdefgh4f2a")
    assert services.settings_for(company.id).text_key_hint == "…4f2a"


def test_a_short_key_reveals_nothing(company):
    """Below the length a real vendor key has, the tail would be most of
    the key."""
    services.save_reply_settings(company.id, api_key="sk-short")
    assert services.settings_for(company.id).text_key_hint == "…"


# --- "Absent means leave it alone" ----------------------------------------

def test_saving_without_a_key_keeps_the_saved_one(company):
    """Hard rule 9, in the case that matters most here: the key field is
    always rendered blank, so editing only the prompt must not wipe it."""
    services.save_reply_settings(company.id, api_key="sk-test-123456789012")
    services.save_reply_settings(company.id, prompt="Ask about hardware finish.")
    settings = services.settings_for(company.id)
    assert settings.text_api_key == "sk-test-123456789012"
    assert settings.reply_prompt == "Ask about hardware finish."


def test_saving_one_section_leaves_the_other_alone(company):
    services.save_reply_settings(company.id, api_key="sk-test-123456789012")
    services.save_render_settings(company.id, api_key="AIza-test-9876543210",
                                  prompt="Matte black hardware.")
    settings = services.settings_for(company.id)
    assert settings.text_api_key == "sk-test-123456789012"
    assert settings.reply_prompt == config.DEFAULT_REPLY_PROMPT
    assert settings.render_prompt == "Matte black hardware."


def test_a_blank_prompt_restores_the_default(company):
    services.save_reply_settings(company.id, prompt="Something short.")
    services.save_reply_settings(company.id, prompt="   ")
    assert services.settings_for(company.id).reply_prompt == config.DEFAULT_REPLY_PROMPT


def test_a_blank_model_restores_the_default(company):
    services.save_render_settings(company.id, model="")
    assert services.settings_for(company.id).image_model == config.IMAGE_MODEL


def test_deleting_a_key_keeps_the_model_and_prompt(company):
    """Deleting is how the feature is switched off. Putting a key back
    should restore the setup, not start it over."""
    services.save_reply_settings(
        company.id, api_key="sk-test-123456789012", model="gpt-4.1",
        prompt="Ask about dimensions.")
    services.clear_text_key(company.id)
    settings = services.settings_for(company.id)
    assert settings.has_text_key is False
    assert settings.text_model == "gpt-4.1"
    assert settings.reply_prompt == "Ask about dimensions."


# --- Availability is derived ----------------------------------------------

def test_availability_follows_the_key(company):
    assert services.reply_available(company.id) is False
    assert services.render_available(company.id) is False
    services.save_reply_settings(company.id, api_key="sk-test-123456789012")
    assert services.reply_available(company.id) is True
    assert services.render_available(company.id) is False
    services.clear_text_key(company.id)
    assert services.reply_available(company.id) is False


def test_a_document_is_renderable_only_when_configured_and_raster(company):
    services.save_render_settings(company.id, api_key="AIza-test-9876543210")
    assert services.can_render_document(company.id, "image/png") is True
    assert services.can_render_document(company.id, "image/jpeg") is True
    # Allowed as an upload, never sent to an image model.
    assert services.can_render_document(company.id, "image/svg+xml") is False
    assert services.can_render_document(company.id, "application/pdf") is False
    assert services.can_render_document(company.id, None) is False


def test_nothing_is_renderable_without_a_key(company):
    assert services.can_render_document(company.id, "image/png") is False


# --- The page -------------------------------------------------------------

def test_the_settings_page_renders(logged_in):
    response = logged_in.get("/settings/ai")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Inquiry replies" in body
    assert "Renderings" in body


def test_the_settings_page_never_sends_a_saved_key_to_the_browser(logged_in, user):
    services.save_reply_settings(user.company_id, api_key="sk-test-123456789012")
    db.session.commit()
    body = logged_in.get("/settings/ai").get_data(as_text=True)
    assert "sk-test-123456789012" not in body
    assert "API key saved" in body


def test_the_settings_page_requires_a_login(app):
    response = app.test_client().get("/settings/ai")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_posting_the_prompt_without_a_key_field_keeps_the_key(logged_in, user):
    """The route-level half of the "absent means leave it alone" rule: a
    form that renders no api_key input at all must not clear it."""
    services.save_reply_settings(user.company_id, api_key="sk-test-123456789012")
    db.session.commit()
    logged_in.post("/settings/ai/replies", data={"prompt": "Ask about lining."})
    settings = services.settings_for(user.company_id)
    assert settings.text_api_key == "sk-test-123456789012"
    assert settings.reply_prompt == "Ask about lining."


def test_posting_a_blank_key_field_keeps_the_key(logged_in, user):
    """And the half that actually happens in the browser: the input is
    rendered, and submitted empty."""
    services.save_reply_settings(user.company_id, api_key="sk-test-123456789012")
    db.session.commit()
    logged_in.post("/settings/ai/replies", data={"api_key": "", "prompt": "Ask about lining."})
    assert services.settings_for(user.company_id).text_api_key == "sk-test-123456789012"


def test_deleting_the_key_through_the_route(logged_in, user):
    services.save_reply_settings(user.company_id, api_key="sk-test-123456789012")
    db.session.commit()
    logged_in.post("/settings/ai/replies/key/delete")
    assert services.settings_for(user.company_id).has_text_key is False


def test_the_settings_nav_offers_email_and_ai(logged_in):
    """The rename is part of this change: "Integrations" was accurate when
    mail was the only one."""
    body = logged_in.get("/settings/ai").get_data(as_text=True)
    assert ">AI</a>" in body
    assert "Integrations" not in body


def test_one_company_cannot_read_another_through_the_page(logged_in, user, other_company):
    services.save_reply_settings(other_company.id, api_key="sk-not-yours-1234")
    db.session.commit()
    body = logged_in.get("/settings/ai").get_data(as_text=True)
    assert "sk-not-yours-1234" not in body
    assert "…1234" not in body


def test_a_company_with_no_user_is_untouched_by_another_saving(company, other_company):
    """Tenant isolation at the service level, not just the page."""
    services.save_render_settings(company.id, prompt="Ours.")
    assert services.settings_for(other_company.id).render_prompt == config.DEFAULT_RENDER_PROMPT
    assert Company.query.count() == 2
