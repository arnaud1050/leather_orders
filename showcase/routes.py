"""
The blueprint: the Showcase page, the item editor, photos, the order-tab
decision, and Settings → Showcase.

Every route belongs to the `showcase` feature: `_gate` 404s them all for a
company without it (FE7, SC1), so a route added later is covered without
anyone remembering to cover it. The order page's tab *route* is the host's
(`order_showcase` in app.py, same split as Materials and inventory/); it
renders `order_tab_context()` from here, and every form inside the tab
posts back to this blueprint.

No CSRF layer, matching documents/ and app.py (`SESSION_COOKIE_SAMESITE=Lax`).
"""

import re

from flask import (
    Blueprint, abort, redirect, render_template, request, send_file, session, url_for,
)
from flask_login import current_user, login_required

import features
from usage import track

from showcase import config, hooks, kiosk, services, storage, website
from showcase.models import (
    STATUS_LABELS, VISIBILITIES, VISIBILITY_LABELS, ShowcaseCategory, ShowcaseSpecField,
)

bp = Blueprint("showcase", __name__, template_folder="templates")

NOTICE_KEY = "showcase_notice"
# URL slug -> (model, singular noun for messages, usage section)
LISTS = {
    "categories": (ShowcaseCategory, "category", "showcase_categories"),
    "spec-fields": (ShowcaseSpecField, "spec field", "showcase_spec_fields"),
}


def register(app, **hook_functions) -> None:
    """Attach both blueprints — this one, and the kiosk links' (kiosk.py) —
    with the host's hooks (see hooks.py)."""
    hooks.configure(**hook_functions)
    app.register_blueprint(bp)
    app.register_blueprint(kiosk.bp)


@bp.before_request
def _gate():
    # Signed out: let each view's @login_required send them to /login,
    # rather than answering 404 to somebody who simply isn't signed in yet.
    if not current_user.is_authenticated:
        return None
    features.require(services.FEATURE)
    return None


def _company_id() -> int | None:
    if not current_user.is_authenticated:
        return None
    return current_user.company_id


@bp.app_context_processor
def _inject_showcase():
    """For base.html's nav badge and order_page.html's tab — callables, so
    nothing is queried on a page that doesn't ask, and both stay quiet for
    a company without the feature."""

    def showcase_awaiting_count() -> int:
        company_id = _company_id()
        if company_id is None or not features.is_enabled(company_id, services.FEATURE):
            return 0
        return len(services.awaiting_orders(company_id))

    def showcase_order_state(order_id: int) -> dict:
        company_id = _company_id()
        if company_id is None:
            return {"available": False, "awaiting": False}
        order = hooks.order_summary(company_id, order_id)
        available = services.order_tab_available(company_id, order)
        return {
            "available": available,
            "awaiting": available and services.is_awaiting(company_id, order),
        }

    return {
        "showcase_awaiting_count": showcase_awaiting_count,
        "showcase_order_state": showcase_order_state,
    }


# ---------------------------------------------------------------------------
# Notices and redirects
# ---------------------------------------------------------------------------

def _flash(message: str, category: str = "error", section: str | None = None) -> None:
    """One-shot message, shown in the section whose form posted it (MOD8)."""
    raw = section or request.form.get("notice_section") or ""
    session[NOTICE_KEY] = {
        "message": message, "category": category,
        "section": raw if re.fullmatch(r"[a-z0-9-]{1,40}", raw) else None,
    }


def take_notice() -> dict | None:
    return session.pop(NOTICE_KEY, None)


def _back(default: str):
    """Back to the page the form was on — the order tab or the item page,
    carrying its `return_to` (hard rule 14). Only a local path is honoured."""
    target = request.form.get("next") or ""
    if not target.startswith("/") or target.startswith("//"):
        target = default
    return redirect(target)


def _item_or_404(item_id: int):
    item = services.get_item(current_user.company_id, item_id)
    if item is None:
        abort(404)
    return item


def _order_or_404(order_id: int) -> dict:
    order = hooks.order_summary(current_user.company_id, order_id)
    if order is None:
        abort(404)
    return order


# ---------------------------------------------------------------------------
# What the editor needs, for the item page and the order tab alike
# ---------------------------------------------------------------------------

