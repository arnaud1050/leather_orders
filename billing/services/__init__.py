"""
The module's public surface.

Everything outside `billing/` should import from here (plus `billing.tax`
and `billing.documents`, which are pure) and nowhere deeper. Reaching into
`billing.models` from a route works today and couples the host to a schema
it shouldn't know about.

    from billing.services import invoicing

Every function takes `company_id` first and filters on it, so a tenant
filter can't be forgotten. Services mutate the session; the caller
commits, except where noted.
"""

from billing.services import invoicing  # noqa: F401
