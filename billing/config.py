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

import os

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

# The PDF layouts a tenant can pick from (see billing/pdf.py), key -> the
# label a settings form shows. Here rather than beside the renderer because
# the host's settings page needs the list too.
INVOICE_TEMPLATES = {
    "classic": "Classic",
    "banded": "Banded",
}
DEFAULT_INVOICE_TEMPLATE = "classic"

# What the coloured layout uses until a tenant chooses: the app's own ink,
# so an unconfigured invoice is sober rather than branded with somebody
# else's taste.
DEFAULT_PRIMARY_COLOR = "#1c1a17"

# Suggestions offered beside the colour picker in settings. Dark enough
# that white text and a white logo stay readable on the band, and spread
# across the wheel so most brands find a neighbour. Not a restriction:
# any #rrggbb can be chosen.
SUGGESTED_PRIMARY_COLORS = (
    "#1c1a17", "#3b3b3b", "#1f3a5f", "#2c5d8a", "#2f4f3a",
    "#3d6b5a", "#6b1f2a", "#8c2f39", "#4a2c4a", "#8c6d1f",
)

# Where invoice logos are kept (see billing/logos.py). The same data/
# directory as the SQLite file and the order documents — the bind-mounted
# volume in both Docker deployments, so a logo survives a rebuild.
# Env-overridable, same convention as documents/config.py.
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGO_DIR = os.environ.get(
    "BILLING_LOGO_DIR", os.path.join(_BASE_DIR, "data", "billing_logos"))
# Cap on the *upload*. What's stored is a re-encoded, downscaled copy and is
# normally far smaller, so this only has to admit a photo straight off a
# phone. 10 MB matches nginx's client_max_body_size (server_config), which
# would otherwise refuse a bigger file before the app could explain why.
LOGO_MAX_BYTES = int(os.environ.get("BILLING_LOGO_MAX_BYTES", 10 * 1024 * 1024))

# How money arrived. A payment processor is just another entry here — the
# app owns the invoice record either way, and the method only records how
# the money came in.
PAYMENT_METHOD_LABELS = {
    "cash": "Cash",
    "etransfer": "E-transfer",
    "square": "Square",
    "other": "Other",
}
