"""
documents/ — validation (allowlist + content sniffing), quota enforcement,
thumbnail degradation, storage path safety, tenant isolation on the routes,
and the legacy fake-documents cleanup migration.
"""

import io
import zipfile
from datetime import date

import pytest
from PIL import Image

from models import Client, Order, db

from documents import config, services, thumbnails
from documents.models import Document
from documents.storage import path_for
from documents.validation import ValidationError, validate_upload


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="red").save(buf, format="PNG")
    return buf.getvalue()


def _docx_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
    return buf.getvalue()


# --- validation: extension allowlist + content sniffing --------------------

def test_disallowed_extension_is_rejected():
    with pytest.raises(ValidationError):
        validate_upload("virus.exe", b"whatever", current_usage_bytes=0)


def test_accepted_png_passes_and_is_sniffed():
    content_type = validate_upload("pattern.png", _png_bytes(), current_usage_bytes=0)
    assert content_type == "image/png"


def test_renamed_file_fails_the_content_sniff():
    """A .pdf extension on a file that isn't a PDF — the sniff catches what
    the extension alone wouldn't."""
    with pytest.raises(ValidationError):
        validate_upload("fake.pdf", b"just plain text, not a pdf", current_usage_bytes=0)


def test_valid_pdf_header_passes():
    assert validate_upload("order.pdf", b"%PDF-1.4\n%rest", current_usage_bytes=0) == "application/pdf"


def test_pdf_compatible_ai_is_recognized_as_pdf():
    """Illustrator's default "Create PDF Compatible File" — treated the
    same as a real PDF for validation/thumbnailing purposes."""
    assert validate_upload("pattern.ai", b"%PDF-1.4\n%rest", current_usage_bytes=0) == "application/pdf"


def test_postscript_only_ai_is_recognized_but_not_as_pdf():
    content_type = validate_upload("pattern.ai", b"%!PS-Adobe-3.0\n%rest", current_usage_bytes=0)
    assert content_type == "application/postscript"


def test_valid_eps_passes():
    assert validate_upload("art.eps", b"%!PS-Adobe-3.0 EPSF-3.0\n", current_usage_bytes=0) == "application/postscript"


def test_valid_svg_passes():
    svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>'
    assert validate_upload("logo.svg", svg, current_usage_bytes=0) == "image/svg+xml"


def test_svg_without_svg_root_is_rejected():
    with pytest.raises(ValidationError):
        validate_upload("logo.svg", b"<?xml version=\"1.0\"?><not-svg/>", current_usage_bytes=0)


def test_valid_docx_passes():
    content_type = validate_upload("order-sheet.docx", _docx_bytes(), current_usage_bytes=0)
    assert "wordprocessingml" in content_type


def test_zip_without_content_types_is_not_a_valid_docx():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("readme.txt", "not office xml")
    with pytest.raises(ValidationError):
        validate_upload("sheet.docx", buf.getvalue(), current_usage_bytes=0)


# --- size + quota ------------------------------------------------------

def test_oversized_file_is_rejected(monkeypatch):
    monkeypatch.setattr(config, "MAX_FILE_BYTES", 10)
    with pytest.raises(ValidationError):
        validate_upload("pattern.png", _png_bytes(), current_usage_bytes=0)


def test_over_quota_file_is_rejected(monkeypatch):
    monkeypatch.setattr(config, "MAX_TOTAL_BYTES", 100)
    with pytest.raises(ValidationError):
        validate_upload("pattern.png", _png_bytes(), current_usage_bytes=90)


def test_under_quota_file_is_accepted(monkeypatch):
    monkeypatch.setattr(config, "MAX_TOTAL_BYTES", 10_000)
    validate_upload("pattern.png", _png_bytes(), current_usage_bytes=0)  # doesn't raise


# --- thumbnails: best-effort, never raise -----------------------------

def test_image_thumbnail_is_generated():
    thumb = thumbnails.generate("image/png", _png_bytes())
    assert thumb is not None
    Image.open(io.BytesIO(thumb)).verify()  # decodable


def test_pdf_thumbnail_degrades_gracefully_without_poppler(monkeypatch):
    monkeypatch.setattr(thumbnails, "_POPPLER_AVAILABLE", False)
    assert thumbnails.generate("application/pdf", b"%PDF-1.4\n%rest") is None


def test_unsupported_type_has_no_thumbnail():
    assert thumbnails.generate("application/postscript", b"%!PS-Adobe-3.0") is None


# --- storage: path containment ------------------------------------------

def test_path_for_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DOCUMENT_DIR", str(tmp_path))
    assert path_for(1, "../../etc/passwd") is None


def test_path_for_unknown_file_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DOCUMENT_DIR", str(tmp_path))
    assert path_for(1, "never-saved.png") is None


# --- services: end-to-end upload/usage/delete ---------------------------

def test_service_upload_creates_a_document_and_stores_the_file(company, order):
    result = services.upload(company.id, order.id, [("pattern.png", _png_bytes())])
    assert result.errors == []
    assert len(result.saved) == 1
    doc = result.saved[0]
    assert doc.company_id == company.id
    assert doc.order_id == order.id
    assert doc.thumbnail_filename is not None  # PNG always thumbnails
    assert path_for(company.id, doc.stored_filename) is not None


