"""
The host half of usage events: the table, the write, and the report.

Unlike `usage/__init__.py`, this file is host code — it knows `Company`,
`User` and impersonation — so no module may import it (`tests/test_usage.py`
enforces that). `app.py` wires `flush()` in as an `after_request` hook and
`admin/` reads the report.

Every rule about failure lives in `flush()`; read its docstring before
changing the write path.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from flask import request, session
from flask_login import current_user

from models import Company, User, db
from usage import EVENTS, take_queued, track

logger = logging.getLogger(__name__)

# How long the event write may wait on a SQLite lock held by someone else
# (the sync scheduler, another worker) before giving up on the event. The
# response is already built by then but not yet sent, so this is time the
# user would spend waiting: keep it small.
LOCK_WAIT_MS = 250

# Read-only pages, recorded from the endpoint that served them rather than
# with a `track()` in each handler — a view has no "success path" to find,
# just a 200. GET only; the `after_request` status check does the rest.
VIEW_ENDPOINTS: dict[str, tuple[str, dict]] = {
    "calendar_view": ("view.calendar", {"mode": "month"}),
    "month_view": ("view.calendar", {"mode": "month"}),
    "current_week_view": ("view.calendar", {"mode": "week"}),
    "week_view": ("view.calendar", {"mode": "week"}),
    "orders_list": ("view.orders_list", {}),
    "clients_list": ("view.clients_list", {}),
    "analytics": ("view.analytics", {}),
    "billing.invoice_list": ("view.invoices_list", {}),
    "inventory.inventory_list": ("view.inventory", {}),
    "communications.leads": ("view.leads", {}),
    "communications.client_emails": ("view.client_emails", {}),
    "order_lifecycle_help": ("view.help", {"page": "orders"}),
    "client_lifecycle_help": ("view.help", {"page": "clients"}),
    "showcase.showcase_list": ("view.showcase", {}),
    # Signed-in only: a kiosk link has no user to count (US4).
    "showcase.present": ("view.catalog", {}),
    # Not views in the dashboard sense, but GETs with nothing to decide:
    # serving the file is the use.
    "billing.invoice_pdf": ("invoice.pdf_downloaded", {}),
    "documents.download": ("document.opened", {"how": "download"}),
    "documents.view": ("document.opened", {"how": "view"}),
    "communications.thread_page": ("mail.thread_opened", {}),
}


def utcnow() -> datetime:
    """Naive UTC, matching the rest of the schema."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class UsageEvent(db.Model):
    """One successful use of a feature by a tenant user.

    Append-only and kept for as long as the company exists (US6). Plain
    integer ids rather than foreign keys: a usage row must never be the
    reason a write to `companies` or `users` fails, and nothing here is
    ever joined for correctness — only for display.
    """

    __tablename__ = "usage_events"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    company_id = db.Column(db.Integer, nullable=False)
    user_id = db.Column(db.Integer, nullable=False)
    event = db.Column(db.String(60), nullable=False)
    # Small JSON object of enum-like values, sorted keys; NULL when empty.
    # Sorted so the same props always produce the same string — the daily
    # de-duplication and the report's breakdown both compare it as text.
    props = db.Column(db.Text)

    __table_args__ = (
        db.Index("ix_usage_events_event_created", "event", "created_at"),
        db.Index("ix_usage_events_company_created", "company_id", "created_at"),
        db.Index("ix_usage_events_user_event_created", "user_id", "event", "created_at"),
    )


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def flush(response):
    """`after_request` hook: persist this request's queued events.

    The rules, each of which is what keeps a broken analytics write from
    touching the action it describes (US1–US4):

    - **Nothing here can raise.** An exception from an `after_request`
      hook would turn the user's successful save into a 500, so the whole
      body sits in one `try` and a failure is a logged warning.
    - **It runs after the handler has committed** and writes on its **own
      connection**, never through `db.session`. A failed insert therefore
      can't poison the session the action used, and can't roll it back.
    - **It waits at most `LOCK_WAIT_MS` for a lock**, then drops the
      events. The default SQLite busy timeout is five seconds — five
      seconds added to someone's click to record that they clicked.
    - **Only responses under 400 count.** A handler that queued an event
      and then failed later did not deliver the feature.
    - **Only tenant users count**: never platform staff, never a session
      that is impersonating (it's us, looking).
    """
    try:
        _queue_view(response)
        events = take_queued()
        if not events or response.status_code >= 400:
            return response
        actor = _actor()
        if actor is None or _request_still_writing():
            return response
        _write(actor, events)
    except Exception:  # noqa: BLE001 — see the docstring
        logger.warning("usage: events dropped", exc_info=True)
    return response


