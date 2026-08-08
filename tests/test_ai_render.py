"""
Image rendering: what's sent, what a draft is, and what saving one means.

**Nothing here reaches Google.** Two levels of double, for the same reason
`test_ai_reply.py` has two: the `image_vendor` fixture replaces
`google_image_client.generate_image` wholesale, which is right for asserting
on what the prompt contained; the failure tests patch `genai.Client` one
layer lower, because the translation from vendor exception to readable
sentence lives inside the function the first double replaces.

The regressions these were checked against:
- a draft counting against the company's document quota before anyone kept it
- the per-project details *replacing* the company prompt instead of adding
  to it
- one company reading another's drafts through the image URL
- charging for a render we already knew would be rejected (oversized source)
"""

import io

import pytest

from ai import config, google_image_client, services, storage
from ai.errors import AIError
from ai.models import RenderDraft
from documents import services as documents_service
from models import db

# A real 1x1 PNG — validation in documents/ sniffs content, so a saved
# render has to be an actual image, not b"bytes".
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c49444154789c63f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)


@pytest.fixture
def configured(company):
    services.save_render_settings(company.id, api_key="AIza-test-9876543210")
    db.session.commit()
    return company


class FakeImageVendor:
    def __init__(self, image=None, content_type="image/png", error=None):
        self.image = image if image is not None else PNG
        self.content_type = content_type
        self.error = error
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.image, self.content_type

    @property
    def instructions(self):
        return self.calls[-1]["instructions"]


@pytest.fixture
def image_vendor(monkeypatch):
    fake = FakeImageVendor()
    monkeypatch.setattr(services.google_image_client, "generate_image", fake)
    return fake


def _render(company_id, order_id=1, document_id=1, extra=""):
    return services.render_from_document(
        company_id, order_id, document_id,
        source_image=PNG, source_content_type="image/png", extra_prompt=extra)


# --- The prompt -----------------------------------------------------------

def test_the_project_details_are_added_not_substituted(configured, image_vendor):
    """`G-2`. Replacing the company prompt would silently drop every
    standing instruction — keep the proportions, don't invent hardware —
    the moment someone typed a colour into the box."""
    _render(configured.id, extra="Tan bridle leather, brass hardware.")
    sent = image_vendor.instructions
    assert config.DEFAULT_RENDER_PROMPT in sent
    assert "Tan bridle leather, brass hardware." in sent


def test_no_details_sends_the_company_prompt_alone(configured, image_vendor):
    _render(configured.id)
    assert image_vendor.instructions == config.DEFAULT_RENDER_PROMPT


def test_the_prompt_can_be_previewed_without_rendering(configured):
    """The modal shows what will be sent before anything is charged for."""
    composed = services.render_prompt_for(configured.id, "Natural edge.")
    assert composed.endswith("Natural edge.")
    assert composed.startswith(config.DEFAULT_RENDER_PROMPT)


def test_the_details_are_newline_normalised(configured, image_vendor):
    """Same CRLF trap as the prompts — this comes from a textarea too."""
    _render(configured.id, extra="One.\r\nTwo.")
    assert "One.\nTwo." in image_vendor.instructions


def test_the_saved_model_and_key_are_used(configured, image_vendor):
    services.save_render_settings(configured.id, model="gemini-3-image")
    db.session.commit()
    _render(configured.id)
    assert image_vendor.calls[-1]["model"] == "gemini-3-image"
    assert image_vendor.calls[-1]["api_key"] == "AIza-test-9876543210"


# --- A draft is not a document -------------------------------------------

def test_a_render_is_a_draft_not_a_document(configured, image_vendor):
    """`G-1`. The whole point of the draft table: a rejected attempt must
    not appear in the order's Documents area or eat the 1GB quota."""
    _render(configured.id)
    assert RenderDraft.query.count() == 1
    assert documents_service.list_for_order(1) == []
    assert documents_service.usage_for_company(configured.id) == 0


def test_a_draft_keeps_the_details_that_produced_it(configured, image_vendor):
    """What you want when the fourth attempt is worse than the second."""
    _render(configured.id, extra="Matte black hardware.")
    assert RenderDraft.query.one().extra_prompt == "Matte black hardware."


def test_drafts_accumulate_newest_first(configured, image_vendor):
    _render(configured.id, extra="first")
    _render(configured.id, extra="second")
    _render(configured.id, extra="third")
    drafts = services.drafts_for_document(configured.id, 1)
    assert [d.extra_prompt for d in drafts] == ["third", "second", "first"]


def test_a_draft_writes_its_bytes(configured, image_vendor):
    draft = _render(configured.id)
    assert services.draft_bytes(draft) == PNG
    assert draft.size_bytes == len(PNG)