def test_service_upload_reports_a_rejected_file_without_raising(company, order):
    result = services.upload(company.id, order.id, [("virus.exe", b"noop")])
    assert result.saved == []
    assert len(result.errors) == 1


def test_partial_success_across_a_multi_file_batch(company, order):
    files = [("good.png", _png_bytes()), ("bad.exe", b"noop")]
    result = services.upload(company.id, order.id, files)
    assert len(result.saved) == 1
    assert len(result.errors) == 1


def test_usage_for_company_sums_stored_sizes(company, order):
    services.upload(company.id, order.id, [("a.png", _png_bytes())])
    assert services.usage_for_company(company.id) == len(_png_bytes())


def test_usage_is_scoped_to_the_tenant(company, other_company, order):
    services.upload(company.id, order.id, [("a.png", _png_bytes())])
    assert services.usage_for_company(other_company.id) == 0


def test_delete_removes_the_row_and_the_file_on_disk(company, order):
    result = services.upload(company.id, order.id, [("a.png", _png_bytes())])
    doc = result.saved[0]
    stored_filename = doc.stored_filename

    services.delete(doc)

    assert db.session.get(Document, doc.id) is None
    assert path_for(company.id, stored_filename) is None


# --- routes: auth, tenant isolation, the actual HTTP flow ----------------

def test_upload_route_requires_login(app, order):
    response = app.test_client().post(f"/orders/{order.id}/documents/upload", data={})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_upload_download_view_delete_roundtrip(logged_in, order):
    data = {"files": (io.BytesIO(_png_bytes()), "pattern.png")}
    response = logged_in.post(
        f"/orders/{order.id}/documents/upload", data=data,
        content_type="multipart/form-data", follow_redirects=True,
    )
    assert response.status_code == 200
    doc = Document.query.filter_by(order_id=order.id).first()
    assert doc is not None
    assert doc.original_filename == "pattern.png"

    download = logged_in.get(f"/orders/{order.id}/documents/{doc.id}/download")
    assert download.status_code == 200
    assert download.headers["Content-Disposition"].startswith("attachment")
    assert download.mimetype == "application/octet-stream"

    view = logged_in.get(f"/orders/{order.id}/documents/{doc.id}/view")
    assert view.status_code == 200
    assert view.mimetype == "image/png"

    delete = logged_in.post(
        f"/orders/{order.id}/documents/{doc.id}/delete", follow_redirects=True
    )
    assert delete.status_code == 200
    assert db.session.get(Document, doc.id) is None


def test_view_of_a_non_previewable_type_redirects_to_download(logged_in, order):
    data = {"files": (io.BytesIO(_docx_bytes()), "sheet.docx")}
    logged_in.post(
        f"/orders/{order.id}/documents/upload", data=data,
        content_type="multipart/form-data",
    )
    doc = Document.query.filter_by(order_id=order.id).first()

    response = logged_in.get(f"/orders/{order.id}/documents/{doc.id}/view")
    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        f"/orders/{order.id}/documents/{doc.id}/download"
    )


def test_upload_rejection_message_is_shown_not_a_500(logged_in, order):
    data = {"files": (io.BytesIO(b"noop"), "virus.exe")}
    response = logged_in.post(
        f"/orders/{order.id}/documents/upload", data=data,
        content_type="multipart/form-data", follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"allowed" in response.data
    assert Document.query.filter_by(order_id=order.id).count() == 0


def _order_for(company_id: int) -> Order:
    outsider_client = Client(company_id=company_id, first_name="X", last_name="Y")
    db.session.add(outsider_client)
    db.session.flush()
    outsider_order = Order(
        client_id=outsider_client.id, item="Outsider's item",
        start=date(2026, 1, 1), due=date(2026, 1, 10), status="in_progress",
    )
    db.session.add(outsider_order)
    db.session.flush()
    return outsider_order


def test_download_of_another_tenants_document_404s(logged_in, other_company):
    outsider_order = _order_for(other_company.id)
    result = services.upload(other_company.id, outsider_order.id, [("a.png", _png_bytes())])
    doc = result.saved[0]

    response = logged_in.get(f"/orders/{outsider_order.id}/documents/{doc.id}/download")
    assert response.status_code == 404


def test_delete_of_another_tenants_document_404s(logged_in, other_company):
    outsider_order = _order_for(other_company.id)
    result = services.upload(other_company.id, outsider_order.id, [("a.png", _png_bytes())])
    doc = result.saved[0]

    response = logged_in.post(f"/orders/{outsider_order.id}/documents/{doc.id}/delete")
    assert response.status_code == 404
    assert db.session.get(Document, doc.id) is not None  # untouched


# --- migration: legacy fake `documents` table is dropped -----------------

def test_legacy_documents_table_is_dropped(app):
    import sqlalchemy as sa

    from documents import migrations as documents_migrations

    with app.app_context():
        db.session.execute(sa.text(
            "CREATE TABLE documents (id INTEGER PRIMARY KEY, order_id INTEGER, "
            "label TEXT, filename TEXT)"
        ))
        db.session.commit()

        documents_migrations.run_migrations()

        inspector = sa.inspect(db.engine)
        assert "documents" not in inspector.get_table_names()
