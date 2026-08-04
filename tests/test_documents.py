"""
documents/ — validation (allowlist + content sniffing), quota enforcement,
thumbnail degradation, storage path safety, tenant isolation on the routes,
and the legacy fake-documents cleanup migration.
"""

import io
import json
import zipfile
from datetime import date

import pytest
from PIL import Image

from models import Client, Order, db

from documents import config, services, thumbnails
from documents.models import Document, DocumentType
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


# --- the remaining rejection branches ----------------------------------
#
# The happy path and a couple of rejections above are covered; these are
# the per-type "the bytes don't match the extension" branches that weren't.

def test_renamed_nonimage_png_fails_the_image_sniff():
    """A .png whose bytes PIL can't open — the image branch, distinct from
    the .pdf sniff already tested above."""
    with pytest.raises(ValidationError):
        validate_upload("fake.png", b"not really a png", current_usage_bytes=0)


def test_eps_without_a_postscript_header_is_rejected():
    with pytest.raises(ValidationError):
        validate_upload("art.eps", b"%PDF-1.4\nnot postscript", current_usage_bytes=0)


def test_svg_that_is_not_valid_utf8_is_rejected():
    """An SVG must decode as UTF-8 before we even look for its root — the
    UnicodeDecodeError branch, separate from the has-no-<svg> one."""
    with pytest.raises(ValidationError):
        validate_upload("logo.svg", b"\xff\xfe<svg>", current_usage_bytes=0)


def test_non_zip_docx_is_rejected():
    """Bytes that aren't a Zip archive at all — the BadZipFile branch, as
    opposed to the valid-zip-missing-Content_Types case above."""
    with pytest.raises(ValidationError):
        validate_upload("sheet.docx", b"plainly not a zip", current_usage_bytes=0)


def test_an_allowed_extension_with_no_sniff_branch_is_rejected(monkeypatch):
    """Defensive: if the allowlist ever gains an extension nobody wrote a
    content check for, the sniff must refuse it rather than wave it through.
    Reproduced by adding .txt to the allowlist without a matching branch."""
    monkeypatch.setattr(config, "ALLOWED_EXTENSIONS", config.ALLOWED_EXTENSIONS | {".txt"})

    with pytest.raises(ValidationError, match="isn't an allowed file type"):
        validate_upload("notes.txt", b"anything", current_usage_bytes=0)


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


# --- document types: add / hide-don't-delete / reorder -------------------

def test_add_document_type_assigns_next_sort_order(company):
    a = services.add_document_type(company.id, "Mockups")
    b = services.add_document_type(company.id, "Renderings")
    assert a.sort_order == 0
    assert b.sort_order == 1


def test_add_document_type_rejects_a_blank_label(company):
    assert services.add_document_type(company.id, "   ") is None


def test_add_document_type_rejects_an_exact_duplicate(company):
    services.add_document_type(company.id, "Mockups")
    assert services.add_document_type(company.id, "Mockups") is None
    assert DocumentType.query.filter_by(company_id=company.id).count() == 1


def test_add_document_type_rejects_a_case_insensitive_duplicate(company):
    services.add_document_type(company.id, "Mockups")
    assert services.add_document_type(company.id, "  mockups  ") is None
    assert DocumentType.query.filter_by(company_id=company.id).count() == 1


def test_add_document_type_rejects_a_duplicate_of_a_hidden_type(company):
    document_type = services.add_document_type(company.id, "Mockups")
    services.toggle_document_type(company.id, document_type.id)  # hide
    assert services.add_document_type(company.id, "Mockups") is None


def test_add_document_type_allows_the_same_label_in_another_company(company, other_company):
    services.add_document_type(company.id, "Mockups")
    assert services.add_document_type(other_company.id, "Mockups") is not None


def test_toggle_document_type_flips_is_active(company):
    document_type = services.add_document_type(company.id, "Mockups")
    services.toggle_document_type(company.id, document_type.id)
    assert db.session.get(DocumentType, document_type.id).is_active is False
    services.toggle_document_type(company.id, document_type.id)
    assert db.session.get(DocumentType, document_type.id).is_active is True


def test_toggle_document_type_is_scoped_to_the_tenant(company, other_company):
    document_type = services.add_document_type(company.id, "Mockups")
    services.toggle_document_type(other_company.id, document_type.id)
    assert db.session.get(DocumentType, document_type.id).is_active is True  # untouched


