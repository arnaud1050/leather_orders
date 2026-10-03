"""
The invoice as a server-rendered PDF (`billing/pdf.py`).

Split the way the module is: the HTML half runs anywhere, so most rules are
pinned against it; the half that needs WeasyPrint (and Pango under it) is
skipped where that can't load — which includes a stock Windows machine —
and runs for real in the Docker image.

The route tests replace the renderer rather than depend on it, so the
tenant boundary and the fallback are checked on every machine.
"""

from dataclasses import replace
from datetime import date

import pytest

from billing import config, pdf
from billing.documents import (
    Branding, InvoiceDocument, IssuerDetails, LineItem, PartyDetails,
    PaymentRecord, clean_color,
)
from billing.models import Invoice
from billing.services import invoicing
from billing.tax import TaxLine
from models import Client, Order, OrderLine, db

needs_renderer = pytest.mark.skipif(
    not pdf.available(), reason="WeasyPrint/Pango isn't installed here",
)


@pytest.fixture(params=sorted(config.INVOICE_TEMPLATES))
def look(request):
    """Each layout in turn — the document rules hold for all of them."""
    return Branding(template=request.param)


def sample_doc() -> InvoiceDocument:
    """A sent, part-paid invoice. A plain function as well as a fixture, so
    `test_invoice_logo.py` can render the same document."""
    return InvoiceDocument(
        number="BM-2026-0007",
        status="sent",
        display_status="sent",
        issued_date=date(2026, 7, 1),
        due_date=date(2026, 7, 15),
        notes=None,
        issuer=IssuerDetails(
            name="By Monsieur",
            address="543 Cambie Street\nVancouver, BC  V6H 3P7",
            gst_number="123456789 RT0001",
            payment_instructions="E-transfer to pay@example.com",
        ),
        payer=PartyDetails(
            name="Marie Alarie", address="1 Rue Test\nMontréal, QC  H2X 1Y4",
            email="marie@example.com", url="/clients/1",
        ),
        subject_description="Briefcase",
        subject_url="/orders/1",
        lines=[LineItem("Briefcase", 1, 1000.0), LineItem("Monogram", 2, 25.0)],
        payments=[PaymentRecord(200.0, date(2026, 7, 2), "etransfer", "REF42")],
        subtotal=1050.0,
        tax_lines=[TaxLine("GST", 0.05, 52.5)],
        amount_paid=200.0,
        is_frozen=True,
        tax_status="ok",
    )


@pytest.fixture
def doc():
    return sample_doc()


# --- The document ---------------------------------------------------------

def test_the_html_carries_the_whole_invoice(app, doc, look):
    html = pdf.render_html(doc, look)

    for expected in (
        "BM-2026-0007", "By Monsieur", "543 Cambie Street", "123456789 RT0001",
        "Marie Alarie", "marie@example.com", "2026-07-01", "2026-07-15",
        "Briefcase", "Monogram", "$1050.00", "GST (5%)", "$52.50",
        "$1102.50", "$200.00", "$902.50", "E-transfer to pay@example.com",
        "E-transfer", "REF42",
    ):
        assert expected in html, expected


def test_the_html_is_the_document_alone(app, doc, look):
    """The whole reason this exists: none of the app around the invoice."""
    html = pdf.render_html(doc, look)

    assert html.lstrip().startswith("<!DOCTYPE html>")
    for app_chrome in ("view-switch", "site-footer", "Log out", "<script", "<form"):
        assert app_chrome not in html, app_chrome


def test_the_html_needs_nothing_from_the_network(app, doc, look):
    """Styles are inline and there is nothing to fetch — the renderer is
    told to refuse every URL, so a reference here would be a blank."""
    html = pdf.render_html(doc, look)

    for reference in ("http://", "https://", "<link", "<img", "url(", "@import"):
        assert reference not in html, reference


