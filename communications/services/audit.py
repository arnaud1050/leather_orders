"""
Audit logging for communication events (§16).

Deliberately tiny. The value isn't in the API, it's in the discipline of
calling it from exactly the places that matter: connecting a mailbox,
disconnecting one, and sending mail on someone's behalf. Those are the
three things that, after the fact, someone will need to answer "who did
that, and when".

`record()` never raises. An audit write failing is bad, but failing a send
that already went out because we couldn't log it is worse — and losing the
send's *local record* over a log row would be worse still.
"""

import logging

from flask_login import current_user

from models import db

from communications.models import AuditLog

logger = logging.getLogger(__name__)


def record(company_id: int, event: str, detail: str | None = None, user_id=None) -> None:
    """Append an audit row. Does not commit — it rides along with whatever
    transaction the caller is already in, so the log and the thing it
    describes commit together or not at all.

    `user_id` defaults to the logged-in user, and stays None for background
    jobs, where there genuinely isn't one.
    """
    if user_id is None:
        try:
            user_id = current_user.id if current_user.is_authenticated else None
        except Exception:  # noqa: BLE001 — no request context (background job)
            user_id = None

    try:
        db.session.add(AuditLog(
            company_id=company_id, user_id=user_id, event=event, detail=detail,
        ))
    except Exception:  # noqa: BLE001 — see module docstring
        logger.exception("Failed to record audit event %s for company %s", event, company_id)


def recent(company_id: int, limit: int = 20) -> list[AuditLog]:
    """Newest events first, scoped to one tenant."""
    return (
        AuditLog.query.filter_by(company_id=company_id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
        .all()
    )
