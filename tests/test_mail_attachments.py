"""
Attaching an order document to an outgoing email.

Three layers, and the seam between them is the point: `documents/` owns the
bytes, `communications/` owns the sending, and neither knows the other
exists — `app.py` holds both hooks (`_attachable_documents`,
`_load_attachable_documents`) and is the only place an Order and a Document
and an EmailMessage are in the same room.
"""

import io

import pytest
from PIL import Image

from models import Client, Order, db

from communications.models import AUDIT_EMAIL_SENT, AuditLog
from communications.providers.base import OutgoingAttachment
from communications.services import email_service

from documents import config as documents_config
from documents import services as documents_service

from tests import fakes

import app as app_module


@pytest.fixture(autouse=True)
def document_dir(tmp_path, monkeypatch):
    """Keep uploaded bytes out of the repo's real data/ directory."""
    monkeypatch.setattr(documents_config, "DOCUMENT_DIR", str(tmp_path))


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="red").save(buf, format="PNG")
    return buf.getvalue()


def _upload(company, order, filename="pattern.png"):
    result = documents_service.upload(company.id, order.id, [(filename, _png_bytes())])
    assert result.errors == []
    return result.saved[0]


# --- the picker: what's offered ------------------------------------------

def test_picker_lists_an_orders_documents(company, client_record, order):
    document = _upload(company, order)

    payload = app_module._attachable_documents(company.id, client_record.id)

    assert len(payload["orders"]) == 1
    row = payload["orders"][0]
    assert row["id"] == order.id
    assert order.item in row["label"]
    assert [d["id"] for d in row["documents"]] == [document.id]
    assert row["documents"][0]["filename"] == "pattern.png"


def test_documents_carry_their_type_for_the_dropdown_groups(company, client_record, order):
    """The picker groups by the same document types the order page files
    them under, so a studio that's set up Mockups/Renderings sees its own
    sections here too."""
    document_type = documents_service.add_document_type(company.id, "Mockups")
    documents_service.upload(
        company.id, order.id, [("mockup.png", _png_bytes())],
        document_type_id=document_type.id,
    )
    _upload(company, order, "pattern.png")

    documents = app_module._attachable_documents(
        company.id, client_record.id)["orders"][0]["documents"]

    by_name = {d["filename"]: d["type"] for d in documents}
    assert by_name["mockup.png"] == "Mockups"
    # Untyped reads "Other" — what the order page's own trailing section
    # calls it — rather than blank.
    assert by_name["pattern.png"] == "Other"


def test_an_order_with_no_documents_is_not_offered(company, client_record, order):
    """Picking an order only to find an empty second dropdown is a dead
    end — but `has_orders` still separates "nothing uploaded yet" from
    "no orders at all", which the modal says differently."""
    payload = app_module._attachable_documents(company.id, client_record.id)
    assert payload == {"orders": [], "has_orders": True}


def test_a_client_with_no_orders_at_all(company, client_record):
    payload = app_module._attachable_documents(company.id, client_record.id)
    assert payload == {"orders": [], "has_orders": False}


def test_picker_does_not_cross_tenants(company, other_company, client_record, order):
    _upload(company, order)
    payload = app_module._attachable_documents(other_company.id, client_record.id)
    assert payload == {"orders": [], "has_orders": False}


def test_picker_does_not_offer_another_clients_order(company, client_record, order):
    _upload(company, order)
    other = Client(company_id=company.id, first_name="Luc", last_name="Roy")
    db.session.add(other)
    db.session.flush()

    assert app_module._attachable_documents(company.id, other.id)["orders"] == []


def test_picker_route_returns_json(logged_in, company, user, client_record, order):
    _upload(company, order)

    response = logged_in.get(f"/mail/attachable-documents/{client_record.id}")

    assert response.status_code == 200
    assert response.get_json()["orders"][0]["id"] == order.id


# --- the compose form --------------------------------------------------

def test_the_compose_form_offers_the_picker(logged_in, user, account, client_record):
    """Offered whether or not anything is attachable — what's there to
    attach is answered inside the modal, which can tell "no orders yet"
    from "no documents on them" (a missing button can't)."""
    page = logged_in.get(f"/clients/{client_record.id}/emails").get_data(as_text=True)

    assert 'id="doc-attach-btn"' in page
    assert 'id="doc-attach-modal"' in page
    assert f"/mail/attachable-documents/{client_record.id}" in page


def test_a_lead_thread_has_no_picker(logged_in, user, account, lead_thread):
    """A lead isn't a client yet, so "documents for this client" has no
    meaning — same reason the AI suggestion button gates on a thread."""
    page = logged_in.get(f"/mail/threads/{lead_thread.id}").get_data(as_text=True)

    assert 'id="doc-attach-btn"' not in page