def test_host_links_are_not_rendered(app, doc, look):
    """A client can't follow a link into the app."""
    html = pdf.render_html(doc, look)

    assert "<a " not in html
    assert "/clients/1" not in html
    assert "/orders/1" not in html


def test_typed_text_is_escaped(app, doc, look):
    html = pdf.render_html(replace(
        doc, notes="<b>bold</b>", lines=[LineItem("<img src=x>", 1, 1.0)],
    ), look)

    assert "<b>bold</b>" not in html
    assert "&lt;b&gt;bold&lt;/b&gt;" in html
    assert "<img" not in html


def test_the_number_is_never_templated_into_the_stylesheet(app, doc, look):
    """It reaches the page footer through CSS `string-set`, so a prefix
    with a quote in it can't break out of a CSS string."""
    html = pdf.render_html(replace(doc, number='B"M-2026-0001'), look)
    stylesheet = html.split("<style>")[1].split("</style>")[0]

    assert "2026-0001" not in stylesheet
    assert "string(invoice-number)" in stylesheet


@pytest.mark.parametrize("status, label", [
    ("draft", "Draft"), ("void", "Void"), ("paid", "Paid"),
])
def test_a_status_that_changes_the_meaning_is_printed(app, doc, look, status, label):
    html = pdf.render_html(replace(doc, display_status=status), look)

    assert f'status--{status}">{label}<' in html


def test_sent_is_not_printed(app, doc, look):
    """That one is the app's bookkeeping, not the client's business."""
    assert 'class="status ' not in pdf.render_html(doc, look)


def test_payment_instructions_follow_the_same_rule_as_the_page(app, doc, look):
    void = pdf.render_html(replace(doc, status="void", display_status="void"), look)
    settled = pdf.render_html(replace(doc, amount_paid=1102.5), look)

    assert "How to pay" in pdf.render_html(doc, look)
    assert "How to pay" not in void
    assert "How to pay" not in settled


def test_an_invoice_with_no_lines_says_so(app, doc, look):
    html = pdf.render_html(replace(doc, lines=[], subtotal=0.0, tax_lines=[]), look)

    assert "Nothing itemised on this invoice." in html


def test_an_unknown_template_falls_back_to_the_default(app, doc):
    stale = Branding(template="no-such-template")

    assert pdf.render_html(doc, stale) == pdf.render_html(doc)
    assert pdf.render_html(doc) == pdf.render_html(doc, Branding(template="classic"))


def test_every_layout_offered_in_settings_has_a_template_behind_it():
    assert set(pdf.TEMPLATES) == set(config.INVOICE_TEMPLATES)
    assert config.DEFAULT_INVOICE_TEMPLATE in pdf.TEMPLATES


# --- Branding -------------------------------------------------------------

@pytest.mark.parametrize("typed, stored", [
    ("#1c1a17", "#1c1a17"),
    ("#D2B356", "#d2b356"),
    ("  #ffffff ", "#ffffff"),
    ("#fff", None),
    ("d2b356", None),
    ("red", None),
    ("#12345g", None),
    ("#1234567", None),
    ("#000000;} body{display:none", None),
    ("", None),
    (None, None),
    (123456, None),
])
def test_only_a_plain_hex_colour_counts_as_a_colour(typed, stored):
    assert clean_color(typed) == stored


def test_branding_with_nothing_chosen_is_the_default_look():
    branding = Branding()

    assert branding.template_key == config.DEFAULT_INVOICE_TEMPLATE
    assert branding.primary == config.DEFAULT_PRIMARY_COLOR


@pytest.mark.parametrize("primary, text", [
    ("#1c1a17", "#ffffff"),
    ("#000000", "#ffffff"),
    ("#1f4e79", "#ffffff"),
    ("#d2b356", "#1c1a17"),
    ("#ffffff", "#1c1a17"),
])
def test_text_on_the_band_stays_readable(primary, text):
    assert Branding(primary_color=primary).on_primary == text