def _editor_context(company_id: int, item, next_url: str) -> dict:
    return {
        "item": item,
        "next_url": next_url,
        "categories": services.offered_categories(company_id, item),
        "spec_rows": services.spec_rows(company_id, item),
        "shown_specs": services.shown_specs(company_id, item),
        "order_images": services.unused_order_images(company_id, item),
        "order": hooks.order_summary(company_id, item.order_id) if item.order_id else None,
        "visibilities": VISIBILITIES,
        "visibility_labels": VISIBILITY_LABELS,
        "max_photos": config.MAX_PHOTOS_PER_ITEM,
        "accept": config.ACCEPT_ATTRIBUTE,
        "max_upload_bytes": config.MAX_UPLOAD_BYTES,
        "has_spec_fields": bool(services.list_rows(ShowcaseSpecField, company_id)),
        "has_categories": bool(services.list_rows(ShowcaseCategory, company_id)),
        "website_state": website.state(company_id, item),
    }


def order_tab_context(order_id: int, next_url: str) -> dict:
    """Everything `showcase/_order_tab.html` renders, for app.py's tab
    route. 404s like every other showcase page when the tab isn't offered
    for this order (SC6)."""
    company_id = current_user.company_id
    features.require(services.FEATURE)
    order = hooks.order_summary(company_id, order_id)
    if not services.order_tab_available(company_id, order):
        abort(404)
    item = services.item_for_order(company_id, order_id)
    context = {
        "order_summary": order,
        "awaiting": item is None and services.is_awaiting(company_id, order),
        "dismissed": item is None and services.is_dismissed(company_id, order_id),
        "notice": take_notice(),
        "next_url": next_url,
        "item": item,
    }
    if item is not None:
        context.update(_editor_context(company_id, item, next_url))
    return context


# ---------------------------------------------------------------------------
# The Showcase page and the item page
# ---------------------------------------------------------------------------

def _int_arg(name: str) -> int | None:
    raw = request.args.get(name, "")
    return int(raw) if raw.isdigit() else None


@bp.route("/showcase")
@login_required
def showcase_list():
    company_id = current_user.company_id
    categories = services.list_rows(ShowcaseCategory, company_id)
    category_id = _int_arg("category")
    if category_id not in {c.id for c in categories}:
        category_id = None
    status = request.args.get("status")
    status = status if status in STATUS_LABELS else None
    visibility = request.args.get("visibility")
    visibility = visibility if visibility in VISIBILITIES else None
    return render_template(
        "showcase/list.html",
        items=services.list_items(company_id, category_id=category_id,
                                  status=status, visibility=visibility),
        awaiting=services.awaiting_orders(company_id),
        categories=categories,
        category_id=category_id,
        status=status,
        visibility=visibility,
        status_labels=STATUS_LABELS,
        visibility_labels=VISIBILITY_LABELS,
        website_connected=website.get_website(company_id) is not None,
        website_summary=website.summary(company_id),
        notice=take_notice(),
        active_view="showcase",
    )


def _details_form(item) -> dict:
    """The details form's fields, as update_details/details_error take them.
    A field the form didn't render keeps the piece's value (hard rule 9)."""
    raw_category = request.form.get("category_id", "")
    return {
        "title": request.form.get("title", item.title),
        "description": request.form.get("description", item.description or ""),
        "category_id": int(raw_category) if raw_category.isdigit() else None,
        "visibility": request.form.get("visibility", item.visibility),
        "specs": {
            int(key.removeprefix("spec_")): value
            for key, value in request.form.items()
            if key.startswith("spec_") and key.removeprefix("spec_").isdigit()
        },
    }


def _new_page(item, specs: dict | None = None, notice: dict | None = None):
    company_id = current_user.company_id
    spec_rows = services.spec_rows(company_id, item)
    if specs:
        spec_rows = [(field, specs.get(field.id, value)) for field, value in spec_rows]
    return render_template(
        "showcase/new.html",
        item=item,
        sc={
            "categories": services.offered_categories(company_id, item),
            "spec_rows": spec_rows,
            "visibilities": VISIBILITIES,
            "visibility_labels": VISIBILITY_LABELS,
            "max_photos": config.MAX_PHOTOS_PER_ITEM,
            "accept": config.ACCEPT_ATTRIBUTE,
            "max_upload_bytes": config.MAX_UPLOAD_BYTES,
            "has_spec_fields": bool(services.list_rows(ShowcaseSpecField, company_id)),
            "has_categories": bool(services.list_rows(ShowcaseCategory, company_id)),
        },
        notice=notice,
        active_view="showcase",
    )


