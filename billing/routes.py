"""
The module's own blueprint: the invoice list and the printable invoice.

A host that wants its own UI can skip `register()` entirely and drive
`billing.services.invoicing` directly — these routes are a convenience,
not the API.

Two things the host supplies at registration time, because billing can't
know them: how to turn a `subject_id` into a `Billable`, and how to label
the back link. Everything else the templates need arrives on the
`InvoiceDocument`, including the host URLs for the buyer and the subject.
"""

from datetime import date

from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from models import db

from billing import config
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
        active_view=None,
    )


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
    return redirect(url_for("billing.invoice_page", invoice_id=invoice.id))


@bp.route("/invoices/<int:invoice_id>/status", methods=["POST"])
@login_required
def set_status(invoice_id: int):
    company_id = current_user.company_id
    invoice = invoicing.get_invoice(company_id, invoice_id)
    if invoice is None:
        abort(404)
    due_date_str = request.form.get("due_date")
    invoicing.set_status(
        company_id, invoice,
        status=request.form.get("status"),
        billable=_resolve_billable(company_id)(invoice.subject_id),
        notes=request.form.get("notes", ""),
        due_date=date.fromisoformat(due_date_str) if due_date_str else None,
        display_name=_seller_name(company_id),
    )
    db.session.commit()
    return_to = request.form.get("return_to") or url_for(
        "billing.invoice_page", invoice_id=invoice.id)
    return redirect(return_to)