def _queue_view(response) -> None:
    # 200 exactly, not "under 400": a redirect hasn't shown anything yet —
    # documents.view redirects to documents.download for a file it can't
    # preview, and that must count as one opening, not two.
    if request.method != "GET" or response.status_code != 200:
        return
    mapped = VIEW_ENDPOINTS.get(request.endpoint or "")
    if mapped is not None:
        event, props = mapped
        track(event, **props)


def _actor() -> tuple[int, int] | None:
    """(company_id, user_id) of a countable user, or None."""
    from admin.services import IMPERSONATOR_KEY  # admin imports models too; keep import local

    if not current_user.is_authenticated or not current_user.is_tenant_user:
        return None
    if session.get(IMPERSONATOR_KEY) is not None:
        return None
    return current_user.company_id, current_user.id


def _request_still_writing() -> bool:
    """True when this request's own session holds an uncommitted write.

    Then the action isn't durable — teardown will roll it back — so there's
    nothing true to record, and on SQLite our separate connection would
    only sit out `LOCK_WAIT_MS` waiting on our own lock before failing.
    Checked without starting a transaction: `in_transaction()` first, and
    only then the driver's own flag, which pysqlite sets on the first
    write rather than on a read.
    """
    session = db.session()  # the request's own Session, out of the scoped registry
    if not session.in_transaction():
        return False
    dbapi = session.connection().connection.dbapi_connection
    return bool(getattr(dbapi, "in_transaction", False))


def _write(actor: tuple[int, int], events: list[tuple[str, dict]]) -> None:
    company_id, user_id = actor
    table = UsageEvent.__table__
    now = utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    with db.engine.connect() as conn:
        # The PRAGMA autobegins this connection's transaction, so commit
        # explicitly rather than opening a second one with `begin()`.
        restore = _limit_lock_wait(conn)
        try:
            for event, props in events:
                props_json = json.dumps(props, sort_keys=True) if props else None
                if EVENTS[event][2] and _seen_today(conn, user_id, event, props_json, day_start):
                    continue
                conn.execute(table.insert().values(
                    created_at=now, company_id=company_id, user_id=user_id,
                    event=event, props=props_json,
                ))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            restore()


def _seen_today(conn, user_id, event, props_json, day_start) -> bool:
    """Daily events count once per user, per props, per UTC day (US5)."""
    table = UsageEvent.__table__
    same_props = (table.c.props.is_(None) if props_json is None
                  else table.c.props == props_json)
    query = (
        sa.select(table.c.id)
        .where(table.c.user_id == user_id, table.c.event == event,
               same_props, table.c.created_at >= day_start)
        .limit(1)
    )
    return conn.execute(query).first() is not None


def _limit_lock_wait(conn):
    """Shorten SQLite's busy timeout on this pooled connection, and return
    the function that puts it back — the connection goes back to the pool
    afterwards, and the next ordinary request must get the normal wait."""
    if conn.dialect.name != "sqlite":
        return lambda: None
    previous = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
    conn.exec_driver_sql(f"PRAGMA busy_timeout = {LOCK_WAIT_MS}")

    def restore():
        try:
            conn.exec_driver_sql(f"PRAGMA busy_timeout = {int(previous)}")
        except Exception:  # noqa: BLE001
            # A connection we can't reset shouldn't be reused with our
            # short wait on it: drop it from the pool instead.
            conn.invalidate()

    return restore


# ---------------------------------------------------------------------------
# Reading — /admin/usage
# ---------------------------------------------------------------------------

PERIODS = {"30": 30, "90": 90, "365": 365, "all": None}
DEFAULT_PERIOD = "90"

# The catalog's areas, in catalog order — the Area filter's options.
AREAS = list(dict.fromkeys(area for area, _, _ in EVENTS.values()))

# Sortable columns of the feature table: key -> (row value, first-click
# direction). Numbers open largest-first, since "what's used most" is the
# question; text opens A–Z. "area" is catalog order, the default.
_CATALOG_ORDER = {name: index for index, name in enumerate(EVENTS)}
FEATURE_SORTS = {
    "area": (lambda r: _CATALOG_ORDER[r["event"]], "asc"),
    "feature": (lambda r: r["label"].lower(), "asc"),
    "companies": (lambda r: r["companies"], "desc"),
    "people": (lambda r: r["users"], "desc"),
    "uses": (lambda r: r["uses"], "desc"),
    "last_used": (lambda r: r["last_used"] or datetime.min, "desc"),
}
DEFAULT_SORT = "area"


