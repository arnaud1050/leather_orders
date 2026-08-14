"""
Atelier Order Book — prototype calendar/timeline view
Custom leather goods order & inventory planner (Flask)

This is a prototype: data lives in a local SQLite database (see models.py)
and is scoped per Company. One company ("By Monsieur") is seeded on an
empty database; further tenants are provisioned by a platform admin from
/admin (see admin/CLAUDE.md). Every query filters by company_id, which is
what made that second tenant additive rather than an audit of query logic.
"""

import json
import os
import re

# Load .env before ANY other project import. Not stylistic: communications/
# config.py reads os.environ at *module* level, so loading after that import
# leaves the module reporting itself unconfigured while a perfectly good .env
# sits on disk. `python app.py` never reads .env on its own (only the
# `flask run` CLI does), which is exactly how that bit us.
#
# Optional dependency — the app must still boot where it isn't installed and
# the environment is set directly, as in both Docker deployments.
try:
    from dotenv import load_dotenv
except ImportError:
    pass
else:
    load_dotenv()

from datetime import date, timedelta  # noqa: E402 — must follow load_dotenv
from calendar import Calendar, month_name  # noqa: E402

from flask import Flask, render_template, request, redirect, url_for, abort, session  # noqa: E402
from flask_login import (  # noqa: E402
    LoginManager, current_user, login_required, login_user, logout_user,
)

from models import (  # noqa: E402
    Client, Company, Order, OrderLine, OrderType, Payment,
    SourceOption, User, db, ensure_platform_admin, normalise_email,
    run_migrations, seed_if_empty,
)
# NOT a self-contained module, unlike everything below it: the platform
# admin area exists to manage Company and User, so it imports host models
# on purpose and the module-boundary tests don't cover it. See
# admin/__init__.py — it's a package rather than more routes in this file
# only because this file is long enough already.
import admin.routes as admin_routes  # noqa: E402
# Self-contained module: its own model, services, blueprint and templates
# (see ai/__init__.py). It holds a company's vendor API keys and prompts and
# never sees a host model — everything it needs about an order, a document
# or a mail thread arrives through a hook registered here.
import ai.migrations as ai_migrations  # noqa: E402
import ai.routes as ai_routes  # noqa: E402
# Self-contained module: its own models, services, blueprint and templates
# (see billing/__init__.py). billing_adapter.py is the only file that knows
# an "order" is what this app bills for.
import billing.migrations as billing_migrations  # noqa: E402
import billing.routes as billing_routes  # noqa: E402
import billing_adapter  # noqa: E402
from billing import config as billing_config  # noqa: E402
from billing.services import invoicing  # noqa: E402
from billing.tax import PROVINCES  # noqa: E402
# Self-contained module: its own models, services, templates and blueprint
# (see communications/__init__.py). Importing it here registers its tables
# with db.create_all() below; register() attaches its routes. Nothing in
# this file calls Gmail — the module's services are the only entry point.
import communications.jobs as communications_jobs  # noqa: E402
import communications.migrations as communications_migrations  # noqa: E402
import communications.routes as communications_routes  # noqa: E402
from communications.services import (  # noqa: E402
    calendar_service, email_service, sender_rules,
)
# Self-contained module: its own table, storage, migrations, blueprint and
# templates (see documents/__init__.py). Real files attached to an order —
# resolve_order is handed in below rather than this module importing
# get_order_or_404 directly, to avoid a circular import with this file.
import documents.migrations as documents_migrations  # noqa: E402
import documents.routes as documents_routes  # noqa: E402
from documents import config as documents_config  # noqa: E402
from documents import services as documents_service  # noqa: E402
from documents import storage as documents_storage  # noqa: E402
# Self-contained module: its own tables, migrations, blueprint and templates
# (see inventory/__init__.py). Cost-tracking only — nothing here ever
# touches Order.total/OrderLine/the invoice.
import inventory.migrations as inventory_migrations  # noqa: E402
import inventory.routes as inventory_routes  # noqa: E402
from inventory import services as inventory_service  # noqa: E402
from inventory.config import UNIT_LABELS as INVENTORY_UNIT_LABELS  # noqa: E402
from inventory.config import UNIT_WHOLE as INVENTORY_UNIT_WHOLE  # noqa: E402

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# DATABASE_URL overrides the default SQLite file. Two reasons it's an env
# var rather than a constant: tests and scripts need to point at a throwaway
# database (Flask-SQLAlchemy builds its engine at import, so overriding the
# config afterwards is too late and silently writes to the real file), and
# it's the single change needed to move to Postgres/MySQL later — see
# "Known gaps" in CLAUDE.md.
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
    "DATABASE_URL", "sqlite:///" + os.path.join(DATA_DIR, "atelier.db")
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Set a real SECRET_KEY via the environment in production/Docker — this
# fallback is only safe for local dev.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-not-secure")
# Defence in depth behind the communications blueprint's CSRF tokens: Lax
# stops the session cookie riding along on cross-site POSTs at all, so a
# forged form never even reaches the token check.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_HTTPONLY"] = True
# Only over TLS in production. Off by default because local dev is plain
# http and a Secure cookie there means nobody can log in at all.
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE") == "1"
# Blunt outer guard on a multi-file document upload request, ahead of the
# more precise per-file/per-company checks in documents/validation.py — so
# an oversized request is rejected before Flask fully buffers it.
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024

# Behind a TLS-terminating reverse proxy (the demo deployment's nginx), the
# request arrives at gunicorn as plain http, so request.url reads
# "http://..." — and oauthlib refuses to parse an http authorization
# response at all ("OAuth 2 MUST utilize https"), which breaks the Google
# callback specifically. ProxyFix rebuilds scheme/host from
# X-Forwarded-Proto / -Host so it reads what the browser actually used.
#
# Opt-in, because these headers are only trustworthy when something we
# control sets them: exposed directly, any client could send
# X-Forwarded-Proto: https and forge the origin. Set it on the deployment
# that really is behind a proxy, and nowhere else.
if os.environ.get("TRUST_PROXY_HEADERS") == "1":
    from werkzeug.middleware.proxy_fix import ProxyFix

    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