def test_discarding_removes_the_row_and_the_file(configured, image_vendor):
    draft = _render(configured.id)
    stored = draft.stored_filename
    assert services.discard_draft(configured.id, draft.id) is True
    assert RenderDraft.query.count() == 0
    assert storage.path_for(configured.id, stored) is None


def test_discarding_someone_elses_draft_does_nothing(configured, other_company, image_vendor):
    draft = _render(configured.id)
    assert services.discard_draft(other_company.id, draft.id) is False
    assert RenderDraft.query.count() == 1


def test_drafts_are_tenant_scoped(configured, other_company, image_vendor):
    services.save_render_settings(other_company.id, api_key="AIza-theirs-000000")
    db.session.commit()
    _render(configured.id)
    _render(other_company.id)
    assert len(services.drafts_for_document(configured.id, 1)) == 1
    assert services.get_draft(other_company.id, RenderDraft.query.first().id) is None


# --- Expiry ---------------------------------------------------------------

def test_expired_drafts_are_pruned(configured, image_vendor):
    from datetime import timedelta

    from ai.models import _utcnow

    draft = _render(configured.id)
    stored = draft.stored_filename
    draft.created_at = _utcnow() - timedelta(hours=config.DRAFT_RETENTION_HOURS + 1)
    db.session.commit()

    assert services.prune_expired_drafts() == 1
    assert RenderDraft.query.count() == 0
    assert storage.path_for(configured.id, stored) is None


def test_fresh_drafts_survive_pruning(configured, image_vendor):
    _render(configured.id)
    assert services.prune_expired_drafts() == 0
    assert RenderDraft.query.count() == 1


def test_pruning_can_be_scoped_to_one_tenant(configured, other_company, image_vendor):
    from datetime import timedelta

    from ai.models import _utcnow

    services.save_render_settings(other_company.id, api_key="AIza-theirs-000000")
    db.session.commit()
    ours = _render(configured.id)
    theirs = _render(other_company.id)
    old = _utcnow() - timedelta(hours=config.DRAFT_RETENTION_HOURS + 1)
    ours.created_at = theirs.created_at = old
    db.session.commit()

    assert services.prune_expired_drafts(configured.id) == 1
    assert RenderDraft.query.count() == 1


# --- Refusing before spending money ---------------------------------------

def test_a_non_image_is_refused_without_calling_the_vendor(configured, image_vendor):
    with pytest.raises(AIError) as caught:
        services.render_from_document(
            configured.id, 1, 1, source_image=b"%PDF-",
            source_content_type="application/pdf")
    assert "JPEG and PNG" in str(caught.value)
    assert image_vendor.calls == []


def test_an_oversized_source_is_refused_without_calling_the_vendor(configured, image_vendor):
    """A charge for something we already know won't work is the one failure
    worth spending a check to avoid."""
    oversized = b"x" * (config.MAX_SOURCE_IMAGE_BYTES + 1)
    with pytest.raises(AIError) as caught:
        services.render_from_document(
            configured.id, 1, 1, source_image=oversized,
            source_content_type="image/png")
    assert "too large" in str(caught.value)
    assert image_vendor.calls == []


def test_no_key_is_an_error(company, image_vendor):
    with pytest.raises(AIError) as caught:
        _render(company.id)
    assert "Settings → AI" in str(caught.value)
    assert image_vendor.calls == []


def test_an_undecryptable_key_is_an_error(configured, image_vendor, monkeypatch):
    monkeypatch.setenv("AI_ENCRYPTION_KEY", "cH8kV2nQ5xL9pR3tY7wA1sD4fG6hJ0kM8nB2vC5xZ1E=")
    with pytest.raises(AIError) as caught:
        _render(configured.id)
    assert "AI_ENCRYPTION_KEY" in str(caught.value)


def test_a_failed_render_leaves_no_draft(configured, monkeypatch):
    monkeypatch.setattr(services.google_image_client, "generate_image",
                        FakeImageVendor(error=AIError("nope")))
    with pytest.raises(AIError):
        _render(configured.id)
    assert RenderDraft.query.count() == 0


# --- Vendor translation ---------------------------------------------------

def _google_error(status=None, message="x"):
    class ClientError(Exception):
        pass

    exc = ClientError(message)
    if status is not None:
        exc.code = status
    return exc


@pytest.mark.parametrize("status,expected", [
    (401, "rejected the API key"),
    (404, "model name"),
    (429, "rate-limiting"),
    (503, "overloaded"),
])
def test_each_vendor_status_gets_its_own_advice(status, expected):
    assert expected in str(google_image_client._translate(_google_error(status)))


def test_the_vendors_own_words_never_reach_the_user():
    """A vendor error can echo the request — including the key — back."""
    exc = _google_error(401, "API key not valid: AIza-test-9876543210")
    assert "AIza-test-9876543210" not in str(google_image_client._translate(exc))