@bp.route("/showcase/items/new")
@login_required
def new_item():
    """The form for a piece with no order. Opening it saves nothing: a
    piece exists only once Save draft or Publish posts it (SC9a)."""
    return _new_page(services.new_item(current_user.company_id), notice=take_notice())


@bp.route("/showcase/items", methods=["POST"])
@login_required
def create_item():
    company_id = current_user.company_id
    draft = services.new_item(company_id)
    fields = _details_form(draft)
    specs = fields.pop("specs")
    error = services.details_error(company_id, draft, **fields)
    if error is not None:
        # Nothing saved: the form again, with what was typed (not the photos,
        # which a browser never refills).
        draft.title, draft.description = fields["title"], fields["description"]
        draft.category_id, draft.visibility = fields["category_id"], fields["visibility"]
        return _new_page(draft, specs, {"message": error, "category": "error",
                                        "section": "details"})

    item = services.create_item(company_id, fields["title"])
    services.update_details(company_id, item, specs=specs, **fields)
    track("showcase.item_created", via="standalone")
    errors, added = [], 0
    for upload in request.files.getlist("photos"):
        if not upload or not upload.filename:
            continue
        photo_error = services.add_photo(company_id, item, upload.read(), upload.filename)
        if photo_error:
            errors.append(photo_error)
        else:
            added += 1
            track("showcase.photo_added", source="upload")

    if request.form.get("action") == "publish":
        publish_error = services.publish(company_id, item)
        if publish_error is None:
            track("showcase.item_published", visibility=item.visibility)
            message = _published_message(company_id, item)
        else:
            errors.append(publish_error)
            message = "Saved as a draft."
    else:
        message = "Saved as a draft."
    if errors:
        _flash(" ".join([message] + errors), "error", section="details")
    else:
        _flash(message, "success", section="details")
    return redirect(url_for("showcase.item_page", item_id=item.id))


@bp.route("/showcase/items/<int:item_id>")
@login_required
def item_page(item_id: int):
    item = _item_or_404(item_id)
    next_url = url_for("showcase.item_page", item_id=item.id)
    return render_template(
        "showcase/item.html",
        sc=_editor_context(current_user.company_id, item, next_url),
        notice=take_notice(),
        active_view="showcase",
    )


# ---------------------------------------------------------------------------
# Editing an item
# ---------------------------------------------------------------------------

@bp.route("/showcase/items/<int:item_id>", methods=["POST"])
@login_required
def save_item(item_id: int):
    item = _item_or_404(item_id)
    company_id = current_user.company_id
    error = services.update_details(company_id, item, **_details_form(item))
    if error is None and request.form.get("action") == "publish":
        was_published = item.status == "published"
        error = services.publish(company_id, item)
        if error is None and not was_published:
            track("showcase.item_published", visibility=item.visibility)
            _flash(_published_message(company_id, item), "success")
            return _back(url_for("showcase.item_page", item_id=item.id))
    if error is not None:
        _flash(error)
    else:
        _flash("Saved.", "success")
    return _back(url_for("showcase.item_page", item_id=item.id))


def _published_message(company_id: int, item) -> str:
    online = item.visibility == "public" and website.get_website(company_id) is not None
    return ("Published. It's in the catalog now"
            + (". It goes to the website only when you send it." if online else "."))


@bp.route("/showcase/items/<int:item_id>/withdraw", methods=["POST"])
@login_required
def withdraw_item(item_id: int):
    item = _item_or_404(item_id)
    if item.status == "published":
        services.withdraw(current_user.company_id, item)
        track("showcase.item_withdrawn")
        _flash("Withdrawn. It's out of the catalog; nothing was deleted.", "success")
    return _back(url_for("showcase.item_page", item_id=item.id))


