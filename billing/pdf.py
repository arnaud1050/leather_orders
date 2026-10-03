"""
An invoice as a real PDF, rendered on the server.

Replaces the browser's print dialog as the export path: that one printed
the page's URL, date and title around the document and looked like a web
page, because it was one.

WeasyPrint is imported lazily, on the same terms as the Google and OpenAI
libraries elsewhere in the app: it needs the Pango system library, which
both Docker images install and a Windows dev machine usually lacks. Where
it can't load, `available()` is False and the invoice page falls back to
the browser's print dialog rather than offering a button that 500s.

Two halves, split so the first is testable anywhere:

- `render_html(doc)` — the document as standalone HTML. No WeasyPrint.
- `render_pdf(doc)` — that HTML turned into bytes. Needs WeasyPrint.

Templates read only from `doc` (an `InvoiceDocument`) and `branding` (a
`Branding`), and carry their stylesheet inline and their logo embedded:
the renderer is given a fetcher that refuses everything but an embedded
image, so nothing typed into an invoice can make the server go and fetch
something.
"""

import base64
import functools
import re
from datetime import date, timedelta

from flask import render_template

from billing import config, tax
from billing.documents import (
    Branding, InvoiceDocument, IssuerDetails, LineItem, PartyDetails,
)

__all__ = [
    "PdfUnavailable", "available", "filename_for", "render_html", "render_pdf",
    "sample_document",
]

# Where each layout in config.INVOICE_TEMPLATES lives. A test keeps the two
# in step, so a layout can't be offered in settings without a file behind it.
TEMPLATES = {
    "classic": "billing/pdf/classic.html",
    "banded": "billing/pdf/banded.html",
}


class PdfUnavailable(RuntimeError):
    """WeasyPrint, or the system library under it, isn't installed here."""


@functools.lru_cache(maxsize=1)
def _weasyprint():
    """The library, or None. Cached: a failed import is slow to repeat, and
    whether Pango is installed doesn't change while the process runs."""
    try:
        import weasyprint
    except (ImportError, OSError):
        # OSError is what a missing Pango raises — the Python package
        # imports fine and then can't find the shared library.
        return None
    return weasyprint


def available() -> bool:
    return _weasyprint() is not None


_EMBEDDED_PNG = "data:image/png;base64,"


def _embedded_png(url: str) -> bytes:
    """The bytes of an embedded PNG — or ValueError for any other URL.

    This is the renderer's whole idea of "fetching": nothing is ever
    fetched. The templates need no external resource (styles are inline,
    fonts are installed on the system), so anything asking for one is a
    mistake or an attempt to make the server request a URL of someone
    else's choosing.

    The single thing let through is the logo, which arrives *inside* the
    document as a base64 PNG. It's decoded here rather than handed to the
    library's own fetcher, so no code path that can open a file or a socket
    is reachable from a template.
    """
    if not url.startswith(_EMBEDDED_PNG):
        # Truncated: a data: URI can be megabytes, and this lands in a log.
        raise ValueError(f"Invoice PDFs load no external resources: {url[:80]}")
    try:
        return base64.b64decode(url[len(_EMBEDDED_PNG):], validate=True)
    except ValueError:
        raise ValueError("Malformed embedded image in an invoice PDF.") from None


def _fetcher(weasyprint):
    """A WeasyPrint URL fetcher that answers only through `_embedded_png`.

    Built on demand because the base class lives in the lazily-imported
    library. It overrides `fetch`, the one method every request goes
    through, so none of the base class's HTTP/file/FTP handlers is ever
    reached. A refused URL is logged by WeasyPrint and the resource left
    out — the invoice still renders.

    This is the WeasyPrint 70 fetcher API (a `URLFetcher` subclass returning
    a `URLFetcherResponse`); older releases took a plain function, which is
    why requirements.txt asks for 70 or later.
    """
    class EmbeddedOnly(weasyprint.urls.URLFetcher):
        def fetch(self, url, headers=None):
            return weasyprint.urls.URLFetcherResponse(
                url, _embedded_png(url), {"Content-Type": "image/png"})

    return EmbeddedOnly()


def render_html(doc: InvoiceDocument, branding: Branding | None = None) -> str:
    """The invoice as a standalone HTML document, ready for the renderer.

    `branding` picks the layout and its colours; without one it's the
    default layout. Branding itself resolves an unknown layout or a bad
    colour to the default rather than raising — a stale value on a profile
    must not stop an invoice from being exported.
    """
    branding = branding or Branding()
    return render_template(
        TEMPLATES[branding.template_key],
        doc=doc,
        branding=branding,
        status_labels=config.STATUS_LABELS,
        payment_method_labels=config.PAYMENT_METHOD_LABELS,
    )


def render_pdf(doc: InvoiceDocument, branding: Branding | None = None) -> bytes:
    weasyprint = _weasyprint()
    if weasyprint is None:
        raise PdfUnavailable("WeasyPrint is not available in this environment.")
    html = render_html(doc, branding)
    return weasyprint.HTML(string=html, url_fetcher=_fetcher(weasyprint)).write_pdf()


def sample_document(issuer: IssuerDetails, number: str,
                    tax_province: str | None = None,
                    today: date | None = None) -> InvoiceDocument:
    """A made-up invoice from this seller, for previewing a look.

    The seller's side is real — their letterhead, their next number, the
    tax their own registrations would charge a buyer in `tax_province` —
    so the preview shows their document, with an invented buyer on it.
    Nothing is stored and no number is consumed.
    """
    today = today or date.today()
    lines = [
        LineItem("Sample item", 1, 480.0),
        LineItem("Another sample item, with a longer description", 2, 60.0),
    ]
    subtotal = sum(line.total for line in lines)
    tax_lines = tax.taxes_for(tax_province, issuer.tax_registrations, subtotal)
    return InvoiceDocument(
        number=number,
        status="sent",
        display_status="sent",
        issued_date=today,
        due_date=today + timedelta(days=14),
        notes="This is a preview. Notes added to an invoice print here.",
        issuer=issuer,
        payer=PartyDetails(
            name="Sample Client",
            address="123 Example Street\nVancouver, BC  V6B 1A1",
            email="client@example.com",
        ),
        subject_description="Sample order",
        subject_url=None,
        lines=lines,
        payments=[],
        subtotal=subtotal,
        tax_lines=tax_lines,
        amount_paid=0.0,
        is_frozen=False,
        tax_status=tax.status_for(tax_province, issuer.tax_registrations, tax_lines),
    )


def filename_for(doc: InvoiceDocument) -> str:
    """`BM-2026-0001.pdf` — the number, stripped to what's safe in a
    filename. The prefix is tenant-typed, so it isn't trusted as-is."""
    stem = re.sub(r"[^\w\-]", "", doc.number or "") or "invoice"
    return f"{stem}.pdf"