def test_the_banded_layout_wears_the_chosen_colour(app, doc):
    html = pdf.render_html(doc, Branding(template="banded", primary_color="#1f4e79"))

    assert "background: #1f4e79" in html
    assert "color: #ffffff" in html          # text on the dark band
    assert "border-top: 2.25pt solid #1f4e79" in html   # table heading rule
    assert "color: #7e7a78" in html          # labels stay a quiet grey


def test_a_bad_stored_colour_never_reaches_the_stylesheet(app, doc):
    """The row is data. Whatever is in it, only a checked #rrggbb is ever
    written into CSS."""
    html = pdf.render_html(doc, Branding(
        template="banded",
        primary_color="red;} body{display:none} .x{url(https://evil.test/x)",
    ))

    assert "display:none" not in html
    assert "evil.test" not in html
    assert f"background: {config.DEFAULT_PRIMARY_COLOR}" in html


def test_the_classic_layout_ignores_the_colours(app, doc):
    plain = pdf.render_html(doc, Branding(template="classic"))
    coloured = pdf.render_html(doc, Branding(
        template="classic", primary_color="#ff0000",
    ))

    assert coloured == plain


# --- The preview's sample invoice -----------------------------------------

def test_the_sample_is_the_sellers_own_document(doc):
    sample = pdf.sample_document(
        doc.issuer, "BM-2026-0042", tax_province="BC", today=date(2026, 7, 1),
    )

    assert sample.number == "BM-2026-0042"
    assert sample.issuer == doc.issuer
    assert sample.issued_date == date(2026, 7, 1)
    assert sample.due_date == date(2026, 7, 15)
    assert sample.subtotal == 600.0
    # Registered for GST only, so that's the one tax a BC buyer is charged.
    assert [(line.label, line.amount) for line in sample.tax_lines] == [("GST", 30.0)]
    assert sample.payments == []
    assert sample.display_status == "sent"


def test_a_sample_from_an_unregistered_seller_charges_no_tax(doc):
    sample = pdf.sample_document(IssuerDetails(name="New Studio"), "INV-2026-0001")

    assert sample.tax_lines == []
    assert sample.total == 600.0


@pytest.mark.parametrize("number, expected", [
    ("BM-2026-0007", "BM-2026-0007.pdf"),
    ("B/M ../x-2026-0001", "BMx-2026-0001.pdf"),
    ("", "invoice.pdf"),
])
def test_the_filename_is_the_number_made_safe(doc, number, expected):
    assert pdf.filename_for(replace(doc, number=number)) == expected


# --- The renderer ---------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://example.com/logo.png",
    "http://169.254.169.254/latest/meta-data/",
    "file:///etc/passwd",
    "data:text/html;base64,PGI+",
    "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
    "data:image/png,not-base64-encoded",
])
def test_the_renderer_fetches_nothing(url):
    with pytest.raises(ValueError):
        pdf._embedded_png(url)


def test_the_renderer_accepts_only_an_embedded_png():
    """The logo, which is already inside the document — decoded by this
    module, never handed to the library's own fetcher."""
    assert pdf._embedded_png("data:image/png;base64,iVBORw0KGgo=") == b"\x89PNG\r\n\x1a\n"


def test_a_malformed_embedded_image_is_refused():
    with pytest.raises(ValueError):
        pdf._embedded_png("data:image/png;base64,***not base64***")


@needs_renderer
@pytest.mark.parametrize("url", [
    "https://example.com/logo.png", "file:///etc/passwd", "data:text/plain,hi",
])
def test_the_fetcher_handed_to_weasyprint_refuses_too(url):
    """The same rule, through the object the library actually calls — so
    a change in its fetcher API can't quietly reopen the door."""
    fetcher = pdf._fetcher(pdf._weasyprint())

    with pytest.raises(ValueError):
        fetcher.fetch(url)
    with pytest.raises(ValueError):
        fetcher(url)


