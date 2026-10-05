"""
The module's own blueprint: the invoice list, the invoice page and its PDF.

A host that wants its own UI can skip `register()` entirely and drive
`billing.services.invoicing` directly — these routes are a convenience,
not the API.

Two things the host supplies at registration time, because billing can't
know them: how to turn a `subject_id` into a `Billable`, and how to label
the back link. Everything else the templates need arrives on the
`InvoiceDocument`, including the host URLs for the buyer and the subject.
"""

import io
import re
from datetime import date

from flask import (
    Blueprint, abort, redirect, render_template, request, send_file, session, url_for,
)
from flask_login import current_user, login_required

from models import db
from usage import track

from billing import config, pdf
from billing.services import invoicing

bp = Blueprint("billing", __name__, template_folder="templates")

# Host-supplied hooks, installed by register().
_resolve_billable = None
_uninvoiced = None
_display_name = None
_back_label = None


def register(app, *, resolve_billable, uninvoiced=None, display_name=None,
             back_label=None) -> None:
    """Attach the blueprint.

    - `resolve_billable(company_id)` -> callable subject_id -> Billable
    - `uninvoiced(company_id)` -> rows for the "not invoiced yet" list,
      each needing `.url`, `.label`, `.payer`, `.total`, `.due`
    - `display_name(company_id)` -> the seller's name (it lives on the
      host's tenant model, not in this module)
    - `back_label(path)` -> wording for the back link
    """
    global _resolve_billable, _uninvoiced, _display_name, _back_label
    _resolve_billable = resolve_billable
    _uninvoiced = uninvoiced
    _display_name = display_name
    _back_label = back_label
    app.register_blueprint(bp)


def _seller_name(company_id: int) -> str:
    return _display_name(company_id) if _display_name else ""


def _label_for(path: str) -> str:
    return _back_label(path) if _back_label else "Back"


@bp.app_context_processor
def _inject_pdf_availability():
    """Lets any template — the host's settings page included — ask whether
    a PDF can be rendered here, without the host importing `billing.pdf`."""
    return {"invoice_pdf_available": pdf.available}


@bp.route("/invoices")
@login_required
def invoice_list():
    company_id = current_user.company_id
    name = _seller_name(company_id)
    documents = invoicing.documents_for(
        company_id, _resolve_billable(company_id), name
    )
    outstanding = sum(
        doc.balance_due for doc in documents
        if doc.status != "void" and not doc.is_settled
    )
    return render_template(
        "billing/invoice_list.html",
        documents=list(zip(invoicing.list_invoices(company_id), documents)),
        uninvoiced=_uninvoiced(company_id) if _uninvoiced else [],
        outstanding=outstanding,
        status_labels=config.STATUS_LABELS,
        active_view="invoices",
    )


def _flash(message: str, category: str = "success") -> None:
    """One-shot message for the next render of the invoice page, in the
    section the form names in `notice_section` (the host's MOD8). Its own
    session key, like every module's: the app has no flash convention."""
    raw = request.form.get("notice_section") or ""
    session["billing_notice"] = {
        "message": message, "category": category,
        "section": raw if re.fullmatch(r"[a-z0-9-]{1,40}", raw) else None,
    }


@bp.route("/invoices/<int:invoice_id>")
@login_required
def invoice_page(invoice_id: int):
    company_id = current_user.company_id
    invoice = invoicing.get_invoice(company_id, invoice_id)
    if invoice is None:
        abort(404)
    name = _seller_name(company_id)
    document = invoicing.document_for(
        company_id, invoice, _resolve_billable(company_id)(invoice.subject_id), name
    )
    return_to = request.args.get("return_to") or url_for("billing.invoice_list")
    return render_template(
        "billing/invoice_page.html",
        invoice=invoice,
        doc=document,
        return_to=return_to,
        back_label=_label_for(return_to),
        status_labels=config.STATUS_LABELS,
        payment_method_labels=config.PAYMENT_METHOD_LABELS,
        settable_statuses=config.SETTABLE_STATUSES,
        notes_max_length=config.NOTES_MAX_LENGTH,
        pdf_available=pdf.available(),
        notice=session.pop("billing_notice", None),
        active_view=None,
    )


