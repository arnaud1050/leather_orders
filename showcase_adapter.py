"""
The seam between this application and the showcase module.

`showcase/` never imports `Order`, `Client` or `OrderType`, nor the
documents module. It asks the hooks in `showcase/hooks.py`, and this file
is the only place that knows a showcase piece can come from an order of
this app, with photos among that order's documents. Kept at the project
root, like `billing_adapter.py`, so everything under `showcase/` stays free
of this app's models.

Every function takes `company_id` first and answers only for that company
(hard rule 1): the ids reaching here come from URLs.
"""

from flask import has_request_context, url_for

import brand
from documents import services as documents_service
from documents import storage as documents_storage
from models import Client, Company, Order, OrderType, db

IMAGE_TYPES = ("image/jpeg", "image/png")


def _order(company_id: int, order_id: int) -> Order | None:
    return (
        Order.query.join(Client)
        .filter(Order.id == order_id, Client.company_id == company_id)
        .first()
    )


def _url(endpoint: str, **values) -> str | None:
    """A link when there's a request to build it from; None in a script or
    a test calling services directly — same caution as billing_adapter's
    `with_urls`."""
    return url_for(endpoint, **values) if has_request_context() else None


def _delivered_on(order: Order):
    """When the piece changed hands: the pickup date if one was recorded,
    otherwise the date it was due."""
    return order.pickup_date or order.due


def _summary(order: Order) -> dict:
    return {
        "id": order.id,
        "item": order.item,
        "client_name": order.client.name,
        "status": order.status,
        "delivered": order.status == "delivered",
        "delivered_on": _delivered_on(order),
        "order_type_id": order.order_type_id,
        "order_type_label": order.order_type.label if order.order_type else None,
        "showcase_url": _url("order_showcase", order_id=order.id),
    }


def delivered_orders(company_id: int) -> list[dict]:
    orders = (
        Order.query.join(Client)
        .filter(Client.company_id == company_id, Order.status == "delivered")
        .all()
    )
    return [_summary(order) for order in orders]


def order_summary(company_id: int, order_id: int) -> dict | None:
    order = _order(company_id, order_id)
    return _summary(order) if order is not None else None


def order_images(company_id: int, order_id: int) -> list[dict]:
    if _order(company_id, order_id) is None:
        return []
    return [
        {
            "id": document.id,
            "filename": document.original_filename,
            "preview_url": _url("documents.view", order_id=order_id, document_id=document.id),
        }
        for document in documents_service.list_for_order(order_id)
        if document.company_id == company_id and document.content_type in IMAGE_TYPES
    ]


def load_order_image(company_id: int, order_id: int, document_id: int) -> dict | None:
    """Tenant-checked twice, like app.py's `_load_order_document`: the
    document must be on this order, and its company must be this one."""
    if _order(company_id, order_id) is None:
        return None
    document = documents_service.get_for_order(order_id, document_id)
    if (document is None or document.company_id != company_id
            or document.content_type not in IMAGE_TYPES):
        return None
    data = documents_storage.read(document.company_id, document.stored_filename)
    if data is None:
        return None
    return {"filename": document.original_filename,
            "content_type": document.content_type, "data": data}


def company_active(company_id: int) -> bool:
    company = db.session.get(Company, company_id)
    return company is not None and company.is_active


def studio_brand(company_id: int) -> dict:
    """The studio's name and logo (always a PNG — brand/ re-encodes it),
    and the logo's tone, so catalog mode knows whether it needs a light
    plate behind it on its dark page (BL12)."""
    company = db.session.get(Company, company_id)
    look = brand.appearance(company_id)
    return {"name": company.name if company else "",
            "logo_path": brand.logo_path(company_id),
            "logo_tone": look["tone"] if look else None}


def order_types(company_id: int) -> list[dict]:
    return [
        {"id": t.id, "label": t.label, "is_active": t.is_active}
        for t in OrderType.query.filter_by(company_id=company_id).order_by(OrderType.sort_order)
    ]
