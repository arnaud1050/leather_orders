"""
The two things a host application has to tell this module.

`SUBJECT_FK` is the only place `billing` names a host table. Porting the
module to a project that invoices jobs, bookings or subscriptions instead
of orders is a one-line change here — the alternative (an untyped
`subject_id` with no foreign key) would trade real referential integrity
for saving that line, which isn't a good deal.

The status vocabulary is here rather than in `models.py` because the host's
routes need it too (to render the dropdown), and importing it from a model
module to build a form reads backwards.
"""

# Host table this module issues invoices against, as SQLAlchemy spells a
# foreign key. Change this and the matching relationship in the host's
# models to point billing at something else.
SUBJECT_FK = "orders.id"

# Statuses a person can set. "paid" is deliberately absent: it's derived
# from payments (see InvoiceDocument.is_settled), and storing it would let
# an invoice disagree with the money actually received.
SETTABLE_STATUSES = ("draft", "sent", "void")

STATUS_LABELS = {
    "draft": "Draft",
    "sent": "Sent",
    "paid": "Paid",
    "void": "Void",
}

# How money arrived. A payment processor is just another entry here — the
# app owns the invoice record either way, and the method only records how
# the money came in.
PAYMENT_METHOD_LABELS = {
    "cash": "Cash",
    "etransfer": "E-transfer",
    "square": "Square",
    "other": "Other",
}
