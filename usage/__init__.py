"""
Feature-usage events: which parts of the app studios actually use.

This file is the half every module may import. It depends on Flask and the
standard library only — no models, no `db` — so `from usage import track`
costs a module nothing of its liftability, the same allowance `crypto.py`
gets (see `tests/test_usage.py`, which enforces it).

`track()` writes nothing. It queues the event on `flask.g`, and the host's
`after_request` hook (`usage.store.flush`) persists the queue once the
response exists — on its own connection, after the action's own commit, and
inside a blanket `try`. That ordering is the whole design: **recording a
usage event can never fail, slow down or roll back the action it
describes** (US1). An event lost to a full disk is a missing tally mark; an
invoice lost to an analytics insert would be a real bug.

The catalog below is the complete list. An unknown name is dropped with a
warning rather than raised, for the same reason — and `tests/test_usage.py`
reads every `track("...")` call in the source so a typo still fails CI.
"""

import logging

from flask import g, has_request_context

logger = logging.getLogger(__name__)

# name -> (area, label shown on /admin/usage, once-per-user-per-day?)
#
# "Daily" events are page views: counting every refresh would measure how
# often someone presses F5, not whether they use the page. Everything else
# counts each successful action.
EVENTS: dict[str, tuple[str, str, bool]] = {
    # Sessions
    "auth.login": ("Sessions", "Signed in", False),
    # Read-only pages — their only trace is being opened
    "view.calendar": ("Views", "Calendar", True),
    "view.orders_list": ("Views", "Orders list", True),
    "view.clients_list": ("Views", "Clients list", True),
    "view.analytics": ("Views", "Analytics", True),
    "view.invoices_list": ("Views", "Invoices list", True),
    "view.inventory": ("Views", "Inventory", True),
    "view.leads": ("Views", "Leads", True),
    "view.client_emails": ("Views", "Client emails tab", True),
    "view.help": ("Views", "Help pages", True),
    # Clients
    "client.created": ("Clients", "Client created", False),
    "client.hidden": ("Clients", "Client hidden / shown", False),
    # Orders
    "order.created": ("Orders", "Order created", False),
    "order.updated": ("Orders", "Order edited", False),
    "order.status_changed": ("Orders", "Order status changed", False),
    "order.rush_toggled": ("Orders", "Rush toggled", False),
    "order.line_added": ("Orders", "Line item added", False),
    "order.discount_set": ("Orders", "Discount set", False),
    "order.payment_recorded": ("Orders", "Payment recorded", False),
    # Invoicing
    "invoice.created": ("Invoicing", "Invoice created", False),
    "invoice.status_changed": ("Invoicing", "Invoice status changed", False),
    "invoice.pdf_downloaded": ("Invoicing", "Invoice PDF opened", False),
    # Inventory
    "inventory.item_created": ("Inventory", "Item created", False),
    "inventory.item_updated": ("Inventory", "Item edited", False),
    "order.material_added": ("Inventory", "Material added to order", False),
    # Documents
    "document.uploaded": ("Documents", "Document uploaded", False),
    "document.opened": ("Documents", "Document opened", False),
    # AI
    "ai.reply_suggested": ("AI", "Reply suggested", False),
    "ai.render_requested": ("AI", "Render requested", False),
    "ai.render_saved": ("AI", "Render saved", False),
    "ai.render_discarded": ("AI", "Render discarded", False),
    # Mail & calendar
    "integration.google_connected": ("Mail & calendar", "Google connected", False),
    "integration.google_disconnected": ("Mail & calendar", "Google disconnected", False),
    "mail.sent": ("Mail & calendar", "Email sent", False),
    "mail.thread_opened": ("Mail & calendar", "Conversation opened", False),
    "mail.manual_sync": ("Mail & calendar", "Manual sync", False),
    "lead.dismissed": ("Mail & calendar", "Lead dismissed", False),
    "lead.trashed": ("Mail & calendar", "Lead trashed", False),
    "sender_rule.created": ("Mail & calendar", "Sender rule created", False),
    "calendar.event_created": ("Mail & calendar", "Calendar event created", False),
    "calendar.event_updated": ("Mail & calendar", "Calendar event edited", False),
    # Showcase — sold per company (features/), so its counts also say
    # whether the tier is worth selling
    "view.showcase": ("Showcase", "Showcase page", True),
    "showcase.item_created": ("Showcase", "Piece created", False),
    "showcase.photo_added": ("Showcase", "Photo added", False),
    "showcase.item_published": ("Showcase", "Piece published", False),
    "showcase.item_withdrawn": ("Showcase", "Piece withdrawn", False),
    "showcase.order_dismissed": ("Showcase", "Order not showcased", False),
    "view.catalog": ("Showcase", "Catalog mode opened", True),
    "showcase.kiosk_link_created": ("Showcase", "Device link created", False),
    # Settings — one event, the section says which
    "settings.changed": ("Settings", "Settings changed", False),
}

_QUEUE = "usage_events"


def track(event: str, **props) -> None:
    """Queue one usage event for this request. Never raises.

    Call it on the success path only, after the action's own checks have
    passed — a refused save is not a use. `props` are small enums
    (`via="modal"`, `method="cash"`): never names, emails, amounts or free
    text. Values other than str/int/bool are dropped.
    """
    try:
        if not has_request_context():
            # A scheduler job or a script: not a person using a feature.
            return
        if event not in EVENTS:
            logger.warning("usage: unknown event %r dropped", event)
            return
        clean = {k: v for k, v in props.items() if isinstance(v, (str, int, bool))}
        queue = g.get(_QUEUE)
        if queue is None:
            queue = []
            setattr(g, _QUEUE, queue)
        queue.append((event, clean))
    except Exception:  # noqa: BLE001 — see the module docstring
        logger.warning("usage: could not queue %r", event, exc_info=True)


def take_queued() -> list[tuple[str, dict]]:
    """Empty and return this request's queue. Used by `usage.store.flush`."""
    if not has_request_context():
        return []
    return g.pop(_QUEUE, None) or []
