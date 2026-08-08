"""
The one-time refresh of a stored prompt that's still a superseded default.

The rule is deliberately narrow: a prompt is rewritten **only** when it
matches a previous default, and a company that changed so much as a word
keeps exactly what it wrote. Getting that wrong destroys someone's work
silently, so it's the thing most of these tests are about.

Why it exists at all: without it the only way off an old default is to
blank the field, and a company that never knew the default had changed
would stay on it forever — in this case still saying "we" for a one-person
atelier.
"""

from ai import config, migrations, services
from ai.models import AISettings
from models import db


def _prompt(company_id: int) -> str:
    return AISettings.query.filter_by(company_id=company_id).one().reply_prompt


def _set_raw(company_id: int, text: str) -> None:
    """Write the column directly, bypassing the service's normalisation —
    the point is to reproduce rows that predate it."""
    services.settings_for(company_id)
    db.session.execute(
        db.text("UPDATE ai_settings SET reply_prompt = :p WHERE company_id = :c"),
        {"p": text, "c": company_id},
    )
    db.session.commit()


def test_a_superseded_default_is_moved_forward(company):
    _set_raw(company.id, config.SUPERSEDED_REPLY_PROMPTS[0])
    migrations.run_migrations()
    assert _prompt(company.id) == config.DEFAULT_REPLY_PROMPT


def test_an_edited_prompt_is_never_touched(company):
    """The one that must not regress. This is somebody's writing."""
    _set_raw(company.id, "Ask about hardware finish and nothing else.")
    migrations.run_migrations()
    assert _prompt(company.id) == "Ask about hardware finish and nothing else."


def test_a_nearly_identical_prompt_is_never_touched(company):
    """"Unedited" means byte-for-byte. One changed word is an edit."""
    edited = config.SUPERSEDED_REPLY_PROMPTS[0].replace("warm", "brisk")
    _set_raw(company.id, edited)
    migrations.run_migrations()
    assert _prompt(company.id) == edited


def test_the_crlf_copy_a_browser_saved_is_recognised(company):
    """The reason this compares normalised text. Every row that has been
    through the settings form holds CRLF, so a raw equality check would
    miss precisely the rows that need moving — which is what it did against
    the real database before this was fixed."""
    _set_raw(company.id, config.SUPERSEDED_REPLY_PROMPTS[0].replace("\n", "\r\n"))
    migrations.run_migrations()
    assert _prompt(company.id) == config.DEFAULT_REPLY_PROMPT


def test_the_current_default_is_left_alone(company):
    services.settings_for(company.id)
    migrations.run_migrations()
    assert _prompt(company.id) == config.DEFAULT_REPLY_PROMPT


def test_it_is_idempotent(company):
    _set_raw(company.id, config.SUPERSEDED_REPLY_PROMPTS[0])
    migrations.run_migrations()
    once = _prompt(company.id)
    migrations.run_migrations()
    assert _prompt(company.id) == once


def test_it_is_tenant_wide_not_tenant_scoped(company, other_company):
    """A boot-time migration has no signed-in user, so it deliberately runs
    across every tenant — the one place in this module a query isn't scoped
    by company_id, same exemption communications' scheduled sync has."""
    _set_raw(company.id, config.SUPERSEDED_REPLY_PROMPTS[0])
    _set_raw(other_company.id, config.SUPERSEDED_REPLY_PROMPTS[0])
    migrations.run_migrations()
    assert _prompt(company.id) == config.DEFAULT_REPLY_PROMPT
    assert _prompt(other_company.id) == config.DEFAULT_REPLY_PROMPT


def test_one_tenants_edit_survives_anothers_refresh(company, other_company):
    _set_raw(company.id, config.SUPERSEDED_REPLY_PROMPTS[0])
    _set_raw(other_company.id, "Mine, thanks.")
    migrations.run_migrations()
    assert _prompt(company.id) == config.DEFAULT_REPLY_PROMPT
    assert _prompt(other_company.id) == "Mine, thanks."


def test_it_survives_an_empty_table(app):
    migrations.run_migrations()  # no rows at all


def test_every_superseded_prompt_differs_from_the_current_one():
    """A superseded entry equal to the current default would make the
    migration rewrite rows to what they already say — harmless, but it
    means someone pasted the wrong text into the list."""
    assert config.DEFAULT_REPLY_PROMPT not in config.SUPERSEDED_REPLY_PROMPTS
