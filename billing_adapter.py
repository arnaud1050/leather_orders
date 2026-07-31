"""
The seam between this application and the billing module.

Billing never imports `Order`, `Client` or `Payment`. It asks for a
`Billable` instead, and this file is the only place that knows an order is
what this particular app bills for. Porting billing to another project
means rewriting this one file — not touching `billing/`.

Kept at the project root rather than inside `billing/` for exactly that
reason: everything under `billing/` should survive a copy-paste into a
different codebase, and this wouldn't.
"""

from flask import url_for

from billing.documents import Billable, LineItem, PartyDetails, PaymentRecord
from models import Order, db


def billable_for(order: Order, *, with_urls: bool = False) -> Billable:
    """Describe an order the way billing understands it.

    `tax_province` is the *client's*, not the studio's — tax is
    destination-based (see billing/tax.py).

    `with_urls` is off by default because `url_for` needs a request
    context, and this is called from `Order.total`, which runs in scripts
    and tests too.
    """
    client = order.client
    return Billable(
        subject_id=order.id,
        description=order.item,
        tax_province=client.province,
        payer=PartyDetails(
            name=client.name,
            address=client.formatted_address,
            email=client.email,
            phone=client.phone,
            url=url_for("client_page", client_id=client.id) if with_urls else None,
        ),
        lines=[
            LineItem(line.description, line.quantity, line.unit_price)
            for line in order.lines
        ],
        payments=[
            PaymentRecord(p.amount, p.paid_date, p.method, p.reference)
            for p in order.payments
        ],
        url=url_for("order_page", order_id=order.id) if with_urls else None,
    )


def resolver(company_id: int, *, with_urls: bool = True):
    """`subject_id -> Billable`, scoped to one tenant.

    Returned as a closure so `billing.services` can walk a tenant's
    invoices without ever seeing an Order. Raising on a missing order is
    deliberate: an invoice whose subject has vanished is a broken
    invariant, not something to paper over with a blank row.
    """
    from models import Client

    def resolve(subject_id: int) -> Billable:
        order = (
            Order.query.join(Client)
            .filter(Order.id == subject_id, Client.company_id == company_id)
            .first()
        )
        if order is None:
            raise LookupError(f"no order {subject_id} for company {company_id}")
        return billable_for(order, with_urls=with_urls)

    return resolve


def subtotal_of(subject_id: int) -> float:
    """Pre-tax value of an order, for billing's legacy-invoice backfill.

    Reads the line items directly rather than going through `Order.total`,
    which would recurse straight back into billing.
    """
    from models import OrderLine

    total = db.session.query(
        db.func.sum(OrderLine.quantity * OrderLine.unit_price)
    ).filter(OrderLine.order_id == subject_id).scalar()
    return float(total or 0.0)