def test_a_response_with_no_image_is_not_reported_as_a_breakage():
    """A refusal will be refused again, so "try again" would be wrong
    advice."""

    class Response:
        candidates = []

    assert google_image_client._first_image(Response()) is None


def test_a_response_shape_we_do_not_expect_reads_as_no_image():
    """Rather than an AttributeError the user would see as a crash."""

    class Response:
        candidates = [object()]

    assert google_image_client._first_image(Response()) is None


def test_the_first_inline_image_is_taken_past_any_text_parts():
    """The model often narrates what it did; the narration isn't the
    deliverable."""

    class Inline:
        data = PNG
        mime_type = "image/jpeg"

    text_part = type("Part", (), {"inline_data": None})()
    image_part = type("Part", (), {"inline_data": Inline()})()
    content = type("Content", (), {"parts": [text_part, image_part]})()
    response = type("Response", (), {
        "candidates": [type("C", (), {"content": content})()]})()

    assert google_image_client._first_image(response) == (PNG, "image/jpeg")


def test_a_missing_google_package_says_so(configured):
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name.startswith("google.genai") or name == "google":
            raise ImportError("No module named 'google.genai'")
        return real_import(name, *args, **kwargs)

    original = builtins.__import__
    builtins.__import__ = refuse
    try:
        with pytest.raises(AIError) as caught:
            _render(configured.id)
        assert "google-genai package isn't installed" in str(caught.value)
    finally:
        builtins.__import__ = original


# --- The routes -----------------------------------------------------------

@pytest.fixture
def stored_document(logged_in, user, order):
    """A real uploaded PNG on a real order, so the routes exercise the same
    tenant checks the app does rather than a stub."""
    result = documents_service.upload(user.company_id, order.id, [("mockup.png", PNG)])
    assert not result.errors, result.errors
    db.session.commit()
    return result.saved[0]


@pytest.fixture
def render_ready(user, stored_document):
    services.save_render_settings(user.company_id, api_key="AIza-test-9876543210")
    db.session.commit()
    return stored_document


def test_the_route_renders_and_returns_a_draft(logged_in, order, render_ready, image_vendor):
    response = logged_in.post(
        f"/orders/{order.id}/documents/{render_ready.id}/render",
        json={"extra_prompt": "Brass hardware."})
    assert response.status_code == 200
    draft = response.get_json()["draft"]
    assert draft["extra_prompt"] == "Brass hardware."
    assert draft["saved"] is False


def test_the_route_sends_the_real_document_bytes(logged_in, order, render_ready, image_vendor):
    logged_in.post(f"/orders/{order.id}/documents/{render_ready.id}/render", json={})
    assert image_vendor.calls[-1]["source_image"] == PNG
    assert image_vendor.calls[-1]["source_content_type"] == "image/png"


def test_the_image_route_serves_the_bytes(logged_in, order, render_ready, image_vendor):
    response = logged_in.post(
        f"/orders/{order.id}/documents/{render_ready.id}/render", json={})
    draft_id = response.get_json()["draft"]["id"]
    image = logged_in.get(f"/ai/renders/{draft_id}/image")
    assert image.status_code == 200
    assert image.data == PNG
    assert image.mimetype == "image/png"


def test_saving_a_draft_creates_a_document(logged_in, order, render_ready, image_vendor):
    """`G-4`. Through documents.services.upload, so validation and the
    quota apply to a vendor's image exactly as to a dragged-in one."""
    response = logged_in.post(
        f"/orders/{order.id}/documents/{render_ready.id}/render", json={})
    draft_id = response.get_json()["draft"]["id"]

    before = len(documents_service.list_for_order(order.id))
    saved = logged_in.post(f"/ai/renders/{draft_id}/save")
    assert saved.status_code == 200
    assert saved.get_json()["saved"] is True
    assert len(documents_service.list_for_order(order.id)) == before + 1


def test_a_saved_draft_reports_itself_as_saved(logged_in, order, render_ready, image_vendor):
    """So the modal can say so rather than offering to save it twice."""
    response = logged_in.post(
        f"/orders/{order.id}/documents/{render_ready.id}/render", json={})
    draft_id = response.get_json()["draft"]["id"]
    logged_in.post(f"/ai/renders/{draft_id}/save")

    history = logged_in.get(
        f"/orders/{order.id}/documents/{render_ready.id}/renders").get_json()
    assert history["drafts"][0]["saved"] is True


