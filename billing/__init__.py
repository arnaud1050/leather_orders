"""
Invoicing, billing and Canadian sales tax, as a self-contained module.

Built to be liftable into another project: it owns its own tables, its own
migrations, its own blueprint and templates, and it never imports a host
model. What it needs to know about the thing being invoiced arrives as a
`Billable` (see `documents.py`) built by a host-written adapter — in this
project, `billing_adapter.py`.

Layers, and what each is for:

- **`tax.py`** — rates and the charging rule. Imports nothing at all, so
  it can be used from a script or another framework as-is.
- **`documents.py`** — the dataclasses the host and this module speak in.
  No database.
- **`models.py`** — this module's tables: `BillingProfile`, `Invoice`,
  `InvoiceTaxLine`.
- **`services/`** — the public API. Every function takes `company_id`
  first and filters on it.
- **`routes.py`** — an optional blueprint with the invoice list and the
  printable invoice page. A host that wants its own UI can ignore it and
  use the services directly.

The one rule that keeps it modular: the rest of the application talks to
`billing.services`, `billing.tax` and `billing.documents`, and nothing
deeper. Reaching into `billing.models` from a route works today and
breaks the first time the schema moves.

Wiring a host application up:

    import billing.migrations
    import billing.routes
    from billing.services import invoicing

    billing.routes.register(app, resolve_billable=..., ...)
    billing.migrations.run()          # after db.create_all()

Two things a host must provide: an adapter that turns its own object into
a `Billable`, and a `companies` table for `company_id` to point at.
"""