def period_start(period: str) -> datetime | None:
    days = PERIODS.get(period)
    return None if days is None else utcnow() - timedelta(days=days)


def _excluded_company_ids():
    """Tenants flagged `exclude_from_usage` — demo and test companies.
    Their events stay in the table; every report query leaves them out."""
    return sa.select(Company.id).where(Company.exclude_from_usage.is_(True))


def _scoped(query, since, company_id):
    query = query.filter(UsageEvent.company_id.notin_(_excluded_company_ids()))
    if since is not None:
        query = query.filter(UsageEvent.created_at >= since)
    if company_id is not None:
        query = query.filter(UsageEvent.company_id == company_id)
    return query


def feature_report(since: datetime | None, company_id: int | None = None) -> list[dict]:
    """One row per catalog event, in catalog order — including the ones
    nobody used, which are as much the answer as the popular ones (US8).

    `companies` is the headline figure: one heavy user can make a feature
    look popular by raw count, but not by how many studios use it.
    """
    totals = {
        row.event: row
        for row in _scoped(
            db.session.query(
                UsageEvent.event,
                sa.func.count(UsageEvent.id).label("uses"),
                sa.func.count(sa.distinct(UsageEvent.company_id)).label("companies"),
                sa.func.count(sa.distinct(UsageEvent.user_id)).label("users"),
                sa.func.max(UsageEvent.created_at).label("last_used"),
            ),
            since, company_id,
        ).group_by(UsageEvent.event)
    }
    breakdowns: dict[str, list[tuple[str, int]]] = {}
    for row in _scoped(
        db.session.query(UsageEvent.event, UsageEvent.props,
                         sa.func.count(UsageEvent.id).label("uses")),
        since, company_id,
    ).filter(UsageEvent.props.isnot(None)).group_by(UsageEvent.event, UsageEvent.props):
        breakdowns.setdefault(row.event, []).append((_describe_props(row.props), row.uses))

    report = []
    for name, (area, label, daily) in EVENTS.items():
        row = totals.get(name)
        report.append({
            "event": name, "area": area, "label": label, "daily": daily,
            "uses": row.uses if row else 0,
            "companies": row.companies if row else 0,
            "users": row.users if row else 0,
            "last_used": row.last_used if row else None,
            "breakdown": sorted(breakdowns.get(name, []), key=lambda b: -b[1]),
        })
    return report


def sort_features(rows: list[dict], key: str, direction: str) -> list[dict]:
    """Sort the feature table. Ties keep catalog order whichever way the
    main key runs, so equal rows don't shuffle between clicks."""
    value, _ = FEATURE_SORTS[key]
    rows = sorted(rows, key=lambda r: _CATALOG_ORDER[r["event"]])
    return sorted(rows, key=value, reverse=(direction == "desc"))


def _describe_props(props_json: str) -> str:
    try:
        props = json.loads(props_json)
    except ValueError:
        return props_json
    return ", ".join(f"{key}: {value}" for key, value in sorted(props.items()))


def company_report(since: datetime | None) -> list[dict]:
    """Per company: who is active and how much. Every company is listed,
    including ones with nothing in the period — a silent tenant is the
    row worth noticing."""
    activity = {
        row.company_id: row
        for row in _scoped(
            db.session.query(
                UsageEvent.company_id,
                sa.func.count(UsageEvent.id).label("events"),
                sa.func.count(sa.distinct(UsageEvent.user_id)).label("users"),
                sa.func.count(sa.distinct(UsageEvent.event)).label("features"),
                sa.func.max(UsageEvent.created_at).label("last_seen"),
            ),
            since, None,
        ).group_by(UsageEvent.company_id)
    }
    logins = dict(
        _scoped(
            db.session.query(UsageEvent.company_id, sa.func.count(UsageEvent.id)),
            since, None,
        ).filter(UsageEvent.event == "auth.login").group_by(UsageEvent.company_id)
    )
    seats = dict(
        db.session.query(User.company_id, sa.func.count(User.id))
        .filter(User.company_id.isnot(None), User.is_active.is_(True))
        .group_by(User.company_id)
    )
    rows = []
    for company in (Company.query.filter_by(exclude_from_usage=False)
                    .order_by(Company.name).all()):
        row = activity.get(company.id)
        rows.append({
            "id": company.id, "name": company.name, "is_active": company.is_active,
            "seats": seats.get(company.id, 0),
            "users": row.users if row else 0,
            "events": row.events if row else 0,
            "features": row.features if row else 0,
            "logins": logins.get(company.id, 0),
            "last_seen": row.last_seen if row else None,
        })
    rows.sort(key=lambda r: (r["last_seen"] is None, -(r["events"])))
    return rows
