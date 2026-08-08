"""
AI assistance, as a self-contained module.

Two features share one module because they share one thing that matters:
a company-held vendor API key, encrypted at rest, plus a company-held base
prompt that the rest of the app never composes for itself.

- **Inquiry replies** — given a new client's email thread, draft a reply
  asking for what a quote actually needs (dimensions, colours, leather,
  hardware, inspiration images). It fills the compose box; a human edits
  and sends it. Nothing here ever sends mail.
- **Renderings** — given a mockup, pattern or sketch already attached to an
  order, generate a rendered image of the finished piece. The result is a
  *draft* until someone saves it into the order's documents.

Layers, and what each is for:

- **`config.py`** — model ids, timeouts, size caps, where render drafts
  live on disk. Env-overridable, same convention as `documents/config.py`.
- **`crypto.py`** — which key this module's API keys are encrypted under.
- **`models.py`** — `AISettings` (one row per company).
- **`migrations.py`** — this module's own column migrations.
- **`services.py`** — the public API. Every function takes `company_id`
  first and filters on it.
- **`routes.py`** — the Flask blueprint, registered from `app.py`.

**The boundary is billing's, not documents'** — this module imports `db`
from `models.py` and the host's dependency-free `crypto.py`, and nothing
else of the app. It never sees an `Order`, a `Document` or an `EmailThread`:
everything it needs about one arrives as a plain dict through a hook the
host registers, and everything it produces leaves the same way.
`tests/test_ai_boundary.py` enforces this.

That's stricter than `documents/` and `inventory/` need to be, and it's a
deliberate choice: a vendor key plus a prompt is the most portable thing in
this codebase, and the two features are wired to *three* different host
concepts (orders, documents, mail threads). Reaching into any of them
directly would tie the module to all three at once.
"""

from ai import models  # noqa: F401 — registers the table with db.create_all()
