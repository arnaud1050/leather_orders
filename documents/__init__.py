"""
Order document uploads, as a self-contained module.

Real files (Illustrator patterns, renderings, order sheets) attached to an
order, replacing the fake placeholder rows the `Document` model used to
carry. Owns its own table, its own on-disk storage, its own migrations and
its own blueprint — the rest of the app only ever calls `documents.services`.

Layers, and what each is for:

- **`config.py`** — where files live, size/type limits, what's allowed to
  render inline in a browser tab.
- **`storage.py`** — bytes on disk. The model holds an opaque
  `stored_filename`; only this file knows it means a path under
  `config.DOCUMENT_DIR`, same shape as
  `communications/storage/attachment_storage.py`.
- **`validation.py`** — extension allowlist, content sniffing, size and
  per-company quota checks. Runs before a byte touches disk.
- **`thumbnails.py`** — best-effort preview generation. A failure here
  never blocks an upload.
- **`models.py`** — `Document` (table `order_documents`).
- **`migrations.py`** — this module's own column/table migrations, plus a
  one-time drop of the legacy fake `documents` table.
- **`services.py`** — the public API. Every function takes `company_id`
  first and filters on it.
- **`routes.py`** — the Flask blueprint, registered with a host-supplied
  `resolve_order` hook (an app.py function, not imported directly, to
  avoid a circular import).

Unlike `billing/`, this module isn't built to be fully portable — its
`Document.order_id` is a real foreign key to this app's `orders` table
rather than a generic subject id, because nothing here forces the
adapter-indirection billing needed (no circular import to break). It's
"self-contained" in the sense of owning its own schema/storage/routes, not
in the sense of being copy-paste-generic across projects.
"""

from documents import models  # noqa: F401 — registers the table with db.create_all()
