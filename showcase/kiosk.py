"""
Catalog mode: the full-screen gallery and slideshow, opened either by a
signed-in user (`/showcase/present`, in routes.py) or by a kiosk link
(`/k/<token>/`, here) on a tablet nobody is signed in on.

A kiosk link is a capability URL: the token *is* the permission, so this
blueprint never looks at `current_user`. That's why it's separate from
`routes.bp` — that one's gate checks the signed-in user's company, which
for a kiosk could be nobody, or someone else entirely. Here every request
resolves the token first (`services.resolve_kiosk`), and an unknown or
deleted link, a company without the feature, or a deactivated company all
get the same 404 (SC29). app.py exempts these endpoints from its own
signed-in guards for the same reason.

Everything a kiosk page loads lives under `/k/<token>/` — the page, its
photos, the logo, the service worker and the manifest — so the service
worker's scope covers the page and the token never has to be repeated in
a query string. `Referrer-Policy: no-referrer` keeps the token from
leaking to the font host.
"""

import json
import os

from flask import (
    Blueprint, Response, abort, redirect, render_template, send_file, url_for,
)
from markupsafe import Markup

from showcase import hooks, services, storage

bp = Blueprint("showcase_kiosk", __name__, template_folder="templates")

# Minutes of no touch in the gallery before the slideshow starts by itself.
IDLE_MINUTES = 3


@bp.after_request
def _private_headers(response):
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _link_or_404(token: str):
    link = services.resolve_kiosk(token)
    if link is None:
        abort(404)
    return link


def render_catalog(company_id: int, *, photo_url, thumb_url, logo_url, exit_url=None,
                   sw_url=None, manifest_url=None) -> str:
    """The catalog page, for a kiosk link or a signed-in user alike; only
    the URLs differ."""
    brand = hooks.studio_brand(company_id)
    payload = services.catalog_payload(company_id, photo_url, thumb_url)
    payload.update({
        "studio": brand.get("name") or "",
        "logo": logo_url if brand.get("logo_path") else None,
        "idleMinutes": IDLE_MINUTES,
        "serviceWorker": sw_url,
    })
    return render_template(
        "showcase/catalog.html",
        payload=payload,
        studio=payload["studio"],
        logo_url=payload["logo"],
        # The page is near-black: a dark logo needs a light plate behind it;
        # a light or opaque one goes straight on (SC26, brand BL12).
        logo_plate=brand.get("logo_tone") == "dark",
        exit_url=exit_url,
        manifest_url=manifest_url,
    )


@bp.route("/k/<token>")
def catalog_no_slash(token: str):
    _link_or_404(token)
    return redirect(url_for("showcase_kiosk.catalog", token=token), code=301)


@bp.route("/k/<token>/")
def catalog(token: str):
    link = _link_or_404(token)
    html = render_catalog(
        link.company_id,
        photo_url=lambda p: url_for("showcase_kiosk.photo", token=token, photo_id=p.id),
        thumb_url=lambda p: url_for("showcase_kiosk.photo_thumbnail", token=token, photo_id=p.id),
        logo_url=url_for("showcase_kiosk.logo", token=token),
        sw_url=url_for("showcase_kiosk.service_worker", token=token),
        manifest_url=url_for("showcase_kiosk.manifest", token=token),
    )
    response = Response(html, mimetype="text/html")
    # Always revalidated: the service worker, not the browser cache, is what
    # keeps a copy for when the market's wifi drops (SC31).
    response.headers["Cache-Control"] = "no-cache"
    return response


def _serve_photo(token: str, photo_id: int, thumbnail: bool):
    link = _link_or_404(token)
    photo = services.get_photo(link.company_id, photo_id)
    # Only photos of pieces the catalog shows: a draft's or a private
    # piece's photo id 404s even with a valid link (SC28).
    if photo is None or not services.in_catalog(link.company_id, photo):
        abort(404)
    path = storage.path_for(photo.company_id,
                            photo.thumbnail_filename if thumbnail else photo.stored_filename)
    if path is None:
        abort(404)
    return send_file(path, mimetype="image/jpeg", max_age=86400)


@bp.route("/k/<token>/photos/<int:photo_id>.jpg")
def photo(token: str, photo_id: int):
    return _serve_photo(token, photo_id, thumbnail=False)


@bp.route("/k/<token>/photos/<int:photo_id>-thumb.jpg")
def photo_thumbnail(token: str, photo_id: int):
    return _serve_photo(token, photo_id, thumbnail=True)


def serve_logo(company_id: int):
    path = hooks.studio_brand(company_id).get("logo_path")
    if not path or not os.path.exists(path):
        abort(404)
    return send_file(path, mimetype="image/png", max_age=3600)


@bp.route("/k/<token>/logo.png")
def logo(token: str):
    return serve_logo(_link_or_404(token).company_id)


@bp.route("/k/<token>/sw.js")
def service_worker(token: str):
    _link_or_404(token)
    response = Response(render_template("showcase/catalog_sw.js"),
                        mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache"
    return response


@bp.route("/k/<token>/manifest.webmanifest")
def manifest(token: str):
    """What "Add to Home Screen" uses: opens straight into the catalog,
    full screen, no browser chrome (SC32)."""
    link = _link_or_404(token)
    brand = hooks.studio_brand(link.company_id)
    start = url_for("showcase_kiosk.catalog", token=token)
    data = {
        "name": brand.get("name") or "Showcase",
        "short_name": (brand.get("name") or "Showcase")[:12],
        "start_url": start,
        "scope": start,
        "display": "fullscreen",
        "background_color": "#111111",
        "theme_color": "#111111",
    }
    if brand.get("logo_path"):
        data["icons"] = [{"src": url_for("showcase_kiosk.logo", token=token),
                          "type": "image/png", "sizes": "any"}]
    return Response(json.dumps(data), mimetype="application/manifest+json")


def qr_svg(text: str) -> Markup:
    """An inline SVG QR code for the new link (Settings → Showcase). Our
    own output from our own URL, so safe to mark up."""
    import qrcode
    import qrcode.image.svg

    image = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    svg = image.to_string(encoding="unicode")
    svg = svg.replace('width="', 'data-width="').replace('height="', 'data-height="', 1)
    return Markup(svg.replace("<svg ", '<svg class="showcase-qr" role="img" aria-label="QR code for the link" ', 1))