@bp.route("/invoices/<int:invoice_id>/pdf")
@login_required
def invoice_pdf(invoice_id: int):
    """The invoice as a downloadable PDF.

    Built from the same `InvoiceDocument` as the page, so the frozen-vs-live
    decision is made once, in `document_for`, and the two can't disagree.
    """
    company_id = current_user.company_id
    invoice = invoicing.get_invoice(company_id, invoice_id)
    if invoice is None:
        abort(404)
    document = invoicing.document_for(
        company_id, invoice, _resolve_billable(company_id)(invoice.subject_id),
        _seller_name(company_id),
    )
    try:
        data = pdf.render_pdf(document, invoicing.branding_for(company_id))
    except pdf.PdfUnavailable:
        # The page doesn't link here when the renderer is missing, so this
        # is a hand-typed or stale URL — send it somewhere that works.
        return redirect(url_for("billing.invoice_page", invoice_id=invoice.id))
    return send_file(
        io.BytesIO(data), mimetype="application/pdf",
        as_attachment=True, download_name=pdf.filename_for(document),
    )


@bp.route("/invoices/preview.pdf")
@login_required
def preview_pdf():
    """A sample invoice in this tenant's saved look, shown in the browser.

    For the settings page: what the layout and colours actually produce,
    without having to open a real invoice. Stores nothing and uses up no
    number.
    """
    company_id = current_user.company_id
    name = _seller_name(company_id)
    profile = invoicing.profile_for(company_id, name)
    document = pdf.sample_document(
        profile.issuer, invoicing.next_number(company_id, name),
        tax_province=profile.province,
    )
    try:
        data = pdf.render_pdf(document, invoicing.branding_for(company_id))
    except pdf.PdfUnavailable:
        return redirect(url_for("billing.invoice_list"))
    return send_file(
        io.BytesIO(data), mimetype="application/pdf",
        as_attachment=False, download_name="invoice-preview.pdf",
    )


@bp.route("/invoices/logo.png")
@login_required
def logo():
    """The signed-in tenant's own logo, for the settings page to show.

    No id in the URL: whose logo it is comes from the session, so there is
    nothing to guess at. Never cached — a replaced logo has to show at once.
    """
    path = invoicing.logo_path(current_user.company_id)
    if path is None:
        abort(404)
    return send_file(path, mimetype="image/png", max_age=0)


@bp.route("/subjects/<int:subject_id>/invoice", methods=["POST"])
@login_required
def create(subject_id: int):
    """Raise a draft invoice for one subject.

    The host's own route guards which subjects this user may reach; the
    resolver raises LookupError for anything outside the tenant, which is
    the second line of defence.
    """
    company_id = current_user.company_id
    try:
        billable = _resolve_billable(company_id)(subject_id)
    except LookupError:
        abort(404)
    due_date_str = request.form.get("due_date")
    invoice = invoicing.create_invoice(
        company_id, billable,
        due_date=date.fromisoformat(due_date_str) if due_date_str else None,
        display_name=_seller_name(company_id),
    )
    db.session.commit()
    track("invoice.created")
    return redirect(url_for("billing.invoice_page", invoice_id=invoice.id))


@bp.route("/invoices/<int:invoice_id>/status", methods=["POST"])
@login_required
def set_status(invoice_id: int):
    company_id = current_user.company_id
    invoice = invoicing.get_invoice(company_id, invoice_id)
    if invoice is None:
        abort(404)
    due_date_str = request.form.get("due_date")
    was = invoice.status
    invoicing.set_status(
        company_id, invoice,
        status=request.form.get("status"),
        billable=_resolve_billable(company_id)(invoice.subject_id),
        notes=request.form.get("notes", ""),
        due_date=date.fromisoformat(due_date_str) if due_date_str else None,
        display_name=_seller_name(company_id),
    )
    db.session.commit()
    if invoice.status != was:
        track("invoice.status_changed", to=invoice.status)
        message = f"Invoice marked {config.STATUS_LABELS[invoice.status].lower()}."
        if was == "draft":
            # The one save here that can't be taken back (frozen at issue).
            message += " What it says is now frozen."
        _flash(message)
    else:
        _flash("Invoice saved.")
    return_to = request.form.get("return_to") or url_for(
        "billing.invoice_page", invoice_id=invoice.id)
    return redirect(return_to)
