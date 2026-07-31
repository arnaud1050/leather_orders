"""
The module's blueprint: upload, download, inline view, delete.

`resolve_order(order_id)` is supplied by the host at registration time
(app.py's `get_order_or_404`) rather than imported directly — this module
can't import from `app.py` without creating a circular import, since
`app.py` is what imports and registers this blueprint. Same hook pattern
as `billing.routes.register`.

No CSRF layer here, deliberately — matches every other mutating route in
`app.py` (edit_order, delete_payment, ...), which rely on the app-wide
`SESSION_COOKIE_SAMESITE=Lax` rather than a token. Communications' CSRF
layer exists because that module sends mail and disconnects accounts; nothing
here is in that class of risk.
"""

from flask import Blueprint, abort, redirect, request, send_file, session, url_for
from flask_login import current_user, login_required

from documents import config, services
from documents.storage import path_for

bp = Blueprint("documents", __name__, template_folder="templates")

_resolve_order = None


def register(app, *, resolve_order) -> None:
    global _resolve_order
    _resolve_order = resolve_order
    app.register_blueprint(bp)


def _flash(message: str) -> None:
    """One-shot message for the next page render.

    Same reasoning as communications' `_flash`/`take_notice`: the app has
    no flash-message convention, so this stays scoped to a session key this
    module owns rather than introducing Flask's flash app-wide.
    """
    session["documents_notice"] = message


def take_notice() -> str | None:
    """Public form, for order_page() in app.py — the page this module's
    upload form redirects back to — to surface an upload rejection."""
    return session.pop("documents_notice", None)


def _get_order_or_404(order_id: int):
    return _resolve_order(order_id)


def _get_document_or_404(order_id: int, document_id: int):
    document = services.get_for_order(order_id, document_id)
    if document is None:
        abort(404)
    return document


def _redirect_back(order_id: int):
    return_to = request.values.get("return_to") or url_for("order_page", order_id=order_id)
    return redirect(return_to)


@bp.route("/orders/<int:order_id>/documents/upload", methods=["POST"])
@login_required
def upload(order_id: int):
    order = _get_order_or_404(order_id)
    files = [
        (f.filename, f.read())
        for f in request.files.getlist("files")
        if f and f.filename
    ]
    result = services.upload(current_user.company_id, order.id, files)
    if result.errors:
        _flash(" ".join(result.errors))
    return _redirect_back(order.id)


@bp.route("/orders/<int:order_id>/documents/<int:document_id>/download")
@login_required
def download(order_id: int, document_id: int):
    order = _get_order_or_404(order_id)
    document = _get_document_or_404(order.id, document_id)
    path = path_for(document.company_id, document.stored_filename)
    if path is None:
        abort(404)
    return send_file(
        path,
        as_attachment=True,
        download_name=document.original_filename,
        # Forced download, not rendered on our origin — same posture as
        # communications' attachment download.
        mimetype="application/octet-stream",
    )


@bp.route("/orders/<int:order_id>/documents/<int:document_id>/view")
@login_required
def view(order_id: int, document_id: int):
    order = _get_order_or_404(order_id)
    document = _get_document_or_404(order.id, document_id)
    if document.content_type not in config.INLINE_PREVIEWABLE_CONTENT_TYPES:
        return redirect(url_for("documents.download", order_id=order.id, document_id=document.id))
    path = path_for(document.company_id, document.stored_filename)
    if path is None:
        abort(404)
    return send_file(path, as_attachment=False, mimetype=document.content_type)


@bp.route("/orders/<int:order_id>/documents/<int:document_id>/delete", methods=["POST"])
@login_required
def delete(order_id: int, document_id: int):
    order = _get_order_or_404(order_id)
    document = _get_document_or_404(order.id, document_id)
    services.delete(document)
    return _redirect_back(order.id)