def test_delete_document_type_removes_it_when_unused(company):
    document_type = services.add_document_type(company.id, "Mockups")
    services.delete_document_type(company.id, document_type.id)
    assert db.session.get(DocumentType, document_type.id) is None


def test_delete_document_type_is_blocked_once_referenced(company, order):
    document_type = services.add_document_type(company.id, "Mockups")
    services.upload(
        company.id, order.id, [("a.png", _png_bytes())],
        document_type_id=document_type.id,
    )
    services.delete_document_type(company.id, document_type.id)
    assert db.session.get(DocumentType, document_type.id) is not None
    assert db.session.get(DocumentType, document_type.id).can_delete is False


def test_reorder_document_types_sets_sort_order_from_position(company):
    a = services.add_document_type(company.id, "A")
    b = services.add_document_type(company.id, "B")
    c = services.add_document_type(company.id, "C")

    services.reorder_document_types(company.id, [c.id, a.id, b.id])

    assert [t.label for t in services.list_document_types(company.id)] == ["C", "A", "B"]


def test_reorder_ignores_ids_from_another_tenant(company, other_company):
    mine = services.add_document_type(company.id, "Mine")
    theirs = services.add_document_type(other_company.id, "Theirs")

    services.reorder_document_types(company.id, [theirs.id, mine.id])

    assert db.session.get(DocumentType, theirs.id).sort_order == 0  # untouched


# --- sections_for_order: the merge/order logic behind the order page -----

def test_no_types_configured_means_no_sectioning(company, order):
    assert services.has_document_types(company.id) is False


def test_sections_follow_sort_order_and_are_uploadable(company, order):
    mockups = services.add_document_type(company.id, "Mockups")
    renderings = services.add_document_type(company.id, "Renderings")
    services.upload(company.id, order.id, [("m.png", _png_bytes())], document_type_id=mockups.id)
    services.upload(company.id, order.id, [("r.png", _png_bytes())], document_type_id=renderings.id)

    sections = services.sections_for_order(order.id, company.id)

    # "Other" is always appended last, on top of the two configured types.
    assert [s.label for s in sections] == ["Mockups", "Renderings", "Other"]
    assert all(s.can_upload for s in sections)
    assert [d.original_filename for d in sections[0].documents] == ["m.png"]


def test_an_empty_active_type_still_gets_a_section(company, order):
    services.add_document_type(company.id, "Mockups")
    sections = services.sections_for_order(order.id, company.id)
    assert [s.label for s in sections] == ["Mockups", "Other"]
    assert sections[0].documents == []
    assert sections[0].can_upload is True


def test_hidden_type_keeps_its_section_if_referenced_but_not_uploadable(company, order):
    document_type = services.add_document_type(company.id, "Mockups")
    services.upload(
        company.id, order.id, [("m.png", _png_bytes())],
        document_type_id=document_type.id,
    )
    services.toggle_document_type(company.id, document_type.id)  # hide

    sections = services.sections_for_order(order.id, company.id)

    assert [s.label for s in sections] == ["Mockups", "Other"]
    assert sections[0].can_upload is False


def test_hidden_unreferenced_type_has_no_section(company, order):
    document_type = services.add_document_type(company.id, "Mockups")
    services.toggle_document_type(company.id, document_type.id)  # hidden, never used
    sections = services.sections_for_order(order.id, company.id)
    # Only the permanent "Other" bucket remains — the hidden, unused type
    # doesn't get a section of its own.
    assert [s.label for s in sections] == ["Other"]


def test_other_bucket_is_always_present_and_uploadable_once_types_exist(company, order):
    services.add_document_type(company.id, "Mockups")

    sections = services.sections_for_order(order.id, company.id)
    other = [s for s in sections if s.label == "Other"]
    assert len(other) == 1
    assert other[0].can_upload is True
    assert other[0].document_type is None
    assert other[0].documents == []

    services.upload(company.id, order.id, [("legacy.png", _png_bytes())])  # no type

    sections = services.sections_for_order(order.id, company.id)
    other = next(s for s in sections if s.label == "Other")
    assert [d.original_filename for d in other.documents] == ["legacy.png"]


def test_uploading_into_other_keeps_the_document_untyped(company, order):
    services.add_document_type(company.id, "Mockups")
    result = services.upload(company.id, order.id, [("a.png", _png_bytes())], document_type_id=None)
    assert result.saved[0].document_type_id is None