@needs_renderer
def test_an_external_image_in_a_document_is_left_out_not_fetched(app, doc, monkeypatch):
    """End to end: a document that references a URL still renders, and the
    reference goes nowhere."""
    import urllib.request

    def explode(*args, **kwargs):
        raise AssertionError("the renderer tried to open a URL")

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", explode)
    weasyprint = pdf._weasyprint()
    html = '<img src="https://example.com/x.png"><img src="file:///etc/passwd"><p>ok</p>'

    data = weasyprint.HTML(string=html, url_fetcher=pdf._fetcher(weasyprint)).write_pdf()

    assert data.startswith(b"%PDF-")


def test_rendering_without_weasyprint_says_so(app, doc, monkeypatch):
    monkeypatch.setattr(pdf, "_weasyprint", lambda: None)

    assert pdf.available() is False
    with pytest.raises(pdf.PdfUnavailable):
        pdf.render_pdf(doc)


@needs_renderer
def test_it_renders_a_real_pdf(app, doc, look):
    data = pdf.render_pdf(doc, look)

    assert data.startswith(b"%PDF-")
    assert len(data) > 1000


@needs_renderer
def test_a_long_invoice_renders_without_error(app, doc, look):
    """The layout it replaces pinned blocks to the page bottom; this one
    has to flow onto as many pages as the lines need."""
    lines = [LineItem(f"Item {i}", 1, 10.0) for i in range(120)]

    assert pdf.render_pdf(replace(doc, lines=lines), look).startswith(b"%PDF-")


# --- The route ------------------------------------------------------------

@pytest.fixture
def order(company, client_record):
    row = Order(
        client_id=client_record.id, item="Briefcase",
        start=date(2026, 7, 1), due=date(2026, 7, 15), status="confirmed",
    )
    db.session.add(row)
    db.session.flush()
    db.session.add(OrderLine(
        order_id=row.id, description="Briefcase", quantity=1, unit_price=1000.0,
    ))
    db.session.commit()
    return row


@pytest.fixture
def invoice_id(logged_in, order):
    logged_in.post(f"/subjects/{order.id}/invoice", data={})
    db.session.expire_all()
    return db.session.get(Order, order.id).invoice.id


@pytest.fixture
def fake_renderer(monkeypatch):
    """Stand in for WeasyPrint, recording what it was handed:
    (document, branding) per call."""
    seen = []

    def render(document, branding=None):
        seen.append((document, branding))
        return b"%PDF-fake"

    monkeypatch.setattr(pdf, "available", lambda: True)
    monkeypatch.setattr(pdf, "render_pdf", render)
    return seen


def test_the_pdf_downloads_under_the_invoice_number(
    logged_in, invoice_id, fake_renderer
):
    response = logged_in.get(f"/invoices/{invoice_id}/pdf")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data == b"%PDF-fake"
    disposition = response.headers["Content-Disposition"]
    assert disposition.startswith("attachment")
    assert "BM-2026-0001.pdf" in disposition


def test_the_pdf_is_built_from_the_same_document_as_the_page(
    logged_in, invoice_id, fake_renderer
):
    logged_in.get(f"/invoices/{invoice_id}/pdf")

    ((document, _),) = fake_renderer
    assert document.number == "BM-2026-0001"
    assert document.subtotal == 1000.0
    assert document.issuer.name == "By Monsieur"


def test_another_tenants_invoice_pdf_404s(
    logged_in, other_company, fake_renderer
):
    stranger = Client(company_id=other_company.id, first_name="X", last_name="Y")
    db.session.add(stranger)
    db.session.flush()
    theirs = Order(
        client_id=stranger.id, item="Theirs",
        start=date(2026, 7, 1), due=date(2026, 7, 15), status="confirmed",
    )
    db.session.add(theirs)
    db.session.flush()
    from billing_adapter import billable_for
    invoice = invoicing.create_invoice(other_company.id, billable_for(theirs))
    db.session.commit()

    assert logged_in.get(f"/invoices/{invoice.id}/pdf").status_code == 404
    assert fake_renderer == []