def test_opening_the_history_prunes_expired_drafts(logged_in, order, render_ready, image_vendor):
    """There's no scheduled job — the app's scheduler is opt-in and off by
    default, so anything hung on it would in practice never run. Opening
    the window is the moment a company's drafts are provably being looked
    at."""
    from datetime import timedelta

    from ai.models import _utcnow

    logged_in.post(f"/orders/{order.id}/documents/{render_ready.id}/render", json={})
    stale = RenderDraft.query.one()
    stored = stale.stored_filename
    stale.created_at = _utcnow() - timedelta(hours=config.DRAFT_RETENTION_HOURS + 1)
    db.session.commit()

    history = logged_in.get(
        f"/orders/{order.id}/documents/{render_ready.id}/renders").get_json()
    assert history["drafts"] == []
    assert RenderDraft.query.count() == 0
    assert storage.path_for(render_ready.company_id, stored) is None


def test_the_history_route_lists_attempts(logged_in, order, render_ready, image_vendor):
    for extra in ("one", "two"):
        logged_in.post(f"/orders/{order.id}/documents/{render_ready.id}/render",
                       json={"extra_prompt": extra})
    history = logged_in.get(
        f"/orders/{order.id}/documents/{render_ready.id}/renders").get_json()
    assert [d["extra_prompt"] for d in history["drafts"]] == ["two", "one"]


def test_discarding_through_the_route(logged_in, order, render_ready, image_vendor):
    response = logged_in.post(
        f"/orders/{order.id}/documents/{render_ready.id}/render", json={})
    draft_id = response.get_json()["draft"]["id"]
    assert logged_in.post(f"/ai/renders/{draft_id}/discard").status_code == 200
    assert RenderDraft.query.count() == 0


def test_the_route_refuses_a_document_that_is_not_this_companys(
        logged_in, order, render_ready, other_company, image_vendor):
    other_order = 999999
    response = logged_in.post(
        f"/orders/{other_order}/documents/{render_ready.id}/render", json={})
    assert response.status_code == 404
    assert image_vendor.calls == []


def test_another_companys_draft_is_not_served(logged_in, order, render_ready,
                                              other_company, image_vendor):
    services.save_render_settings(other_company.id, api_key="AIza-theirs-000000")
    db.session.commit()
    theirs = services.render_from_document(
        other_company.id, 1, 1, source_image=PNG, source_content_type="image/png")
    assert logged_in.get(f"/ai/renders/{theirs.id}/image").status_code == 404
    assert logged_in.post(f"/ai/renders/{theirs.id}/save").status_code == 404
    assert logged_in.post(f"/ai/renders/{theirs.id}/discard").status_code == 404


def test_the_routes_require_a_login(app):
    client = app.test_client()
    assert client.post("/orders/1/documents/1/render", json={}).status_code == 302
    assert client.get("/ai/renders/1/image").status_code == 302
    assert client.post("/ai/renders/1/save").status_code == 302


def test_a_vendor_failure_is_json_not_a_crash(logged_in, order, render_ready, monkeypatch):
    monkeypatch.setattr(services.google_image_client, "generate_image",
                        FakeImageVendor(error=AIError("Google is overloaded right now.")))
    response = logged_in.post(
        f"/orders/{order.id}/documents/{render_ready.id}/render", json={})
    assert response.status_code == 502
    assert "overloaded" in response.get_json()["error"]


# --- The button and the modal --------------------------------------------

def test_the_render_action_appears_on_an_image(logged_in, order, render_ready):
    body = logged_in.get(f"/orders/{order.id}").get_data(as_text=True)
    assert f'data-render-document="{render_ready.id}"' in body
    assert 'id="ai-render-modal"' in body


def test_no_render_action_without_a_key(logged_in, order, stored_document):
    body = logged_in.get(f"/orders/{order.id}").get_data(as_text=True)
    assert "data-render-document" not in body
    assert 'id="ai-render-modal"' not in body


def test_no_render_action_on_a_non_image(logged_in, user, order):
    """`A-3`. A PDF is a perfectly good document and nothing an image model
    should be handed."""
    services.save_render_settings(user.company_id, api_key="AIza-test-9876543210")
    pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
    result = documents_service.upload(user.company_id, order.id, [("sheet.pdf", pdf)])
    db.session.commit()
    assert result.saved, result.errors

    body = logged_in.get(f"/orders/{order.id}").get_data(as_text=True)
    assert f'data-render-document="{result.saved[0].id}"' not in body


def test_the_modal_is_rendered_once_not_once_per_document(logged_in, user, order, render_ready):
    """Twenty photos on an order must not mean twenty copies of the dialog
    and its script."""
    for name in ("second.png", "third.png"):
        documents_service.upload(user.company_id, order.id, [(name, PNG)])
    db.session.commit()

    body = logged_in.get(f"/orders/{order.id}").get_data(as_text=True)
    assert body.count('id="ai-render-modal"') == 1
    # `data-render-document="` matches only the buttons; the bare attribute
    # name also appears once in the modal's own querySelectorAll.
    assert body.count('data-render-document="') == 3