@bp.route("/showcase/items/<int:item_id>/delete", methods=["POST"])
@login_required
def delete_item(item_id: int):
    item = _item_or_404(item_id)
    title, order_id = item.title, item.order_id
    error = services.delete_item(current_user.company_id, item)
    if error is not None:
        _flash(error)
        return _back(url_for("showcase.item_page", item_id=item_id))
    # The item page no longer exists; an order's tab still does, and shows
    # the decision again (SC10). Back to the tab the form was on, keeping
    # its return_to, or to that order's tab from the item page.
    order = hooks.order_summary(current_user.company_id, order_id) if order_id else None
    if order is not None:
        _flash(f'"{title}" deleted.', "success", section="decision")
        target = request.form.get("next") or ""
        if not target.startswith(order["showcase_url"]):
            target = order["showcase_url"]
        return redirect(target)
    _flash(f'"{title}" deleted.', "success", section="items")
    return redirect(url_for("showcase.showcase_list"))


# ---------------------------------------------------------------------------
# Photos
# ---------------------------------------------------------------------------

@bp.route("/showcase/items/<int:item_id>/photos", methods=["POST"])
@login_required
def upload_photos(item_id: int):
    item = _item_or_404(item_id)
    errors, added = [], 0
    for upload in request.files.getlist("photos"):
        if not upload or not upload.filename:
            continue
        error = services.add_photo(current_user.company_id, item, upload.read(),
                                   upload.filename)
        if error:
            errors.append(error)
        else:
            added += 1
            track("showcase.photo_added", source="upload")
    _report_photos(added, errors)
    return _back(url_for("showcase.item_page", item_id=item.id))


@bp.route("/showcase/items/<int:item_id>/photos/from-order", methods=["POST"])
@login_required
def add_order_photos(item_id: int):
    item = _item_or_404(item_id)
    ids = [int(i) for i in request.form.getlist("document_id") if i.isdigit()]
    before = len(item.photos)
    errors = services.add_order_photos(current_user.company_id, item, ids)
    added = len(item.photos) - before
    for _ in range(added):
        track("showcase.photo_added", source="order")
    if not ids:
        errors = ["Tick the documents to add first."]
    _report_photos(added, errors)
    return _back(url_for("showcase.item_page", item_id=item.id))


def _report_photos(added: int, errors: list[str]) -> None:
    if errors:
        prefix = f"{added} photo{'s' if added != 1 else ''} added. " if added else ""
        _flash(prefix + " ".join(errors))
    elif added:
        _flash(f"{added} photo{'s' if added != 1 else ''} added.", "success")


@bp.route("/showcase/items/<int:item_id>/photos/<int:photo_id>/delete", methods=["POST"])
@login_required
def delete_photo(item_id: int, photo_id: int):
    item = _item_or_404(item_id)
    error = services.delete_photo(current_user.company_id, item, photo_id)
    if error:
        _flash(error)
    return _back(url_for("showcase.item_page", item_id=item.id))


@bp.route("/showcase/items/<int:item_id>/photos/<int:photo_id>/cover", methods=["POST"])
@login_required
def make_cover(item_id: int, photo_id: int):
    """The way to choose a cover without dragging: HTML drag and drop does
    nothing on a touch screen, and an iPad is where this gets used."""
    item = _item_or_404(item_id)
    services.reorder_photos(current_user.company_id, item, [photo_id])
    return _back(url_for("showcase.item_page", item_id=item.id))


@bp.route("/showcase/items/<int:item_id>/photos/reorder", methods=["POST"])
@login_required
def reorder_photos(item_id: int):
    """Fired by the drag handler — a JSON body, no page load."""
    item = _item_or_404(item_id)
    payload = request.get_json(silent=True) or {}
    ids = [i for i in payload.get("order", []) if isinstance(i, int)]
    services.reorder_photos(current_user.company_id, item, ids)
    return "", 204


def _serve_photo(photo_id: int, thumbnail: bool):
    photo = services.get_photo(current_user.company_id, photo_id)
    if photo is None:
        abort(404)
    path = storage.path_for(photo.company_id,
                            photo.thumbnail_filename if thumbnail else photo.stored_filename)
    if path is None:
        abort(404)
    return send_file(path, mimetype="image/jpeg", max_age=3600)