def test_a_missing_invoice_pdf_404s(logged_in, fake_renderer):
    assert logged_in.get("/invoices/999999/pdf").status_code == 404


def test_the_pdf_requires_a_login(app, order):
    response = app.test_client().get("/invoices/1/pdf")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_without_a_renderer_the_pdf_url_returns_to_the_page(
    logged_in, invoice_id, monkeypatch
):
    monkeypatch.setattr(pdf, "_weasyprint", lambda: None)

    response = logged_in.get(f"/invoices/{invoice_id}/pdf")

    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/invoices/{invoice_id}")


def test_the_page_offers_the_pdf_when_it_can_be_rendered(
    logged_in, invoice_id, fake_renderer
):
    body = logged_in.get(f"/invoices/{invoice_id}").get_data(as_text=True)

    assert f'href="/invoices/{invoice_id}/pdf"' in body
    assert "Download PDF" in body
    assert "window.print" not in body


def test_the_page_falls_back_to_printing_when_it_cannot(
    logged_in, invoice_id, monkeypatch
):
    monkeypatch.setattr(pdf, "available", lambda: False)

    body = logged_in.get(f"/invoices/{invoice_id}").get_data(as_text=True)

    assert "Download PDF" not in body
    assert "Print / save as PDF" in body
    assert "window.print" in body


# --- The look is live, the content is frozen ------------------------------

def test_the_pdf_wears_the_companys_saved_look(
    logged_in, company, invoice_id, fake_renderer
):
    invoicing.update_profile(
        company.id, invoice_template="banded",
        primary_color="#1f4e79",
    )
    db.session.commit()

    logged_in.get(f"/invoices/{invoice_id}/pdf")

    ((_, branding),) = fake_renderer
    assert branding.template_key == "banded"
    assert branding.primary == "#1f4e79"


def test_an_issued_invoice_follows_a_rebrand_but_keeps_what_it_said(
    logged_in, company, invoice_id, fake_renderer
):
    """PD12. The seller's details were frozen when it was sent; the look
    wasn't, and isn't meant to be."""
    logged_in.post(f"/invoices/{invoice_id}/status", data={"status": "sent"})
    invoicing.update_profile(
        company.id, "Renamed Studio", street="1 New Street",
        invoice_template="banded", primary_color="#1f4e79",
    )
    db.session.commit()

    logged_in.get(f"/invoices/{invoice_id}/pdf")

    ((document, branding),) = fake_renderer
    assert document.issuer.name == "By Monsieur"
    assert "1 New Street" not in (document.issuer.address or "")
    assert branding.template_key == "banded"
    assert branding.primary == "#1f4e79"


# --- The preview ----------------------------------------------------------

def test_the_preview_shows_a_sample_in_the_saved_look(
    logged_in, company, fake_renderer
):
    invoicing.update_profile(
        company.id, invoice_template="banded", primary_color="#1f4e79",
    )
    db.session.commit()

    response = logged_in.get("/invoices/preview.pdf")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.headers["Content-Disposition"].startswith("inline")
    ((document, branding),) = fake_renderer
    assert document.issuer.name == "By Monsieur"
    assert document.payer.name == "Sample Client"
    assert branding.template_key == "banded"
    assert branding.primary == "#1f4e79"


def test_the_preview_uses_up_no_invoice_number(logged_in, company, fake_renderer):
    logged_in.get("/invoices/preview.pdf")
    logged_in.get("/invoices/preview.pdf")

    ((first, _), (second, _)) = fake_renderer
    assert first.number == second.number == "BM-%d-0001" % date.today().year
    assert Invoice.query.count() == 0


def test_the_preview_requires_a_login(app):
    response = app.test_client().get("/invoices/preview.pdf")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_without_a_renderer_the_preview_goes_somewhere_that_works(
    logged_in, monkeypatch
):
    monkeypatch.setattr(pdf, "_weasyprint", lambda: None)

    response = logged_in.get("/invoices/preview.pdf")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/invoices")