def test_other_bucket_is_always_last(company, order):
    document_type = services.add_document_type(company.id, "Mockups")
    services.upload(company.id, order.id, [("legacy.png", _png_bytes())])
    services.upload(
        company.id, order.id, [("m.png", _png_bytes())],
        document_type_id=document_type.id,
    )

    sections = services.sections_for_order(order.id, company.id)

    assert sections[-1].label == "Other"


# --- upload() with a document_type_id -------------------------------------

def test_upload_stores_the_given_document_type(company, order):
    document_type = services.add_document_type(company.id, "Mockups")
    result = services.upload(
        company.id, order.id, [("a.png", _png_bytes())],
        document_type_id=document_type.id,
    )
    assert result.saved[0].document_type_id == document_type.id


def test_upload_with_a_foreign_document_type_id_falls_back_to_none(company, order, other_company):
    theirs = services.add_document_type(other_company.id, "Theirs")
    result = services.upload(
        company.id, order.id, [("a.png", _png_bytes())], document_type_id=theirs.id,
    )
    assert result.saved[0].document_type_id is None


def test_upload_with_a_bogus_document_type_id_falls_back_to_none(company, order):
    result = services.upload(
        company.id, order.id, [("a.png", _png_bytes())], document_type_id=999999,
    )
    assert result.saved[0].document_type_id is None


# --- routes: settings-level document type management ----------------------

def test_add_type_route_creates_a_type(logged_in, company):
    response = logged_in.post(
        "/settings/document-types", data={"label": "Mockups"}, follow_redirects=True,
    )
    assert response.status_code == 200
    assert DocumentType.query.filter_by(company_id=company.id, label="Mockups").first() is not None


def test_add_type_route_rejects_a_duplicate_and_flashes_a_message(logged_in, company):
    logged_in.post("/settings/document-types", data={"label": "Mockups"})

    response = logged_in.post(
        "/settings/document-types", data={"label": "Mockups"}, follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"already exists" in response.data
    assert DocumentType.query.filter_by(company_id=company.id, label="Mockups").count() == 1


def test_reorder_route_persists_the_new_order(logged_in, company):
    a = services.add_document_type(company.id, "A")
    b = services.add_document_type(company.id, "B")

    response = logged_in.post(
        "/settings/document-types/reorder",
        data=json.dumps({"order": [b.id, a.id]}),
        content_type="application/json",
    )

    assert response.status_code == 204
    assert [t.label for t in services.list_document_types(company.id)] == ["B", "A"]


@pytest.mark.parametrize("path", [
    "/settings/document-types",
    "/settings/document-types/1/toggle",
    "/settings/document-types/1/delete",
])
def test_document_type_management_routes_require_login(app, path):
    response = app.test_client().post(path, data={})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_reorder_route_requires_login(app, company):
    document_type = services.add_document_type(company.id, "A")
    response = app.test_client().post(
        "/settings/document-types/reorder",
        data=json.dumps({"order": [document_type.id]}),
        content_type="application/json",
    )
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_upload_route_accepts_a_document_type_id(logged_in, company, order):
    document_type = services.add_document_type(company.id, "Mockups")
    data = {
        "files": (io.BytesIO(_png_bytes()), "a.png"),
        "document_type_id": str(document_type.id),
    }
    logged_in.post(
        f"/orders/{order.id}/documents/upload", data=data,
        content_type="multipart/form-data",
    )
    doc = Document.query.filter_by(order_id=order.id).first()
    assert doc.document_type_id == document_type.id


# --- migrations ------------------------------------------------------------

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


def test_migration_adds_document_type_id_to_existing_installs(app):
    import sqlalchemy as sa

    from documents import migrations as documents_migrations

    with app.app_context():
        # SQLite refuses to DROP COLUMN a column involved in a foreign key
        # (even one it owns, like this one referencing document_types), so
        # the pre-migration shape is rebuilt from scratch instead — same
        # approach as the legacy `documents` table test above.
        db.session.execute(sa.text("DROP TABLE order_documents"))
        db.session.execute(sa.text(
            "CREATE TABLE order_documents ("
            "id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, "
            "order_id INTEGER NOT NULL, original_filename VARCHAR(255) NOT NULL, "
            "stored_filename VARCHAR(64) NOT NULL, thumbnail_filename VARCHAR(64), "
            "content_type VARCHAR(100) NOT NULL, size_bytes INTEGER NOT NULL, "
            "uploaded_at DATETIME NOT NULL)"
        ))
        db.session.commit()

        documents_migrations.run_migrations()

        inspector = sa.inspect(db.engine)
        columns = {c["name"] for c in inspector.get_columns("order_documents")}
        assert "document_type_id" in columns