# --- the picker: resolving ids back into bytes ---------------------------

def test_load_returns_the_stored_bytes(company, order):
    document = _upload(company, order)

    attachments = app_module._load_attachable_documents(company.id, [document.id])

    assert len(attachments) == 1
    assert attachments[0].filename == "pattern.png"
    assert attachments[0].content_type == "image/png"
    assert attachments[0].data == _png_bytes()


def test_load_drops_an_id_from_another_company(company, other_company, order):
    """The ids arrive in a hidden form field, so they're a request, not a
    fact — every one is re-resolved against the sending company."""
    document = _upload(company, order)
    assert app_module._load_attachable_documents(other_company.id, [document.id]) == []


def test_load_drops_a_document_whose_file_is_gone(company, order, tmp_path):
    """A row with no file behind it shouldn't cost the user the message
    they just typed."""
    document = _upload(company, order)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            path.unlink()

    assert app_module._load_attachable_documents(company.id, [document.id]) == []


# --- sending -------------------------------------------------------------

def test_attachments_reach_the_provider(company, account, client_record, order):
    document = _upload(company, order)
    attachments = app_module._load_attachable_documents(company.id, [document.id])

    with fakes.fake_providers():
        email_service.send_email(
            company.id, to="marie@example.com", subject="Your pattern",
            body_text="Attached.", attachments=attachments,
        )

    sent = fakes.SENT_LOG[-1]["attachments"]
    assert [a.filename for a in sent] == ["pattern.png"]
    assert sent[0].data == _png_bytes()


def test_the_audit_line_names_what_went_out(company, account, client_record, order):
    document = _upload(company, order)

    with fakes.fake_providers():
        email_service.send_email(
            company.id, to="marie@example.com", subject="Your pattern",
            body_text="Attached.",
            attachments=app_module._load_attachable_documents(company.id, [document.id]),
        )

    entry = AuditLog.query.filter_by(event=AUDIT_EMAIL_SENT).one()
    assert "pattern.png" in entry.detail


def test_an_oversized_batch_is_refused_before_the_provider_is_called(company, account):
    """Refused here rather than as an opaque provider 400 after the whole
    message has been uploaded."""
    huge = OutgoingAttachment(
        filename="hides.png", content_type="image/png",
        data=b"x" * (20 * 1024 * 1024 + 1),
    )

    with fakes.fake_providers():
        with pytest.raises(email_service.EmailServiceError, match="over the 20MB limit"):
            email_service.send_email(
                company.id, to="marie@example.com", subject="Hi",
                body_text="Hello", attachments=[huge],
            )

    assert not fakes.SENT_LOG


def test_sending_nothing_attached_still_works(company, account):
    with fakes.fake_providers():
        email_service.send_email(
            company.id, to="marie@example.com", subject="Hi", body_text="Hello",
        )
    assert fakes.SENT_LOG[-1]["attachments"] == []


# --- the send route ------------------------------------------------------

def test_send_route_attaches_the_picked_document(
    logged_in, csrf, company, user, account, client_record, order,
):
    document = _upload(company, order)

    with fakes.fake_providers():
        response = logged_in.post("/mail/send", data={
            "csrf_token": csrf,
            "to": "marie@example.com",
            "subject": "Your pattern",
            "body_text": "Attached.",
            "client_id": str(client_record.id),
            "document_id": str(document.id),
            "return_to": "/clients",
        })

    assert response.status_code == 302
    assert [a.filename for a in fakes.SENT_LOG[-1]["attachments"]] == ["pattern.png"]


def test_send_route_ignores_a_document_id_from_another_tenant(
    logged_in, csrf, company, other_company, user, account, client_record, order,
):
    """A rewritten hidden field can't attach someone else's file — and
    doesn't stop the message going out either."""
    other_client = Client(company_id=other_company.id, first_name="X", last_name="Y")
    db.session.add(other_client)
    db.session.flush()
    from datetime import date
    other_order = Order(
        client_id=other_client.id, item="Wallet", start=date(2026, 7, 1),
        due=date(2026, 7, 15), status="confirmed",
    )
    db.session.add(other_order)
    db.session.flush()
    theirs = _upload(other_company, other_order)

    with fakes.fake_providers():
        logged_in.post("/mail/send", data={
            "csrf_token": csrf,
            "to": "marie@example.com",
            "subject": "Your pattern",
            "body_text": "Attached.",
            "document_id": str(theirs.id),
            "return_to": "/clients",
        })

    assert fakes.SENT_LOG[-1]["attachments"] == []