@bp.route("/showcase/photos/<int:photo_id>.jpg")
@login_required
def photo(photo_id: int):
    return _serve_photo(photo_id, thumbnail=False)


@bp.route("/showcase/photos/<int:photo_id>-thumb.jpg")
@login_required
def photo_thumbnail(photo_id: int):
    return _serve_photo(photo_id, thumbnail=True)


# ---------------------------------------------------------------------------
# Catalog mode for a signed-in user ("Present" on the Showcase page)
# ---------------------------------------------------------------------------

@bp.route("/showcase/present")
@login_required
def present():
    """The same catalog a kiosk link shows, on this device, for whoever is
    signed in — with a way back to the app, and no service worker: working
    offline is what kiosk links are for (SC31)."""
    return kiosk.render_catalog(
        current_user.company_id,
        photo_url=lambda p: url_for("showcase.photo", photo_id=p.id),
        thumb_url=lambda p: url_for("showcase.photo_thumbnail", photo_id=p.id),
        logo_url=url_for("showcase.logo"),
        exit_url=url_for("showcase.showcase_list"),
    )


@bp.route("/showcase/logo.png")
@login_required
def logo():
    return kiosk.serve_logo(current_user.company_id)


# ---------------------------------------------------------------------------
# The order tab's decision: showcase it, or say it won't be
# ---------------------------------------------------------------------------

@bp.route("/showcase/orders/<int:order_id>/start", methods=["POST"])
@login_required
def start_for_order(order_id: int):
    order = _order_or_404(order_id)
    company_id = current_user.company_id
    if not services.order_tab_available(company_id, order):
        abort(404)
    existed = services.item_for_order(company_id, order_id) is not None
    services.start_for_order(company_id, order)
    if not existed:
        track("showcase.item_created", via="order")
    return _back(url_for("showcase.showcase_list"))


@bp.route("/showcase/orders/<int:order_id>/dismiss", methods=["POST"])
@login_required
def dismiss_order(order_id: int):
    order = _order_or_404(order_id)
    company_id = current_user.company_id
    if services.item_for_order(company_id, order_id) is None:
        services.dismiss(company_id, order["id"], current_user.id)
        track("showcase.order_dismissed")
        _flash(f'"{order["item"]}" won\'t be showcased. You can change your mind '
               "from its Showcase tab.", "success")
    return _back(url_for("showcase.showcase_list"))


@bp.route("/showcase/orders/<int:order_id>/undismiss", methods=["POST"])
@login_required
def undismiss_order(order_id: int):
    order = _order_or_404(order_id)
    services.undismiss(current_user.company_id, order["id"])
    _flash("Back on the list of pieces waiting for a decision.", "success")
    return _back(url_for("showcase.showcase_list"))


# ---------------------------------------------------------------------------
# The website: one piece, the review page, linking (SC43, SC45)
# ---------------------------------------------------------------------------

RESULTS_KEY = "showcase_website_results"


@bp.route("/showcase/items/<int:item_id>/website", methods=["POST"])
@login_required
def send_item(item_id: int):
    """The piece's own button: send it, send it again, send it as a new
    card, or take it off. Nothing reaches the website any other way."""
    item = _item_or_404(item_id)
    action = request.form.get("action", "")
    ok, message = website.send(current_user.company_id, item, action)
    if ok:
        track("showcase.website_sent", action=action, count=1)
    _flash(f'"{item.title}" {message}.' if ok else f'"{item.title}": {message}',
           "success" if ok else "error", section="website")
    return _back(url_for("showcase.item_page", item_id=item.id))


def _results_page(template: str, **context):
    return render_template(template, results=session.pop(RESULTS_KEY, None),
                           notice=take_notice(), active_view="showcase", **context)


def _chosen_items(company_id: int) -> list:
    ids = {int(i) for i in request.form.getlist("item_id") if i.isdigit()}
    return [item for item in (services.get_item(company_id, i) for i in sorted(ids))
            if item is not None]