db.init_app(app)
communications_routes.register(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"


@login_manager.user_loader
def load_user(user_id):
    """Resolve the session's user, refusing one that's been switched off.

    The login route already blocks a deactivated user or company, but that
    only guards the moment of signing in — somebody deactivated *while*
    holding a live session would otherwise keep it until the cookie
    expired, which would make deactivation a request to leave rather than
    an instruction. Returning None here logs that session out on its very
    next request, which is what the platform admin pressing the button
    means.
    """
    user = db.session.get(User, int(user_id))
    if user is None or not user.is_active:
        return None
    # Platform staff have no company, so there's no company to be inactive
    # — the `and` is doing real work here, not defending against a null.
    if user.company is not None and not user.company.is_active:
        return None
    return user


with app.app_context():
    db.create_all()
    run_migrations()  # adds columns create_all can't; see models.py
    # The module keeps its own column migrations, so the root model file
    # doesn't have to know what it stores. Called here because app.py is the
    # composition root — a call either way between models.py and the module
    # would be circular.
    communications_migrations.run_migrations()
    # Same arrangement for billing: its own tables, its own column
    # migrations, run after the app's (which may still be splitting a
    # legacy address this module then takes over).
    billing_migrations.set_subtotal_resolver(billing_adapter.subtotal_of)
    billing_migrations.run()
    # Same arrangement again: its own table (order_documents) plus a
    # one-time drop of the legacy fake `documents` table (see
    # documents/migrations.py) — run before seeding so a fresh company
    # never sees the old placeholder rows this replaces.
    documents_migrations.run_migrations()
    # Same arrangement again: its own tables (inventory_units,
    # inventory_prefs, inventory_types, inventory_items, order_materials,
    # order_material_others) — all still covered by create_all, plus its own
    # ADDED_COLUMNS for the columns added to inventory_items since it
    # shipped, and the InventoryUnit backfill.
    inventory_migrations.run_migrations()
    # Same arrangement again: its own table (ai_settings), brand new, so
    # ADDED_COLUMNS is empty and the call is a no-op — it's wired up now so
    # this module's first column migration lands in its own file rather than
    # the root one (hard rule 12).
    ai_migrations.run_migrations()
    # Bootstrap only — one company, its admin user, and the SourceOption /
    # OrderType starter lists. Deliberately no sample clients or orders: a
    # production deployment starts empty. To load the demo dataset in a test
    # environment, run scripts/seed_sample_data.py.
    # ADMIN_EMAIL because email is the login identity now — the fallback is
    # an `.invalid` placeholder that can never be a real mailbox, which is
    # the honest default for an address nobody has told us yet.
    seed_if_empty(
        admin_password=os.environ.get("ADMIN_PASSWORD", "changeme"),
        admin_email=os.environ.get("ADMIN_EMAIL", "admin@example.invalid"),
    )
    # Guarded on "is there any platform staff?", not on "is the database
    # empty?" — which is why it's a separate call. A single-tenant database
    # being migrated has a company already, so seeding returns early, and
    # without this there'd be nobody who could reach /admin.
    ensure_platform_admin(
        email=os.environ.get("PLATFORM_ADMIN_EMAIL", "platform@example.invalid"),
        password=os.environ.get("PLATFORM_ADMIN_PASSWORD", "changeme"),
    )

# Background mailbox/calendar sync. A no-op unless RUN_SCHEDULER=1 — with
# two gunicorn workers an unguarded scheduler would start twice and race
# itself, so exactly one process should set it. See communications/jobs.py.
communications_jobs.start_scheduler(app)

# Owned by the billing module now — aliased so the rest of this file and
# its templates keep reading one name.
PAYMENT_METHOD_LABELS = billing_config.PAYMENT_METHOD_LABELS
INVOICE_STATUS_LABELS = billing_config.STATUS_LABELS
SETTABLE_INVOICE_STATUSES = billing_config.SETTABLE_STATUSES

# The order lifecycle. Two inactive ends (`tentative` before the work is
# real, `delivered`/`cancelled` after it's over) around the active middle.
#
# `in_progress` is in here for its label only — it is never stored. An
# order sits at `confirmed` and `Order.display_status` renames it once the
# start date arrives, so the timeline can't show "confirmed" three weeks
# into the work. Anything writing a status goes through ALLOWED_TRANSITIONS
# below, which has no `in_progress` key for exactly that reason.
STATUS_LABELS = {
    "tentative": "Tentative",
    "confirmed": "Confirmed",
    "in_progress": "In progress",
    "ready": "Ready for pickup",
    "delivered": "Delivered",
    "cancelled": "Cancelled",
}

# Where an order may go from where it is. Both final stages map to nothing:
# an order moves forward or it gets called off, never backwards (correct a
# mis-stage by editing the order, not by reversing it).
ALLOWED_TRANSITIONS = {
    "tentative": ("confirmed", "cancelled"),
    "confirmed": ("ready", "cancelled"),
    "ready": ("delivered", "cancelled"),
    "delivered": (),
    "cancelled": (),
}

# What a brand-new order may start at. An order that's already finished
# isn't something you create, it's something you arrive at.
INITIAL_STATUSES = ("tentative", "confirmed")


def settable_statuses(status: str) -> list[str]:
    """Stages the plain edit form may move an order to, from `status`.

    Where it is now plus its forward transitions — `cancelled` is
    deliberately excluded even though it's a legal transition, because
    cancelling has to collect a reason and so goes through its own route
    and confirm dialog. Takes the status string rather than the order so
    the timeline (whose rows are dicts, not `Order`s) can call it too.
    Used by the templates to build the dropdown *and* by `edit_order()` to
    check what came back, so the two can't drift apart.
    """
    return [status] + [
        s for s in ALLOWED_TRANSITIONS.get(status, ()) if s != "cancelled"
    ]


app.jinja_env.globals["settable_statuses"] = settable_statuses


# Zones offered at /settings/general. A curated list, not
# zoneinfo.available_timezones() — that's ~600 entries in no useful order, and
# every one of them is a way to mislabel a studio's own mail. Canada first
# (west to east), then the couple of places its clients and suppliers are.
# Anything missing is one line here; the stored value is the IANA name, so
# nothing but this list has to change.
TIME_ZONES = [
    ("America/Vancouver", "Vancouver (Pacific)"),
    ("America/Edmonton", "Edmonton (Mountain)"),
    ("America/Regina", "Regina (Central, no DST)"),
    ("America/Winnipeg", "Winnipeg (Central)"),
    ("America/Toronto", "Toronto (Eastern)"),
    ("America/Halifax", "Halifax (Atlantic)"),
    ("America/St_Johns", "St. John's (Newfoundland)"),
    ("America/New_York", "New York (Eastern)"),
    ("America/Los_Angeles", "Los Angeles (Pacific)"),
    ("Europe/London", "London"),
    ("Europe/Paris", "Paris"),
    ("UTC", "UTC"),
]



# --- What the billing module needs from this app --------------------------
#
# Four small hooks, all host knowledge the module deliberately doesn't
# have: how to price a subject, what hasn't been invoiced, what the seller
# is called, and how to word a back link. billing_adapter.py does the
# actual translation; these just hand it over.

def _uninvoiced_rows(company_id: int):
    """Orders with no invoice yet — the to-do list the invoice page exists
    for, shaped the way the module's template expects."""
    invoiced = invoicing.invoiced_subject_ids(company_id)
    # A called-off order is never going to be invoiced, so it has no place
    # on a list whose whole purpose is "these still need billing".
    orders = (
        Order.query.join(Client)
        .filter(Client.company_id == company_id, Order.status != "cancelled")
        .order_by(Order.due)
        .all()
    )
    return [
        {
            "url": url_for("order_page", order_id=order.id),
            "label": order.item,
            "payer": order.client.name,
            "payer_url": url_for("client_page", client_id=order.client.id),
            "total": order.total,
            "due": order.due,
            # display_status, not status: this feeds a `dot--{{ row.status }}`
            # colour chip, and the chip should say what the timeline says.
            "status": order.display_status,
        }
        for order in orders if order.id not in invoiced
    ]


def _seller_name(company_id: int) -> str:
    company = db.session.get(Company, company_id)
    return company.name if company else ""


billing_routes.register(
    app,
    resolve_billable=billing_adapter.resolver,
    uninvoiced=_uninvoiced_rows,
    display_name=_seller_name,
    back_label=lambda path: back_label(path),
)


def get_order_or_404(order_id: int) -> Order:
    order = (
        Order.query.join(Client)
        .filter(Order.id == order_id, Client.company_id == current_user.company_id)
        .first()
    )
    if order is None:
        abort(404)
    return order


documents_routes.register(app, resolve_order=get_order_or_404)
inventory_routes.register(app, resolve_order=get_order_or_404)
# TIME_ZONES is handed in rather than imported: admin/ can't import this
# file without a circular import, and the list belongs to the app.
admin_routes.register(app, time_zones=TIME_ZONES)


def _attachable_documents(company_id: int, client_id: int) -> dict:
    """This client's orders and the documents filed against each, for the
    mail compose form's "Attach document" picker.

    The composition root does this join because neither module can: a
    document belongs to an order (`documents/`), and `communications/` must
    not learn what an order is. Orders with no documents are dropped —
    picking one only to find an empty second dropdown is a dead end, so it
    isn't offered.

    `has_orders` is what's left of the ones dropped, and it's the whole
    reason this returns a dict rather than the list: an empty picker has
    two very different fixes ("this client has no orders yet" vs "upload
    something to one"), and a bare `[]` can't tell the modal which to say.

    Each document carries its type label so the dropdown can group by it,
    matching the sections the order page already files them under —
    `documents.services.sections_for_order` builds the same grouping for
    that page, but around a DocumentType object this side has no business
    handing to a template it doesn't own.

    Tenant check is `Client.company_id`, the same join `get_order_or_404`
    uses, applied to the client id the URL supplied rather than trusting it.
    """
    orders = (
        Order.query.join(Client)
        .filter(Order.client_id == client_id, Client.company_id == company_id)
        .order_by(Order.due.desc())
        .all()
    )
    rows = []
    for order in orders:
        documents = documents_service.list_for_order(order.id)
        if not documents:
            continue
        rows.append({
            "id": order.id,
            "label": f"{order.item} — due {order.due.strftime('%b %d, %Y')}",
            "documents": [
                {
                    "id": document.id,
                    "filename": document.original_filename,
                    "size": document.size_bytes,
                    # "Other" is what the order page calls an untyped
                    # document, so the dropdown's groups read the same.
                    "type": (
                        document.document_type.label if document.document_type else "Other"
                    ),
                }
                for document in documents
            ],
        })
    return {"orders": rows, "has_orders": bool(orders)}


def _load_attachable_documents(company_id: int, document_ids: list[int]) -> list:
    """Turn picked document ids back into bytes the mail module can send.

    Every id is re-resolved against this company and anything that doesn't
    belong to it — or whose file has gone missing off disk — is dropped
    rather than raising: the ids arrive in a hidden form field, and one
    stale row shouldn't cost the user the message they just typed.
    """
    from communications.providers.base import OutgoingAttachment

    attachments = []
    for document_id in document_ids:
        document = documents_service.get_for_company(company_id, document_id)
        if document is None:
            continue
        data = documents_service.read_bytes(document)
        if data is None:
            continue
        attachments.append(OutgoingAttachment(
            filename=document.original_filename,
            content_type=document.content_type,
            data=data,
        ))
    return attachments


communications_routes.set_document_attachments(
    list_for_client=_attachable_documents,
    load=_load_attachable_documents,
)


def _thread_conversation(company_id: int, thread_id: int) -> dict | None:
    """One mail thread as the plain dict `ai/` speaks.

    This function is the whole reason `ai/` never imports `communications/`:
    the translation from an `EmailThread` into "subject plus a list of
    messages" happens here, in the composition root that already knows
    both sides. `ai/conversation.py` documents the shape.

    Three deliberate choices, all of them `ai/REQUIREMENTS.md` rules:

    - **`thread.messages` whole**, already ordered oldest-first by the
      relationship — not just the latest (`R-2`). Trimming to fit is the
      module's job, not this one's.
    - **`body_display`, not `body_text`** (`R-4`): every mail client quotes
      the entire prior conversation into a reply, and those messages are
      already in this list in their own right.
    - **`direction`, not `sender_label`**, as the source of who's speaking.
      `sender_label` renders our own mail as "You", which is right on a page
      a human reads and ambiguous in a prompt. `sender_display` is that same
      label without the substitution — so a form submission is attributed to
      the customer who wrote it rather than to the relay that carried it, and
      a draft opens "Hi Haejung" instead of greeting Squarespace.
    - **`contact_address`, not `counterparty`**, for the same reason.
    """
    thread = email_service.get_thread(company_id, thread_id)
    if thread is None:
        return None
    return {
        "subject": thread.display_subject,
        "counterparty": thread.contact_address,
        "messages": [
            {
                "sender": message.sender_display,
                "direction": message.direction,
                "sent_at": (
                    message.received_date.strftime("%Y-%m-%d")
                    if message.received_date else None
                ),
                "body": message.body_display,
            }
            for message in thread.messages
        ],
    }


def _load_order_document(company_id: int, order_id: int, document_id: int) -> dict | None:
    """One document's bytes for `ai/` to render from, or None.

    Tenant-checked twice over, and both checks are load-bearing: the
    document must belong to this order (documents' own scoping), and the
    row's `company_id` must match the session's — the order_id in the URL
    is user input, and this hook is reached without going through
    `get_order_or_404`.
    """
    document = documents_service.get_for_order(order_id, document_id)
    if document is None or document.company_id != company_id:
        return None
    data = documents_storage.read(document.company_id, document.stored_filename)
    if data is None:
        return None
    return {
        "filename": document.original_filename,
        "content_type": document.content_type,
        "data": data,
    }


def _save_rendered_document(company_id: int, order_id: int, filename: str,
                            content_type: str, data: bytes) -> str | None:
    """Store a render as a real document. Returns an error message, or None.

    Goes through `documents.services.upload()` rather than writing a row —
    so validation, content sniffing and the per-company storage quota all
    still apply to an image that came from a vendor exactly as they would to
    one someone dragged in.
    """
    result = documents_service.upload(company_id, order_id, [(filename, data)])
    if result.errors:
        return " ".join(result.errors)
    if not result.saved:
        return "The rendering couldn't be saved."
    return None


ai_routes.register(
    app,
    resolve_thread_context=_thread_conversation,
    load_document=_load_order_document,
    save_render=_save_rendered_document,
)


def _parse_amount(raw: str | None) -> float | None:
    """Money from a form field, or None if it's blank/not a number.

    Returning None rather than raising lets callers decide between "leave
    the existing value alone" and "reject the request".
    """
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def format_phone(raw: str | None) -> str:
    """Normalize a phone number to 416-555-0133 style on the way in.

    Formatting on save (not just on display) means every form that shows a
    phone number afterwards — client page, clients list, timeline modal —
    reads the same way with no per-template logic. A North American 10-digit
    number (however it was typed: dots, spaces, no separators, a leading "1")
    becomes XXX-XXX-XXXX; anything else (extensions, international numbers)
    is left exactly as entered rather than mangled by a guess.
    """
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"{digits[0:3]}-{digits[3:6]}-{digits[6:10]}"
    return raw.strip()


# Registered so templates can normalize phone numbers saved before this
# formatting existed, not just ones entered after — `format_phone` is applied
# on save too (see new_client/edit_client/new_order), but a filter covers
# whatever's already sitting in the database unformatted.
app.jinja_env.filters["phone"] = format_phone


def back_label(return_to: str) -> str:
    """Wording for a detail page's "← Back" link.

    Client/order pages used to be reachable only from the timeline, so the
    label could be hardcoded. They're linked from the invoice list now too,
    and a link that says "Back to timeline" but doesn't go there is worse
    than a generic one.

    Matched on the path alone: a return_to can carry a query string (the
    hidden-clients view is `/clients?hidden=1`, and every sort link adds
    `?sort=`), and every one of those used to fall through to a bare
    "Back".
    """
    return_to = return_to.split("?", 1)[0]
    if return_to == "/" or return_to.startswith("/timeline"):
        return "Back to timeline"
    if return_to.startswith("/invoices"):
        return "Back to invoices"
    if return_to == "/orders":
        return "Back to orders"
    if return_to == "/clients":
        return "Back to clients"
    if return_to.startswith("/clients/"):
        return "Back to client"
    if return_to.startswith("/orders/"):
        return "Back to order"
    return "Back"


def get_client_or_404(client_id: int) -> Client:
    client = Client.query.filter_by(
        id=client_id, company_id=current_user.company_id
    ).first()
    if client is None:
        abort(404)
    return client


# ---------------------------------------------------------------------------
# Auth
#
# Two kinds of signed-in user, and they don't overlap (see `User` in
# models.py): a tenant user has a company and sees the app; platform staff
# have no company and see only /admin. The guard below is what makes the
# second half true — one hook rather than a `company_id is not None` check
# threaded through 155 call sites, and it fails closed, so a route added
# tomorrow is covered without anyone remembering to cover it.
# ---------------------------------------------------------------------------

# Endpoints reachable without a company. Everything else in the app needs a
# `company_id` to answer at all — "which orders?" has no meaning for
# somebody who isn't in a studio.
_COMPANYLESS_ENDPOINTS = {"login", "logout", "privacy", "terms", "static"}


def _landing_page() -> str:
    """Where a signed-in user belongs when no particular page was asked for."""
    if current_user.is_authenticated and current_user.is_staff:
        return url_for("admin.companies")
    return url_for("timeline_view")


@app.before_request
def _keep_staff_out_of_tenant_routes():
    """Send platform staff back to /admin from any tenant route.

    A redirect rather than a 403: for this user the timeline isn't
    forbidden, it's meaningless — there's no company whose orders it could
    show. They're already trusted with more than the page would reveal, so
    there's nothing to withhold, and landing them on the one page that does
    have an answer beats an error.

    Impersonation passes straight through, because `current_user` is then
    the tenant user being impersonated and has a company like anyone else.
    """
    if not current_user.is_authenticated or current_user.is_tenant_user:
        return None
    endpoint = request.endpoint
    # No endpoint means no route matched. Let Flask 404 it: turning an
    # unknown URL into "here's the admin page" would hide typos and make
    # a deleted route look like it still half-exists.
    if endpoint is None:
        return None
    if endpoint in _COMPANYLESS_ENDPOINTS or endpoint.startswith("admin."):
        return None
    return redirect(url_for("admin.companies"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(_landing_page())

    error = None
    if request.method == "POST":
        # Normalised through the same helper that writes the column, so a
        # capitalised address signs in rather than mystifying its owner.
        email = normalise_email(request.form.get("email", ""))
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user is None or not user.check_password(password):
            # One message for "no such address" and "wrong password", as
            # before: telling them apart tells a stranger which addresses
            # have accounts on this installation.
            error = "Incorrect email or password."
        elif not user.is_active or (
            user.company is not None and not user.company.is_active
        ):
            # Said plainly, and deliberately not folded into the message
            # above. The credentials were right — someone who's been
            # switched off needs to know to ask, not to keep retyping.
            # Which of the two is off stays unsaid: it's the same phone
            # call either way.
            error = "That account is no longer active. Contact your administrator."
        else:
            login_user(user)
            # `next` is honoured for a tenant user only. Platform staff have
            # no timeline to be sent back to, and a bookmarked tenant URL
            # would bounce off the guard below into a redirect loop.
            next_url = request.args.get("next") if user.is_tenant_user else None
            return redirect(next_url or _landing_page())

    return render_template("login.html", error=error, active_view=None)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Legal pages
#
# Deliberately NOT @login_required, and deliberately the only routes besides
# /login that aren't: Google's OAuth verification requires the privacy policy
# URL to be publicly reachable and on the same domain as the app, and a
# reviewer hitting a sign-in wall fails the review. They read nothing from the
# database, so there's no tenant to filter on.
#
# LEGAL_UPDATED is the date shown on both pages. Bump it in the same commit as
# any wording change — a policy whose date doesn't move is a policy nobody can
# tell has changed.
# ---------------------------------------------------------------------------

LEGAL_UPDATED = "8 August 2026"


@app.route("/privacy")
def privacy():
    return render_template("privacy.html", updated=LEGAL_UPDATED, active_view=None)


@app.route("/terms")
def terms():
    return render_template("terms.html", updated=LEGAL_UPDATED, active_view=None)


# ---------------------------------------------------------------------------
# Calendar view — month grid via Python's stdlib calendar module
# (calendar.Calendar), no external dependency. Orders shown as chips on
# their due date.
# ---------------------------------------------------------------------------

@app.route("/calendar")
@login_required
def calendar_view():
    today = date.today()
    return month_view(today.year, today.month)


@app.route("/month/<int:year>/<int:month>")
@login_required
def month_view(year: int, month: int):
    if not (1 <= month <= 12):
        abort(404)

    cal = Calendar(firstweekday=6)  # Sunday-first, feels right for a paper ledger
    weeks = cal.monthdayscalendar(year, month)
    # Google Calendar events, read from the local mirror rather than from
    # Google — this view renders on every page load and mustn't depend on a
    # third-party round trip (see communications/services/calendar_service.py).
    # Empty for a company with no calendar connected, so the grid is just
    # empty days for anyone not using the integration. This calendar shows
    # only synced appointments, not orders — orders live on the timeline.
    events = calendar_service.events_by_day(current_user.company_id, year, month)
    today = date.today()

    # weeks_data: list of weeks, each week a list of (day_number, [events]) or None for padding
    weeks_data = []
    for week in weeks:
        week_data = []
        for day in week:
            if day == 0:
                week_data.append(None)
            else:
                week_data.append({
                    "day": day,
                    "events": events.get(day, []),
                    "is_today": date(year, month, day) == today,
                })
        weeks_data.append(week_data)

    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)

    # Each event once, however many days it spans — one edit dialog per event,
    # not one per cell it appears in. Same reasoning as the timeline's
    # deduplicated clients_in_view.
    events_in_view = list({event.id: event for day in events.values() for event in day}.values())
    month_total = len(events_in_view)

    return render_template(
        "calendar.html",
        weeks=weeks_data,
        month_name=month_name[month],
        year=year,
        month=month,
        prev_month=prev_month, prev_year=prev_year,
        next_month=next_month, next_year=next_year,
        month_total=month_total,
        today_year=today.year, today_month=today.month,
        # Adding an event needs somewhere to add it to. False means the studio
        # hasn't connected a calendar, so the template shows no event UI at all
        # rather than a button that can only fail.
        has_calendar=calendar_service.has_calendar(current_user.company_id),
        events_in_view=events_in_view,
        clients=Client.query.filter_by(company_id=current_user.company_id)
            .order_by(Client.first_name, Client.last_name).all(),
        default_date=today.replace(year=year, month=month, day=1)
            if (year, month) != (today.year, today.month) else today,
        # The event forms post into the communications blueprint, which reports
        # back through the module's own one-shot notice.
        notice=communications_routes.take_notice(),
        active_view="calendar",
    )


# ---------------------------------------------------------------------------
# Timeline (Gantt-style) view — multiple weeks at once, one row per order,
# client name on the left, a bar spanning start -> due for each order.
# Clicking a client name or a bar opens a quick-view/edit modal (see
# timeline.html); each modal links out to the full client/order page.
# This is the app's default view (see "/" below).
# ---------------------------------------------------------------------------

TIMELINE_WEEKS = 8       # weeks visible at once
TIMELINE_STEP_WEEKS = 4  # how far prev/next moves (half the window, so views overlap)


def _sunday_on_or_before(d: date) -> date:
    """Snap a date back to the most recent Sunday, matching the calendar's
    Sunday-first week convention (Calendar(firstweekday=6) in month_view)."""
    offset = (d.weekday() + 1) % 7  # Python: Monday=0 ... Sunday=6
    return d - timedelta(days=offset)


@app.route("/")
@login_required
def timeline_view():
    start = _sunday_on_or_before(date.today())
    return timeline_window(start.year, start.month, start.day)


@app.route("/timeline/<int:year>/<int:month>/<int:day>")
@login_required
def timeline_window(year: int, month: int, day: int):
    try:
        requested = date(year, month, day)
    except ValueError:
        abort(404)

    window_start = _sunday_on_or_before(requested)
    window_days = TIMELINE_WEEKS * 7
    window_end = window_start + timedelta(days=window_days - 1)  # inclusive

    week_headers = [window_start + timedelta(days=w * 7) for w in range(TIMELINE_WEEKS)]

    # Cancelled orders leave the schedule entirely — the timeline is for
    # work that still needs studio time, and a called-off order needs none.
    # It stays on /orders and on the client's record; it just stops taking
    # up a row here. Delivered orders keep their row: they only drop out
    # when the window moves past them, same as always.
    all_orders = (
        Order.query.join(Client)
        .filter(
            Client.company_id == current_user.company_id,
            Order.status != "cancelled",
        )
        .order_by(Order.start)
        .all()
    )

    rows = []
    clients_seen: dict[int, Client] = {}  # preserves first-appearance order, deduped
    for order in all_orders:
        # Skip orders that don't overlap the visible window at all
        if order.due < window_start or order.start > window_end:
            continue

        clipped_start = max(order.start, window_start)
        clipped_end = min(order.due, window_end)
        col_start = (clipped_start - window_start).days + 1  # 1-indexed CSS grid column
        span = (clipped_end - clipped_start).days + 1

        rows.append({
            "id": order.id,
            "client": order.client,
            "item": order.item,
            "total": order.total,
            "start": order.start,
            "due": order.due,
            "status": order.status,
            # What the bar is labelled/coloured by — `confirmed` reads as
            # "In progress" once its start date has arrived (see
            # Order.display_status). The raw `status` above is what the
            # edit form posts back, so both travel together.
            "display_status": order.display_status,
            "is_rush": order.is_rush,
            "can_rush": order.can_rush,
            "can_delete": order.can_delete,
            "can_cancel": order.can_cancel,
            "is_active": order.is_active,
            "order_type": order.order_type,
            "notes": order.notes,
            "col_start": col_start,
            "span": span,
            "truncated_start": order.start < window_start,
            "truncated_end": order.due > window_end,
        })
        clients_seen.setdefault(order.client.id, order.client)

    step_days = TIMELINE_STEP_WEEKS * 7
    prev_start = window_start - timedelta(days=step_days)
    next_start = window_start + timedelta(days=step_days)

    return render_template(
        "timeline.html",
        window_start=window_start,
        window_end=window_end,
        week_headers=week_headers,
        window_days=window_days,
        rows=rows,
        clients_in_view=list(clients_seen.values()),
        prev_start=prev_start,
        next_start=next_start,
        status_labels=STATUS_LABELS,
        active_view="timeline",
    )


# ---------------------------------------------------------------------------
# Orders & clients list pages. Full rosters, sortable by clicking a column
# header — unlike the timeline (which is windowed to a few weeks and
# filters/sorts client-side, see timeline.html), these show everything at
# once, so sorting happens server-side via ?sort=&dir= and a real page
# load. Sorting on a computed property (total, balance_due, lifetime_value)
# means fetching everything and sorting in Python rather than in SQL —
# fine at this table's scale, revisit if the row count ever gets large.
# ---------------------------------------------------------------------------

# Sorting the Status column alphabetically would run cancelled, confirmed,
# delivered, ready, tentative — an order nobody thinks in. STATUS_LABELS is
# already declared in lifecycle order, so its key order *is* the sort.
_STATUS_SORT = {key: i for i, key in enumerate(STATUS_LABELS)}

ORDER_SORT_KEYS = {
    "item": lambda o: o.item.lower(),
    "client": lambda o: o.client.name.lower(),
    "type": lambda o: o.order_type.label.lower() if o.order_type else "",
    "status": lambda o: _STATUS_SORT.get(o.display_status, len(_STATUS_SORT)),
    "start": lambda o: o.start,
    "due": lambda o: o.due,
    "total": lambda o: o.total,
    "paid": lambda o: o.amount_paid,
    "balance": lambda o: o.balance_due,
}

# The client page's Orders tab is a single client's own roster, so it uses a
# fixed column set (no per-company customization like ORDER_COLUMNS) plus one
# column that list doesn't have — Invoice, since "did this order get billed"
# reads naturally sitting next to a single client's orders in a way it
# wouldn't in the company-wide list (that already has its own /invoices page).
CLIENT_ORDER_SORT_KEYS = {
    "item": lambda o: o.item.lower(),
    "status": lambda o: _STATUS_SORT.get(o.display_status, len(_STATUS_SORT)),
    "start": lambda o: o.start,
    "due": lambda o: o.due,
    "total": lambda o: o.total,
    "paid": lambda o: o.amount_paid,
    "balance": lambda o: o.balance_due,
    "invoice": lambda o: o.invoice.number.lower() if o.invoice else "",
}

# Canonical Orders-list columns: key -> (label, numeric). This dict is both
# the fallback order for a company that's never saved a preference and the
# whitelist a saved preference is filtered against — a key removed from here
# later just drops out of anyone's stored JSON instead of erroring.
ORDER_COLUMNS = {
    "item": ("Item", False),
    "client": ("Client", False),
    "type": ("Type", False),
    "status": ("Status", False),
    "start": ("Start", False),
    "due": ("Due", False),
    "total": ("Total", True),
    "paid": ("Paid", True),
    "balance": ("Balance", True),
}


def _order_columns_for(company_id: int) -> list[dict]:
    """This company's Orders-list columns, in the order/visibility it saved.

    Stored as one JSON blob on Company.order_columns rather than a table like
    SourceOption/OrderType — this is a fixed set of 9 known columns, not an
    open-ended list a user names, so there's nothing per-row to hide-not-delete.
    Merges the saved list against ORDER_COLUMNS so a column added to the app
    later appears (visible, appended at the end) for a company with an older
    saved blob, and a column since removed from the app silently drops out
    instead of erroring.
    """
    company = db.session.get(Company, company_id)
    saved = []
    if company.order_columns:
        try:
            saved = json.loads(company.order_columns)
        except (ValueError, TypeError):
            saved = []
    seen = set()
    columns = []
    for entry in saved:
        key = entry.get("key") if isinstance(entry, dict) else None
        if key in ORDER_COLUMNS and key not in seen:
            seen.add(key)
            label, numeric = ORDER_COLUMNS[key]
            columns.append({"key": key, "label": label, "numeric": numeric, "visible": bool(entry.get("visible", True))})
    for key, (label, numeric) in ORDER_COLUMNS.items():
        if key not in seen:
            columns.append({"key": key, "label": label, "numeric": numeric, "visible": True})
    return columns


def _save_order_columns(company_id: int, columns: list[dict]) -> None:
    company = db.session.get(Company, company_id)
    company.order_columns = json.dumps([
        {"key": c["key"], "visible": c["visible"]} for c in columns
    ])
    db.session.commit()


# Canonical Analytics layout: section key -> (heading, ordered card keys).
# Same role as ORDER_COLUMNS above — the fallback order for a company that's
# never dragged anything, and the whitelist a saved layout is filtered
# against. A card belongs to exactly one section and can't be dragged into
# another: "Clients" and "Revenue" are what the headings promise, and a card
# under the wrong one would be a mislabelled stat, not a preference.
ANALYTICS_SECTIONS = {
    "clients": ("Clients", ("avg_value", "top_clients", "sources")),
    "revenue": ("Revenue", ("total_revenue", "revenue_ytd", "outstanding",
                            "by_method", "tax_ytd")),
}


def _analytics_layout_for(company_id: int) -> list[dict]:
    """This company's Analytics sections and cards, in the order it saved.

    Stored as one JSON blob on Company.analytics_layout, for the same reason
    ORDER_COLUMNS is: a fixed set of known keys, not an open-ended list a user
    names. Merged against ANALYTICS_SECTIONS the same way too, so a section or
    card added to the app later appears (at the end) for a company with an
    older saved blob, and one since removed silently drops out instead of
    erroring.
    """
    company = db.session.get(Company, company_id)
    saved = []
    if company.analytics_layout:
        try:
            saved = json.loads(company.analytics_layout)
        except (ValueError, TypeError):
            saved = []

    saved_cards = {
        entry["key"]: entry.get("cards", [])
        for entry in saved
        if isinstance(entry, dict) and entry.get("key") in ANALYTICS_SECTIONS
    }
    section_order = [key for key in saved_cards]
    section_order += [key for key in ANALYTICS_SECTIONS if key not in saved_cards]

    layout = []
    for key in section_order:
        heading, canonical_cards = ANALYTICS_SECTIONS[key]
        cards = [c for c in dict.fromkeys(saved_cards.get(key, []))
                 if c in canonical_cards]
        cards += [c for c in canonical_cards if c not in cards]
        layout.append({"key": key, "heading": heading, "cards": cards})
    return layout


def _save_analytics_layout(company_id: int, layout: list[dict]) -> None:
    company = db.session.get(Company, company_id)
    company.analytics_layout = json.dumps([
        {"key": s["key"], "cards": s["cards"]} for s in layout
    ])
    db.session.commit()


CLIENT_SORT_KEYS = {
    "name": lambda c: c.name.lower(),
    "orders": lambda c: len(c.orders),
    "value": lambda c: c.lifetime_value,
}


def _sort_args(valid_keys: dict, default_key: str) -> tuple[str, str]:
    sort_by = request.args.get("sort", default_key)
    if sort_by not in valid_keys:
        sort_by = default_key
    sort_dir = "desc" if request.args.get("dir") == "desc" else "asc"
    return sort_by, sort_dir


@app.route("/orders")
@login_required
def orders_list():
    orders = (
        Order.query.join(Client)
        .filter(Client.company_id == current_user.company_id)
        .all()
    )
    sort_by, sort_dir = _sort_args(ORDER_SORT_KEYS, "due")
    orders.sort(key=ORDER_SORT_KEYS[sort_by], reverse=(sort_dir == "desc"))
    # Any type at all, not just active ones — a hidden type an order still
    # references is exactly the case the Type column needs to keep showing.
    has_order_types = (
        OrderType.query.filter_by(company_id=current_user.company_id).first() is not None
    )
    # A column can be hidden in settings, and Type additionally only ever
    # shows when there's a type to show — same "don't show empty" rule the
    # dropdown on the order form already follows.
    columns = [
        c for c in _order_columns_for(current_user.company_id)
        if c["visible"] and (c["key"] != "type" or has_order_types)
    ]
    return render_template(
        "orders_list.html",
        orders=orders,
        status_labels=STATUS_LABELS,
        has_order_types=has_order_types,
        columns=columns,
        sort_by=sort_by,
        sort_dir=sort_dir,
        active_view="orders",
    )


@app.route("/clients")
@login_required
def clients_list():
    # Seeing the roster is what settles the "a client appeared by itself"
    # badge — the client is already there, so looking at it is the whole of
    # the response required. (Unlike the lead badge, which counts work.)
    sender_rules.acknowledge_all(current_user.company_id)
    # Hidden clients are filtered server-side rather than client-side like
    # the orders filter below: that one is a view of rows already on the
    # page, this one is "these aren't part of the roster any more" and
    # shouldn't be shipped to the browser at all. ?hidden=1 is the archive
    # view — only the hidden ones, so the two never mix into one list
    # someone has to read a marker to tell apart.
    showing_hidden = request.args.get("hidden") == "1"
    clients = Client.query.filter_by(
        company_id=current_user.company_id, is_hidden=showing_hidden
    ).all()
    hidden_count = Client.query.filter_by(
        company_id=current_user.company_id, is_hidden=True
    ).count()
    # One grouped query for the whole table, not a count per row.
    unread_by_client = email_service.unread_counts_by_client(current_user.company_id)
    sort_by, sort_dir = _sort_args(CLIENT_SORT_KEYS, "name")
    clients.sort(key=CLIENT_SORT_KEYS[sort_by], reverse=(sort_dir == "desc"))
    return render_template(
        "clients_list.html",
        clients=clients,
        unread_by_client=unread_by_client,
        showing_hidden=showing_hidden,
        hidden_count=hidden_count,
        # Both groups start shown and every client stays in the DOM; the
        # filter buttons hide a group client-side — see clients_list.html's
        # script. This just tells the template whether the filter is worth
        # rendering: with every client in the same group, hiding it would
        # only ever empty the table.
        show_order_filter=(
            any(c.orders for c in clients) and any(not c.orders for c in clients)
        ),
        sort_by=sort_by,
        sort_dir=sort_dir,
        active_view="clients",
    )


@app.route("/clients/new", methods=["GET", "POST"])
@login_required
def new_client():
    """Standalone client creation — the "+ Add client" button on /clients.

    Separate from the inline "+ Add new client" option in new_order.html
    (which creates a client as a side effect of placing an order): this is
    for a client with no order yet, e.g. someone just inquiring. Only
    collects the basics; address and sources are edited from the full
    client page afterwards, same "quick create, fill in detail later"
    split as new orders.
    """
    return_to = request.values.get("return_to") or url_for("clients_list")

    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        if not first_name or not last_name:
            abort(400)
        client = Client(
            company_id=current_user.company_id,
            first_name=first_name,
            last_name=last_name,
            email=request.form.get("email", "").strip(),
            phone=format_phone(request.form.get("phone", "")),
        )
        db.session.add(client)
        db.session.commit()
        return redirect(return_to)

    return render_template(
        "new_client.html",
        return_to=return_to,
        back_label=back_label(return_to),
        active_view=None,
    )


# ---------------------------------------------------------------------------
# Client & order detail pages. Reachable directly, or via "view full
# profile / open full order page" links inside the timeline's modals.
# `return_to` carries the visitor back to whichever timeline window they
# came from, instead of always bouncing to today's window.
# ---------------------------------------------------------------------------

@app.route("/clients/<int:client_id>")
@login_required
def client_page(client_id: int):
    """The client page's "Information" tab — see client_orders() for the
    other tab. Kept at the bare /clients/<id> URL (rather than e.g.
    /clients/<id>/info) since every other page's links to a client already
    point here and there's no reason to churn all of them."""
    client = get_client_or_404(client_id)
    return_to = request.args.get("return_to") or url_for("timeline_view")
    # Selectable options = active ones, plus any now-hidden option this
    # client already has (so existing data stays visible), deduped and
    # ordered by sort_order.
    active_options = SourceOption.query.filter_by(
        company_id=current_user.company_id, is_active=True
    ).all()
    source_options = sorted(
        {o.id: o for o in active_options + client.sources}.values(),
        key=lambda o: o.sort_order,
    )
    other_source_option = next((o for o in source_options if o.is_other), None)
    return render_template(
        "client_page.html",
        section="info",
        client=client,
        return_to=return_to,
        back_label=back_label(return_to),
        source_options=source_options,
        other_source_option=other_source_option,
        provinces=PROVINCES,
        active_view=None,
    )


@app.route("/clients/<int:client_id>/orders")
@login_required
def client_orders(client_id: int):
    client = get_client_or_404(client_id)
    return_to = request.args.get("return_to") or url_for("timeline_view")
    orders = list(client.orders)
    sort_by, sort_dir = _sort_args(CLIENT_ORDER_SORT_KEYS, "due")
    orders.sort(key=CLIENT_ORDER_SORT_KEYS[sort_by], reverse=(sort_dir == "desc"))
    return render_template(
        "client_page.html",
        section="orders",
        client=client,
        orders=orders,
        return_to=return_to,
        back_label=back_label(return_to),
        status_labels=STATUS_LABELS,
        sort_by=sort_by,
        sort_dir=sort_dir,
        active_view=None,
    )


@app.route("/clients/<int:client_id>/edit", methods=["POST"])
@login_required
def edit_client(client_id: int):
    client = get_client_or_404(client_id)
    client.first_name = request.form.get("first_name", "").strip() or client.first_name
    client.last_name = request.form.get("last_name", "").strip() or client.last_name
    client.email = request.form.get("email", "").strip()
    client.phone = format_phone(request.form.get("phone", ""))
    # Notes and address are only on the full client page, not the timeline's
    # quick-edit modal — absent from the form means "not shown", not
    # "cleared". The province gate matters more than it looks: it decides
    # what tax this client is charged (see taxes_for in models.py).
    if "notes" in request.form:
        client.notes = request.form.get("notes", "").strip()
    if "street" in request.form:
        client.street = request.form.get("street", "").strip() or None
        client.city = request.form.get("city", "").strip() or None
        province = request.form.get("province", "").strip().upper()
        client.province = province if province in PROVINCES else None
        client.postal_code = request.form.get("postal_code", "").strip().upper() or None

    source_ids = [int(i) for i in request.form.getlist("source_ids") if i.isdigit()]
    client.sources = SourceOption.query.filter(
        SourceOption.id.in_(source_ids),
        SourceOption.company_id == current_user.company_id,
    ).all()

    # The free-text detail behind whichever source is marked is_other only
    # means anything while that option is actually checked — kept blank the
    # rest of the time rather than holding onto stale text nobody can see.
    if any(o.is_other for o in client.sources):
        client.other_source_detail = request.form.get("other_source_detail", "").strip() or None
    else:
        client.other_source_detail = None

    db.session.commit()
    return_to = request.form.get("return_to") or url_for("timeline_view")
    return redirect(return_to)


@app.route("/clients/<int:client_id>/hide", methods=["POST"])
@login_required
def toggle_client_hidden(client_id: int):
    """Take a client off the roster, or put them back.

    One toggle rather than a hide route and a show route, matching
    toggle_order_type / toggle_source_option — and there is no delete
    counterpart at all, unlike those two: a client is referenced by orders,
    invoices and email threads, so `can_delete` could never be true.

    Deliberately narrow. It writes one boolean and touches nothing else:
    the client's orders stay on the timeline, in /orders and in Analytics,
    their invoices stay issued, their mail stays matched. Hiding answers
    "do I still deal with this person", which is not a question the
    schedule or the books get to be re-written by.
    """
    client = get_client_or_404(client_id)
    client.is_hidden = not client.is_hidden
    db.session.commit()
    return_to = request.form.get("return_to") or url_for("clients_list")
    return redirect(return_to)


@app.route("/orders/new", methods=["GET", "POST"])
@login_required
def new_order():
    # Active clients only, the same way new_order() offers only active
    # OrderTypes (OT5): hiding is a statement about who the studio still
    # deals with, and the picker is exactly where that statement is worth
    # something. It's the *new* selection that's filtered — an order a
    # hidden client already has still shows their name everywhere.
    clients = (
        Client.query.filter_by(company_id=current_user.company_id, is_hidden=False)
        .order_by(Client.first_name, Client.last_name)
        .all()
    )
    # Active only — a brand-new order can't already be tagged with a
    # now-hidden type, unlike editing (see order_page()/edit_order()).
    order_types = (
        OrderType.query.filter_by(company_id=current_user.company_id, is_active=True)
        .order_by(OrderType.sort_order)
        .all()
    )
    return_to = request.values.get("return_to") or url_for("timeline_view")

    if request.method == "POST":
        client_id = request.form.get("client_id", "")
        if client_id == "new":
            first_name = request.form.get("new_first_name", "").strip()
            last_name = request.form.get("new_last_name", "").strip()
            if not first_name or not last_name:
                abort(400)
            client = Client(
                company_id=current_user.company_id,
                first_name=first_name,
                last_name=last_name,
                email=request.form.get("new_email", "").strip(),
                phone=format_phone(request.form.get("new_phone", "")),
            )
            db.session.add(client)
            db.session.flush()  # assigns client.id
        else:
            client = Client.query.filter_by(
                id=client_id if client_id.isdigit() else None,
                company_id=current_user.company_id,
            ).first()
            if client is None:
                abort(400)

        status = request.form.get("status")
        item = request.form.get("item", "").strip()
        order_type_id = request.form.get("order_type_id", "")
        order_type = (
            OrderType.query.filter_by(
                id=order_type_id, company_id=current_user.company_id, is_active=True
            ).first()
            if order_type_id.isdigit() else None
        )
        order = Order(
            client_id=client.id,
            item=item,
            start=date.fromisoformat(request.form.get("start")),
            due=date.fromisoformat(request.form.get("due")),
            status=status if status in INITIAL_STATUSES else "tentative",
            order_type_id=order_type.id if order_type else None,
            notes=request.form.get("notes", "").strip(),
        )
        db.session.add(order)
        db.session.flush()  # assigns order.id
        # An order's value comes from its lines, so a new order starts with
        # one covering the whole thing. Splitting it into several (materials,
        # surcharges, ...) happens on the full order page — same "quick form
        # here, detail over there" split as payments.
        db.session.add(OrderLine(
            order_id=order.id,
            description=item,
            quantity=1,
            unit_price=_parse_amount(request.form.get("price")) or 0.0,
            sort_order=0,
        ))
        db.session.commit()
        return redirect(return_to)

    return render_template(
        "new_order.html",
        clients=clients,
        order_types=order_types,
        status_labels=STATUS_LABELS,
        initial_statuses=INITIAL_STATUSES,
        return_to=return_to,
        back_label=back_label(return_to),
        today=date.today(),
        active_view=None,
    )


@app.route("/orders/<int:order_id>")
@login_required
def order_page(order_id: int):
    """The order page's "Details" tab — see order_billing() for the other
    tab. Kept at the bare /orders/<id> URL (rather than e.g. /orders/<id>/details)
    since every other page's links to an order already point here."""
    order = get_order_or_404(order_id)
    return_to = request.args.get("return_to") or url_for("timeline_view")
    # Same "active options ∪ whatever's already selected" pattern as the
    # client page's source checkboxes: a now-hidden type this order already
    # has stays selectable here (so saving the rest of the form doesn't
    # silently clear it), just isn't offered to other orders.
    active_types = OrderType.query.filter_by(
        company_id=current_user.company_id, is_active=True
    ).all()
    order_types = sorted(
        {t.id: t for t in active_types + ([order.order_type] if order.order_type else [])}.values(),
        key=lambda t: t.sort_order,
    )
    storage_used_bytes = documents_service.usage_for_company(current_user.company_id)
    has_document_types = documents_service.has_document_types(current_user.company_id)
    return render_template(
        "order_page.html",
        section="details",
        order=order,
        status_labels=STATUS_LABELS,
        order_types=order_types,
        return_to=return_to,
        back_label=back_label(return_to),
        active_view=None,
        # For the documents/_explorer.html partial included below. Flat
        # layout (documents) when no DocumentType exists for the company;
        # sectioned (sections) once at least one does — see has_document_types.
        has_document_types=has_document_types,
        documents=None if has_document_types else documents_service.list_for_order(order.id),
        sections=documents_service.sections_for_order(order.id, current_user.company_id)
            if has_document_types else None,
        documents_notice=documents_routes.take_notice(),
        storage_used_bytes=storage_used_bytes,
        storage_limit_bytes=documents_config.MAX_TOTAL_BYTES,
        storage_percent=min(100, round(storage_used_bytes / documents_config.MAX_TOTAL_BYTES * 100, 1)),
        inline_previewable_content_types=documents_config.INLINE_PREVIEWABLE_CONTENT_TYPES,
        allowed_extensions=documents_config.ALLOWED_EXTENSIONS,
    )


@app.route("/orders/<int:order_id>/billing")
@login_required
def order_billing(order_id: int):
    order = get_order_or_404(order_id)
    return_to = request.args.get("return_to") or url_for("timeline_view")
    return render_template(
        "order_page.html",
        section="billing",
        order=order,
        payment_method_labels=PAYMENT_METHOD_LABELS,
        invoice_status_labels=INVOICE_STATUS_LABELS,
        return_to=return_to,
        back_label=back_label(return_to),
        today=date.today(),
        active_view=None,
    )


@app.route("/orders/<int:order_id>/materials")
@login_required
def order_materials(order_id: int):
    """The order page's "Materials" tab — cost-tracking only, see
    inventory/__init__.py. Renders order_page.html same as order_billing()."""
    order = get_order_or_404(order_id)
    return_to = request.args.get("return_to") or url_for("timeline_view")
    return render_template(
        "order_page.html",
        section="materials",
        order=order,
        materials=inventory_service.list_materials_for_order(order.id),
        others=inventory_service.list_others_for_order(order.id),
        total_material_cost=inventory_service.total_material_cost(order.id),
        selectable_items=inventory_service.selectable_items(current_user.company_id, order.id),
        understocked_materials=inventory_service.understocked_materials_for_order(order.id),
        low_stock_materials=inventory_service.low_stock_materials_for_order(order.id),
        unit_labels=INVENTORY_UNIT_LABELS,
        unit_whole=INVENTORY_UNIT_WHOLE,
        return_to=return_to,
        back_label=back_label(return_to),
        active_view=None,
    )


@app.route("/orders/<int:order_id>/edit", methods=["POST"])
@login_required
def edit_order(order_id: int):
    order = get_order_or_404(order_id)
    order.item = request.form.get("item", "").strip() or order.item

    start_str = request.form.get("start")
    due_str = request.form.get("due")
    if start_str:
        order.start = date.fromisoformat(start_str)
    if due_str:
        order.due = date.fromisoformat(due_str)

    if "pickup_date" in request.form:
        pickup_str = request.form.get("pickup_date", "")
        order.pickup_date = date.fromisoformat(pickup_str) if pickup_str else None

    # Not "is this a real status" but "is this a legal move from here" —
    # otherwise the dropdown's own markup is the only thing stopping a
    # delivered order going back to tentative.
    status = request.form.get("status")
    if status and status != order.status:
        if status not in settable_statuses(order.status):
            abort(400)
        order.status = status

    # Rush travels with the modal's Save rather than as its own button: a
    # button in there would reload the page and lose whatever else was
    # being edited. Unchecked checkboxes post nothing, hence the marker
    # field — without it "unchecked" and "form didn't render this" look
    # identical, and hard rule 9 says those must not.
    #
    # Read after the status write on purpose: moving to `ready` clears the
    # flag even if the box came back ticked, and the `elif` catches the
    # same move arriving from a form that has no rush field at all.
    if "rush_field" in request.form:
        order.is_rush = order.can_rush and "is_rush" in request.form
    elif not order.can_rush:
        order.is_rush = False

    if "order_type_id" in request.form:
        order_type_id = request.form.get("order_type_id", "")
        order.order_type = (
            OrderType.query.filter_by(
                id=order_type_id, company_id=current_user.company_id
            ).first()
            if order_type_id.isdigit() else None
        )

    # Guarded, not unconditional: the timeline's quick-edit modal posts here
    # without a notes field (OR4 keeps notes on the full order page), and an
    # unguarded write blanked them. Hard rule 9.
    if "notes" in request.form:
        order.notes = request.form.get("notes", "").strip()

    db.session.commit()
    return_to = request.form.get("return_to") or url_for("timeline_view")
    return redirect(return_to)


# ---------------------------------------------------------------------------
# Lifecycle moves that aren't a plain field edit. Each re-checks the same
# `can_*` property the template used to decide whether to render its
# button: a hidden control is a UI convenience, not a permission check.
# ---------------------------------------------------------------------------

@app.route("/orders/<int:order_id>/delete", methods=["POST"])
@login_required
def delete_order(order_id: int):
    """Hard delete — the one place the app does this to an order.

    Only reachable while `can_delete`: tentative, with no invoice, payment
    or material attached. Anything further along has history worth keeping
    and gets cancelled instead (hard rule 8).
    """
    order = get_order_or_404(order_id)
    if not order.can_delete:
        abort(400)

    # Lines and payments cascade off the relationships; an invoice and any
    # stock-drawing materials can't exist here (can_delete says so). What
    # doesn't cascade is anything a *module* owns by plain order_id — Order
    # has no relationship to reach it with, so each module is asked to
    # clean up its own through its services (hard rule 4). Documents matter
    # because they leave bytes on disk, not just rows; one-off "Other"
    # costs are plain rows with no stock behind them.
    for document in documents_service.list_for_order(order.id):
        documents_service.delete(document)
    for other in inventory_service.list_others_for_order(order.id):
        inventory_service.delete_other(order.id, other.id)

    db.session.delete(order)
    db.session.commit()
    return redirect(request.form.get("return_to") or url_for("timeline_view"))


@app.route("/orders/<int:order_id>/cancel", methods=["POST"])
@login_required
def cancel_order(order_id: int):
    """Call off a real order, recording why.

    The reason is appended to `notes` rather than kept in a column of its
    own: it's a sentence a person writes and a person reads, and nothing
    queries it. Dated on the way in, because "client changed their mind"
    is worth much less in a year without knowing when.
    """
    order = get_order_or_404(order_id)
    if not order.can_cancel:
        abort(400)

    order.status = "cancelled"
    order.is_rush = False  # urgency is meaningless once nothing is owed

    reason = request.form.get("reason", "").strip()
    if reason:
        entry = f"Cancelled {date.today().isoformat()}: {reason}"
        order.notes = f"{order.notes}\n\n{entry}" if order.notes else entry

    db.session.commit()
    return redirect(request.form.get("return_to") or url_for("timeline_view"))


@app.route("/orders/<int:order_id>/rush", methods=["POST"])
@login_required
def toggle_rush(order_id: int):
    """Flag/unflag a time-sensitive order. `confirmed` only — see
    `Order.can_rush` for why `ready` doesn't qualify."""
    order = get_order_or_404(order_id)
    if not order.can_rush:
        abort(400)

    order.is_rush = not order.is_rush
    db.session.commit()
    return redirect(request.form.get("return_to") or url_for("timeline_view"))


# ---------------------------------------------------------------------------
# Line items. An order's value is the sum of these (Order.total), so this
# is where a price actually gets set — the order form itself only ever
# creates/edits the one line it starts with.
# ---------------------------------------------------------------------------

@app.route("/orders/<int:order_id>/lines", methods=["POST"])
@login_required
def add_order_line(order_id: int):
    order = get_order_or_404(order_id)
    description = request.form.get("description", "").strip()
    unit_price = _parse_amount(request.form.get("unit_price"))
    quantity = request.form.get("quantity", "1")
    if description and unit_price is not None:
        db.session.add(OrderLine(
            order_id=order.id,
            description=description,
            quantity=int(quantity) if quantity.isdigit() and int(quantity) > 0 else 1,
            unit_price=unit_price,
            sort_order=len(order.lines),
        ))
        db.session.commit()
    return_to = request.form.get("return_to") or url_for("timeline_view")
    return redirect(return_to)


@app.route("/orders/<int:order_id>/lines/<int:line_id>/delete", methods=["POST"])
@login_required
def delete_order_line(order_id: int, line_id: int):
    order = get_order_or_404(order_id)
    line = OrderLine.query.filter_by(id=line_id, order_id=order.id).first()
    if line is not None:
        db.session.delete(line)
        db.session.commit()
    return_to = request.form.get("return_to") or url_for("timeline_view")
    return redirect(return_to)


@app.route("/orders/<int:order_id>/payments", methods=["POST"])
@login_required
def add_payment(order_id: int):
    order = get_order_or_404(order_id)
    amount = _parse_amount(request.form.get("amount"))
    paid_date_str = request.form.get("paid_date")
    if amount is not None and paid_date_str:
        method = request.form.get("method")
        db.session.add(Payment(
            order_id=order.id,
            amount=amount,
            paid_date=date.fromisoformat(paid_date_str),
            method=method if method in PAYMENT_METHOD_LABELS else "cash",
            reference=request.form.get("reference", "").strip() or None,
        ))
        db.session.commit()
    return_to = request.form.get("return_to") or url_for("timeline_view")
    return redirect(return_to)


@app.route("/orders/<int:order_id>/payments/<int:payment_id>/delete", methods=["POST"])
@login_required
def delete_payment(order_id: int, payment_id: int):
    order = get_order_or_404(order_id)
    payment = Payment.query.filter_by(id=payment_id, order_id=order.id).first()
    if payment is not None:
        db.session.delete(payment)
        db.session.commit()
    return_to = request.form.get("return_to") or url_for("timeline_view")
    return redirect(return_to)


# ---------------------------------------------------------------------------
# Settings — currently just the per-company "how did you hear about us"
# options shown as checkboxes on the client page. Options are never
# hard-deleted once a client references one (see SourceOption.can_delete in
# models.py) so historical answers/stats stay intact; hiding is the only way
# to retire one from the checkbox list.
# ---------------------------------------------------------------------------

def get_source_option_or_404(source_option_id: int) -> SourceOption:
    option = SourceOption.query.filter_by(
        id=source_option_id, company_id=current_user.company_id
    ).first()
    if option is None:
        abort(404)
    return option


def get_order_type_or_404(order_type_id: int) -> OrderType:
    order_type = OrderType.query.filter_by(
        id=order_type_id, company_id=current_user.company_id
    ).first()
    if order_type is None:
        abort(404)
    return order_type


def _flash_settings_notice(message: str) -> None:
    """One-shot message for the next settings page render.

    Same session-key-scoped shape as documents' _flash/take_notice and
    communications' _flash/_take_notice — this app has no app-wide flash
    convention, so each part that needs one keeps its own key rather than
    introducing Flask's flash() globally.
    """
    session["settings_notice"] = message


def _take_settings_notice() -> str | None:
    return session.pop("settings_notice", None)


def _is_duplicate_label(model, company_id: int, label: str) -> bool:
    """Case-insensitive match against every row this company has for
    `model` (active and hidden both — a hidden row is still a real one
    someone could unhide back into a naming collision)."""
    normalized = label.strip().lower()
    return any(
        row.label.strip().lower() == normalized
        for row in model.query.filter_by(company_id=company_id)
    )


@app.route("/settings")
@login_required
def settings():
    # No content of its own — just lands on the first category. Bookmarks
    # and the nav's "Settings" link both go through here.
    return redirect(url_for("settings_general"))


@app.route("/settings/general")
@login_required
def settings_general():
    return render_template(
        "settings.html",
        section="general",
        company=db.session.get(Company, current_user.company_id),
        time_zones=TIME_ZONES,
        active_view="settings",
    )


@app.route("/settings/orders")
@login_required
def settings_orders():
    order_types = (
        OrderType.query.filter_by(company_id=current_user.company_id)
        .order_by(OrderType.sort_order)
        .all()
    )
    # Same "any type at all, not just active" rule as orders_list()'s
    # has_order_types — with none, the Type column can never show on the
    # Orders list regardless of its saved visibility, so there's nothing for
    # this editor to offer a row for either.
    has_order_types = bool(order_types)
    return render_template(
        "settings.html",
        section="orders",
        company=db.session.get(Company, current_user.company_id),
        order_types=order_types,
        # Every column, not just visible ones — this editor is what turns
        # visibility back on, so a hidden column has to still render its row.
        # Type is the one exception: it's excluded entirely with no order
        # types on file, matching the Orders list's own "don't show empty" rule.
        order_columns=[
            c for c in _order_columns_for(current_user.company_id)
            if c["key"] != "type" or has_order_types
        ],
        # For the documents/_settings_types.html partial included below.
        document_types=documents_service.list_document_types(current_user.company_id),
        documents_notice=documents_routes.take_notice(),
        notice=_take_settings_notice(),
        active_view="settings",
    )


@app.route("/settings/inventory")
@login_required
def settings_inventory():
    return render_template(
        "settings.html",
        section="inventory",
        company=db.session.get(Company, current_user.company_id),
        units=inventory_service.list_units(current_user.company_id),
        available_units=inventory_service.available_catalog_units(current_user.company_id),
        inventory_types=inventory_service.list_types(current_user.company_id),
        # Every column, not just the visible ones — this editor is what turns
        # visibility back on, so a hidden column still needs its row (same as
        # settings_orders' order_columns).
        inventory_columns=inventory_service.list_columns(current_user.company_id),
        notice=_take_settings_notice(),
        active_view="settings",
    )


@app.route("/settings/clients")
@login_required
def settings_clients():
    source_options = (
        SourceOption.query.filter_by(company_id=current_user.company_id)
        .order_by(SourceOption.sort_order)
        .all()
    )
    return render_template(
        "settings.html",
        section="clients",
        company=db.session.get(Company, current_user.company_id),
        source_options=source_options,
        notice=_take_settings_notice(),
        active_view="settings",
    )


@app.route("/settings/invoicing")
@login_required
def settings_invoicing():
    company = db.session.get(Company, current_user.company_id)
    # The letterhead belongs to the billing module; this page just edits it.
    profile = invoicing.profile_for(company.id, company.name)
    db.session.commit()  # profile_for creates one on first visit
    return render_template(
        "settings.html",
        section="invoicing",
        company=company,
        profile=profile,
        provinces=PROVINCES,
        next_number=invoicing.next_number(company.id, company.name),
        active_view="settings",
    )


@app.route("/settings/general", methods=["POST"])
@login_required
def update_preferences():
    """The company's display timezone.

    Only affects rendering — stored timestamps stay naive UTC (see the
    communications module), so switching zones re-labels history rather than
    rewriting it. Anything outside TIME_ZONES is ignored rather than stored:
    an unknown zone name would make every time on the page fall back to UTC
    with nothing to say why.
    """
    company = db.session.get(Company, current_user.company_id)
    chosen = request.form.get("timezone", "").strip()
    if chosen in dict(TIME_ZONES):
        company.timezone = chosen
        db.session.commit()
    return redirect(url_for("settings_general"))


# Order types (Custom Order / White Label / Consulting-Sampling, or whatever
# a given studio calls its own categories) — same hide-don't-delete shape and
# add/toggle/delete routes as SourceOption below, just a different table.
@app.route("/settings/order-types", methods=["POST"])
@login_required
def add_order_type():
    label = request.form.get("label", "").strip()
    if label and _is_duplicate_label(OrderType, current_user.company_id, label):
        _flash_settings_notice(f'An order type called "{label}" already exists.')
    elif label:
        max_sort_order = (
            OrderType.query.filter_by(company_id=current_user.company_id)
            .count()
        )
        db.session.add(OrderType(
            company_id=current_user.company_id,
            label=label,
            sort_order=max_sort_order,
        ))
        db.session.commit()
    return redirect(url_for("settings_orders"))


@app.route("/settings/order-types/<int:order_type_id>/toggle", methods=["POST"])
@login_required
def toggle_order_type(order_type_id: int):
    order_type = get_order_type_or_404(order_type_id)
    order_type.is_active = not order_type.is_active
    db.session.commit()
    return redirect(url_for("settings_orders"))


@app.route("/settings/order-types/<int:order_type_id>/delete", methods=["POST"])
@login_required
def delete_order_type(order_type_id: int):
    order_type = get_order_type_or_404(order_type_id)
    if order_type.can_delete:
        db.session.delete(order_type)
        db.session.commit()
    return redirect(url_for("settings_orders"))


# Orders-list column order/visibility — same drag-to-reorder pattern as
# documents.reorder_types (immediate fetch on drop), plus a per-column
# hide/show toggle matching the order-type/source-option Hide button, rather
# than a checkbox-plus-Save form: everything else in Settings acts
# immediately on click, and this shouldn't be the odd one out.
@app.route("/settings/order-columns/<key>/toggle", methods=["POST"])
@login_required
def toggle_order_column(key: str):
    if key not in ORDER_COLUMNS:
        abort(404)
    columns = _order_columns_for(current_user.company_id)
    for col in columns:
        if col["key"] == key:
            col["visible"] = not col["visible"]
    _save_order_columns(current_user.company_id, columns)
    return redirect(url_for("settings_orders"))


@app.route("/settings/order-columns/reorder", methods=["POST"])
@login_required
def reorder_order_columns():
    payload = request.get_json(silent=True) or {}
    order = [key for key in payload.get("order", []) if key in ORDER_COLUMNS]
    if not order:
        return ("", 204)
    visibility = {c["key"]: c["visible"] for c in _order_columns_for(current_user.company_id)}
    columns = [{"key": key, "visible": visibility.get(key, True)} for key in order]
    # A key the client didn't send back (shouldn't happen — every column
    # renders a draggable row) is appended rather than silently dropped.
    for key in visibility:
        if key not in order:
            columns.append({"key": key, "visible": visibility[key]})
    _save_order_columns(current_user.company_id, columns)
    return ("", 204)


@app.route("/settings/sources", methods=["POST"])
@login_required
def add_source_option():
    label = request.form.get("label", "").strip()
    if label and _is_duplicate_label(SourceOption, current_user.company_id, label):
        _flash_settings_notice(f'An option called "{label}" already exists.')
    elif label:
        max_sort_order = (
            SourceOption.query.filter_by(company_id=current_user.company_id)
            .count()
        )
        db.session.add(SourceOption(
            company_id=current_user.company_id,
            label=label,
            sort_order=max_sort_order,
        ))
        db.session.commit()
    return redirect(url_for("settings_clients"))


@app.route("/settings/sources/<int:source_option_id>/toggle", methods=["POST"])
@login_required
def toggle_source_option(source_option_id: int):
    option = get_source_option_or_404(source_option_id)
    option.is_active = not option.is_active
    db.session.commit()
    return redirect(url_for("settings_clients"))


@app.route("/settings/sources/<int:source_option_id>/set-other", methods=["POST"])
@login_required
def set_other_source_option(source_option_id: int):
    """Pair (or unpair) a free-text box with this option, on the client page.

    Handy as an "Other, please specify" catch-all, but that's just the
    common case — the mechanism is a plain "this option gets a text box
    too", usable on whichever option needs one. At most one per company —
    adding it here removes it from whatever option held it before, same
    "only one at a time" shape a radio group would give, but built on the
    existing toggle-button convention instead of a new form control.
    """
    option = get_source_option_or_404(source_option_id)
    turning_on = not option.is_other
    SourceOption.query.filter_by(
        company_id=current_user.company_id, is_other=True
    ).update({"is_other": False})
    option.is_other = turning_on
    db.session.commit()
    return redirect(url_for("settings_clients"))


@app.route("/settings/sources/reorder", methods=["POST"])
@login_required
def reorder_source_options():
    """Fired by the drag-and-drop handler in settings.html — a JSON body,
    not a form post, since there's no page navigation involved. Same shape
    as documents.reorder_types: ids not belonging to this company are
    silently skipped rather than trusted, since the request is data from a
    fetch() call, not a form the server built."""
    payload = request.get_json(silent=True) or {}
    ordered_ids = [i for i in payload.get("order", []) if isinstance(i, int)]
    options_by_id = {
        o.id: o for o in
        SourceOption.query.filter_by(company_id=current_user.company_id).all()
    }
    for index, option_id in enumerate(ordered_ids):
        option = options_by_id.get(option_id)
        if option is not None:
            option.sort_order = index
    db.session.commit()
    return ("", 204)


@app.route("/settings/sources/<int:source_option_id>/delete", methods=["POST"])
@login_required
def delete_source_option(source_option_id: int):
    option = get_source_option_or_404(source_option_id)
    if option.can_delete:
        db.session.delete(option)
        db.session.commit()
    return redirect(url_for("settings_clients"))


@app.route("/settings/company", methods=["POST"])
@login_required
def update_company_details():
    """Name, address and registration numbers — what an invoice has to say
    about who issued it. Only the name is the app's; the rest is the
    billing module's letterhead. Editing any of it leaves invoices already
    issued alone, since those carry their own frozen copy."""
    company = db.session.get(Company, current_user.company_id)
    name = request.form.get("name", "").strip()
    if name:
        company.name = name
    province = request.form.get("province", "").strip().upper()
    invoicing.update_profile(
        company.id, company.name,
        street=request.form.get("street", "").strip() or None,
        city=request.form.get("city", "").strip() or None,
        province=province if province in PROVINCES else None,
        postal_code=request.form.get("postal_code", "").strip().upper() or None,
        gst_number=request.form.get("gst_number", "").strip() or None,
        pst_number=request.form.get("pst_number", "").strip() or None,
        qst_number=request.form.get("qst_number", "").strip() or None,
        neq=request.form.get("neq", "").strip() or None,
    )
    db.session.commit()
    return redirect(url_for("settings_invoicing"))


# Account — the one settings category that's about the signed-in user rather
# than the company. Its own page, not a block on General, because everything
# else under Settings is company-wide: a password sitting among the
# company-wide preferences would read as one of them.
MIN_PASSWORD_LENGTH = 8


def _flash_password_status(message: str, ok: bool) -> None:
    """One-shot feedback for the next Settings → Account render.

    Its own session key rather than `_flash_settings_notice`'s: that one
    renders as a .warning-note, which is right for a standing condition of
    the page and wrong for "that worked" after one button press.
    """
    session["password_status"] = {"message": message, "ok": ok}


@app.route("/settings/account")
@login_required
def settings_account():
    return render_template(
        "settings.html",
        section="account",
        company=db.session.get(Company, current_user.company_id),
        password_status=session.pop("password_status", None),
        signature_saved=session.pop("signature_saved", False),
        min_password_length=MIN_PASSWORD_LENGTH,
        active_view="settings",
    )


@app.route("/settings/account/signature", methods=["POST"])
@login_required
def update_signature():
    """Set the signed-in user's own email signature.

    Blank is a real value here, meaning "no signature" — unlike a key or a
    prompt, there's nothing destructive about clearing it and no other way
    to say it. Stored as None rather than "" so `signature_block` has one
    empty case to test rather than two.
    """
    # Normalised to LF for the same reason prompts are (see
    # ai.services.normalise_newlines): a browser submits textarea content
    # with CRLF, and this text is concatenated into message bodies and
    # compared in tests — one line-ending convention in the database is
    # worth more than round-tripping the browser's exactly.
    signature = request.form.get("signature", "").replace("\r\n", "\n").replace("\r", "\n")
    current_user.signature = signature.strip() or None
    db.session.commit()
    session["signature_saved"] = True
    return redirect(url_for("settings_account"))


@app.route("/settings/account/password", methods=["POST"])
@login_required
def change_password():
    """Change the signed-in user's own password.

    The current password is required even though the session already proves
    who this is — it's what stops an unattended logged-in browser from being
    turned into a permanent one. There's no reset-by-email counterpart: the
    app has no address of its own to send from (the Gmail accounts under
    Email/Calendar are the studio's outgoing client mail, not app mail), so
    a forgotten password is still a shell job. See N2 in REQUIREMENTS.md.
    """
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirmation = request.form.get("confirm_password", "")

    if not current_user.check_password(current):
        # Deliberately not "wrong password" — the same wording the login
        # page uses would be confusing here, where only one field can be at
        # fault and the user is already identified.
        error = "That isn't your current password."
    elif len(new) < MIN_PASSWORD_LENGTH:
        error = f"Choose a new password of at least {MIN_PASSWORD_LENGTH} characters."
    elif new != confirmation:
        error = "The two new passwords don't match."
    elif new == current:
        error = "That's already your password."
    else:
        error = None

    if error is not None:
        _flash_password_status(error, ok=False)
    else:
        # current_user is a proxy around the row; write through the session's
        # own instance so the commit is unambiguous.
        user = db.session.get(User, current_user.id)
        user.set_password(new)
        db.session.commit()
        _flash_password_status("Password changed.", ok=True)

    return redirect(url_for("settings_account"))


@app.route("/settings/invoicing", methods=["POST"])
@login_required
def update_invoicing_settings():
    company = db.session.get(Company, current_user.company_id)
    prefix = request.form.get("invoice_prefix", "").strip().upper()
    # Changing the prefix starts a fresh sequence rather than renumbering
    # anything already issued — invoice numbers that have gone out to a
    # client are not ours to rewrite.
    fields = {
        "payment_instructions":
            request.form.get("payment_instructions", "").strip() or None,
    }
    if prefix:
        fields["invoice_prefix"] = prefix[:10]
    invoicing.update_profile(company.id, company.name, **fields)
    db.session.commit()
    return redirect(url_for("settings_invoicing"))


# ---------------------------------------------------------------------------
# Analytics — company-wide client and revenue stats. "Paid revenue" is
# always the sum of recorded Payment rows, never inferred from order
# status, since studios run different deposit schemes (see Payment's
# docstring in models.py). "Average value per client" is lifetime spend
# per client (sum of their orders), averaged only over clients who've
# actually ordered — not average order price.
# ---------------------------------------------------------------------------

@app.route("/analytics")
@login_required
def analytics():
    clients = Client.query.filter_by(company_id=current_user.company_id).all()
    clients_with_orders = [c for c in clients if c.orders]
    avg_value_per_client = (
        sum(c.lifetime_value for c in clients_with_orders) / len(clients_with_orders)
        if clients_with_orders else 0
    )
    top_clients = sorted(
        clients_with_orders, key=lambda c: c.lifetime_value, reverse=True
    )[:5]

    # Include hidden SourceOptions too — a hidden-but-historically-used
    # source should still show up in stats (that's the whole point of
    # hiding instead of deleting once a client references it).
    source_options = SourceOption.query.filter_by(company_id=current_user.company_id).all()
    total_clients = len(clients)
    source_breakdown = [
        (option.label, len(option.clients) / total_clients * 100)
        for option in source_options
    ] if total_clients else []
    source_breakdown = sorted(
        (pair for pair in source_breakdown if pair[1] > 0),
        key=lambda pair: pair[1], reverse=True,
    )

    payments = (
        Payment.query.join(Order).join(Client)
        .filter(Client.company_id == current_user.company_id)
        .all()
    )
    total_revenue = sum(p.amount for p in payments)
    this_year = date.today().year
    revenue_ytd = sum(p.amount for p in payments if p.paid_date.year == this_year)

    # Where the money actually came from. Ordered by amount rather than by
    # the labels dict so the dominant method reads first.
    by_method: dict[str, float] = {}
    for payment in payments:
        by_method[payment.method] = by_method.get(payment.method, 0) + payment.amount
    method_breakdown = sorted(
        ((PAYMENT_METHOD_LABELS.get(k, k), v) for k, v in by_method.items()),
        key=lambda pair: pair[1], reverse=True,
    )

    # Outstanding counts invoiced work only — an order that hasn't been
    # billed yet isn't money anyone owes.
    company_id = current_user.company_id
    documents = invoicing.documents_for(
        company_id, billing_adapter.resolver(company_id, with_urls=False),
        _seller_name(company_id),
    )
    outstanding = sum(
        doc.balance_due for doc in documents
        if doc.status != "void" and not doc.is_settled
    )
    # Tax actually collected this year, straight out of the frozen invoice
    # rows — what a GST/QST remittance is. Windowed on issued_date so it lines
    # up with Revenue YTD above; one (label, amount) row per tax charged.
    tax_collected_ytd = invoicing.tax_collected(
        company_id, since=date(this_year, 1, 1)
    )

    return render_template(
        "analytics.html",
        avg_value_per_client=avg_value_per_client,
        top_clients=top_clients,
        source_breakdown=source_breakdown,
        total_revenue=total_revenue,
        revenue_ytd=revenue_ytd,
        method_breakdown=method_breakdown,
        outstanding=outstanding,
        tax_collected_ytd=sorted(
            tax_collected_ytd, key=lambda pair: pair[1], reverse=True
        ),
        layout=_analytics_layout_for(current_user.company_id),
        active_view="analytics",
    )


# The one route on the Analytics page that writes, and it writes presentation
# only — never a figure. A JSON fetch fired on `dragend`, the same shape as
# reorder_order_columns above, so a drop saves immediately with no Save
# button. The page posts its whole layout (sections and each section's cards)
# rather than one list at a time, since either kind of drag leaves the DOM
# holding the complete answer anyway.
@app.route("/analytics/layout/reorder", methods=["POST"])
@login_required
def reorder_analytics_layout():
    payload = request.get_json(silent=True) or {}
    sections = payload.get("sections")
    if not isinstance(sections, list) or not sections:
        return ("", 204)

    layout = []
    for entry in sections:
        if not isinstance(entry, dict) or entry.get("key") not in ANALYTICS_SECTIONS:
            continue
        _, canonical_cards = ANALYTICS_SECTIONS[entry["key"]]
        cards = entry.get("cards")
        cards = cards if isinstance(cards, list) else []
        layout.append({
            "key": entry["key"],
            "cards": [c for c in dict.fromkeys(cards) if c in canonical_cards],
        })
    if not layout:
        return ("", 204)
    # Anything the client didn't send back is filled in by
    # _analytics_layout_for's merge on the next read, so a partial payload
    # degrades to "these moved, the rest keep their default place".
    _save_analytics_layout(current_user.company_id, layout)
    return ("", 204)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