@bp.route("/showcase/website")
@login_required
def website_review():
    company_id = current_user.company_id
    connection = website.get_website(company_id)
    if connection is None:
        return redirect(url_for("showcase.settings"))
    return _results_page(
        "showcase/website.html",
        connection=connection,
        rows=website.pending(company_id),
        outcomes=website.ACTION_OUTCOMES,
    )


@bp.route("/showcase/website", methods=["POST"])
@login_required
def website_send():
    """The review page's one button: every ticked piece, each with the
    action its state offers now (SC43). Results are reported per line."""
    company_id = current_user.company_id
    results = []
    for item in _chosen_items(company_id):
        current = website.state(company_id, item)
        if current is None or current.action not in ("send", "take_off"):
            continue
        ok, message = website.send(company_id, item, current.action)
        results.append({"title": item.title, "ok": ok, "message": message, "item_id": item.id})
    if not results:
        _flash("Tick at least one piece to send.", section="review")
    else:
        sent = sum(1 for r in results if r["ok"])
        track("showcase.website_sent", action="review", count=sent)
        session[RESULTS_KEY] = results
    return redirect(url_for("showcase.website_review"))


@bp.route("/showcase/website/link")
@login_required
def website_link_review():
    company_id = current_user.company_id
    connection = website.get_website(company_id)
    if connection is None:
        return redirect(url_for("showcase.settings"))
    return _results_page(
        "showcase/website_link.html",
        connection=connection,
        items=website.link_candidates(company_id),
    )


@bp.route("/showcase/website/link", methods=["POST"])
@login_required
def website_link():
    company_id = current_user.company_id
    results = []
    for item in _chosen_items(company_id):
        ok, message = website.link(company_id, item)
        results.append({"title": item.title, "ok": ok, "message": message, "item_id": item.id})
    if not results:
        _flash("Tick at least one piece to link.", section="review")
    else:
        track("showcase.website_linked", count=sum(1 for r in results if r["ok"]))
        session[RESULTS_KEY] = results
    return redirect(url_for("showcase.website_link_review"))


# ---------------------------------------------------------------------------
# Settings → Showcase
# ---------------------------------------------------------------------------

NEW_LINK_KEY = "showcase_new_kiosk_token"
NEW_SECRET_KEY = "showcase_new_website_secret"


@bp.route("/settings/showcase")
@login_required
def settings():
    company_id = current_user.company_id
    # A link just created: shown on this one render and never again (SC27).
    new_token = session.pop(NEW_LINK_KEY, None)
    new_link_url = (url_for("showcase_kiosk.catalog", token=new_token, _external=True)
                    if new_token else None)
    return render_template(
        "showcase/settings.html",
        section="showcase",
        categories=services.list_rows(ShowcaseCategory, company_id),
        spec_fields=services.list_rows(ShowcaseSpecField, company_id),
        order_types=hooks.order_types(company_id),
        excluded=services.excluded_type_ids(company_id),
        default_visibility=services.get_settings(company_id).default_visibility,
        visibilities=VISIBILITIES,
        visibility_labels=VISIBILITY_LABELS,
        kiosk_links=services.list_kiosk_links(company_id),
        catalog_count=len(services.catalog_items(company_id)),
        new_link_url=new_link_url,
        new_link_qr=kiosk.qr_svg(new_link_url) if new_link_url else None,
        website=website.get_website(company_id),
        # The secret just generated: shown on this one render, never again.
        new_secret=session.pop(NEW_SECRET_KEY, None),
        link_count=len(website.link_candidates(company_id)),
        notice=take_notice(),
        active_view="settings",
    )


@bp.route("/settings/showcase/kiosk-links", methods=["POST"])
@login_required
def create_kiosk_link():
    link, token = services.create_kiosk_link(current_user.company_id,
                                             request.form.get("name", ""))
    session[NEW_LINK_KEY] = token
    track("showcase.kiosk_link_created")
    _flash(f'Link "{link.name}" created. Open it on the device now: it\'s shown only once.',
           "success")
    return redirect(url_for("showcase.settings"))


@bp.route("/settings/showcase/kiosk-links/<int:link_id>/delete", methods=["POST"])
@login_required
def delete_kiosk_link(link_id: int):
    link = services.revoke_kiosk_link(current_user.company_id, link_id)
    if link is not None:
        _flash(f'"{link.name}" deleted. That device can no longer open the catalog.', "success")
    return redirect(url_for("showcase.settings"))


def _list_or_404(kind: str):
    if kind not in LISTS:
        abort(404)
    return LISTS[kind]


@bp.route("/settings/showcase/<kind>", methods=["POST"])
@login_required
def add_list_row(kind: str):
    model, noun, usage_section = _list_or_404(kind)
    label = request.form.get("label", "")
    error = services.add_row(model, current_user.company_id, label)
    if error:
        _flash(error)
    else:
        track("settings.changed", section=usage_section)
        _flash(f'{noun.capitalize()} "{label.strip()}" added.', "success")
    return redirect(url_for("showcase.settings"))


@bp.route("/settings/showcase/<kind>/<int:row_id>/toggle", methods=["POST"])
@login_required
def toggle_list_row(kind: str, row_id: int):
    model, noun, usage_section = _list_or_404(kind)
    row = services.toggle_row(model, current_user.company_id, row_id)
    if row is not None:
        track("settings.changed", section=usage_section)
        _flash(f'"{row.label}" is offered again.' if row.is_active
               else f'"{row.label}" hidden. Pieces that use it keep it.', "success")
    return redirect(url_for("showcase.settings"))


@bp.route("/settings/showcase/<kind>/<int:row_id>/delete", methods=["POST"])
@login_required
def delete_list_row(kind: str, row_id: int):
    model, noun, usage_section = _list_or_404(kind)
    result = services.delete_row(model, current_user.company_id, row_id)
    if result is not None:
        label, deleted = result
        if deleted:
            track("settings.changed", section=usage_section)
            _flash(f'"{label}" deleted.', "success")
        else:
            _flash(f'"{label}" is used by a piece, so it can\'t be deleted. Hide it instead.')
    return redirect(url_for("showcase.settings"))


@bp.route("/settings/showcase/<kind>/reorder", methods=["POST"])
@login_required
def reorder_list(kind: str):
    model, _, usage_section = _list_or_404(kind)
    payload = request.get_json(silent=True) or {}
    ids = [i for i in payload.get("order", []) if isinstance(i, int)]
    services.reorder_rows(model, current_user.company_id, ids)
    if ids:
        track("settings.changed", section=usage_section)
    return "", 204


@bp.route("/settings/showcase/order-types", methods=["POST"])
@login_required
def save_excluded_types():
    if "order_types_shown" in request.form:  # hard rule 9
        chosen = {int(i) for i in request.form.getlist("excluded") if i.isdigit()}
        services.set_excluded_types(current_user.company_id, chosen)
        track("settings.changed", section="showcase_order_types")
        _flash("Saved.", "success")
    return redirect(url_for("showcase.settings"))


@bp.route("/settings/showcase/visibility", methods=["POST"])
@login_required
def save_default_visibility():
    error = services.set_default_visibility(
        current_user.company_id, request.form.get("visibility", ""))
    if error:
        _flash(error)
    else:
        track("settings.changed", section="showcase_visibility")
        _flash("Saved. New pieces start with this visibility.", "success")
    return redirect(url_for("showcase.settings"))


@bp.route("/settings/showcase/website", methods=["POST"])
@login_required
def connect_website():
    secret, error = website.connect(current_user.company_id, request.form.get("url", ""))
    if error:
        _flash(error, section="website")
    else:
        session[NEW_SECRET_KEY] = secret
        track("showcase.website_connected")
        _flash("Connected. Copy the secret below into the website now: it's shown only once. "
               "Nothing is sent until you press a button.", "success", section="website")
    return redirect(url_for("showcase.settings"))


@bp.route("/settings/showcase/website/test", methods=["POST"])
@login_required
def test_website():
    error = website.test_connection(current_user.company_id)
    if error:
        _flash(error, section="website")
    else:
        _flash("The website answered. Nothing was sent.", "success", section="website")
    return redirect(url_for("showcase.settings"))


@bp.route("/settings/showcase/website/delete", methods=["POST"])
@login_required
def disconnect_website():
    if website.disconnect(current_user.company_id):
        _flash("Website connection deleted. Its cards stay on the website as they are.",
               "success", section="website")
    return redirect(url_for("showcase.settings"))
