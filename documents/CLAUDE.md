# Documents module (`documents/`)

> Part of the `leather_orders` app — see the root [CLAUDE.md](../CLAUDE.md) for
> stack, conventions and design language.

**This file is structure. [REQUIREMENTS.md](REQUIREMENTS.md) is behaviour** —
storage, upload limits, the allowed-type list and its content sniffing,
thumbnails, inline-vs-download serving, document types, the flat-vs-sectioned
order page layout, tenant isolation and the security posture, each as a
checkable rule. Rules are cited rather than restated; changing behaviour means
changing REQUIREMENTS and the code in the same commit.

Real files attached to an order — Illustrator patterns, renderings, order sheets
— replacing the fake placeholder rows the old `Document` model used to carry.
Own table, own on-disk storage, own migrations, own blueprint; the rest of the
app only ever calls `documents.services`.

**A document belongs to an order, never to a client** (REQUIREMENTS §1).
Client-level documents were discussed and deliberately left out of scope.

## How self-contained it actually is

Less than `billing/`, on purpose. `Document.order_id` is a **real foreign key**
to this app's `orders` table rather than a generic subject id, because nothing
here forces the adapter indirection billing needed — there's no circular import
to break, since nothing on `Order` reads back from this module. Same call as
`inventory/` made.

So "self-contained" here means *owns its own schema, storage and routes*, not
*copy-paste-portable across projects*. Don't add a `documents_adapter.py`
expecting the billing shape; there's nothing for it to decouple.

## Layers

| File | What it's for |
|---|---|
| `config.py` | Where files live, size/type limits, what may render inline. All env-overridable, same convention as `communications/config.py`. |
| `storage.py` | Bytes on disk. The model holds an opaque `stored_filename`; only this file knows it means a path under `config.DOCUMENT_DIR` — same shape as `communications/storage/attachment_storage.py`. |
| `validation.py` | Extension allowlist, content sniffing, size and per-company quota. Runs **before a byte touches disk**. |
| `thumbnails.py` | Best-effort previews. A failure here never blocks an upload. |
| `models.py` | `Document` (table `order_documents`) and `DocumentType` (`document_types`). |
| `migrations.py` | This module's own column migrations, plus the one-time drop of the legacy fake `documents` table. |
| `services.py` | The public API. Every function takes `company_id` first and filters on it. |
| `routes.py` | The blueprint, registered with a host-supplied `resolve_order` hook. |
| `templates/documents/_explorer.html` | The order page's Documents area (flat and sectioned layouts). |
| `templates/documents/_settings_types.html` | The Settings → Orders type list, with drag-reorder. |

## Registration

```python
documents.routes.register(app, resolve_order=get_order_or_404)
```

Same shape as `inventory/`: the host passes its own tenant-checked order lookup
rather than the module importing `app.py` (which would be circular). The order
page route stays in `app.py` and includes the module's partial — the same
"app.py owns the tab route, the module owns what's inside it" split.

`documents/__init__.py` imports `models` so `db.create_all()` sees the tables.

## Notes worth having before you change something

- **`data/order_documents/<company_id>/`** is the storage root — the same
  bind-mounted volume as `atelier.db` and communications' attachments, so
  uploads survive a rebuild for the same reason the database does.
- **The 1GB per-company cap is decimal** (`1_000_000_000`), not `1024**3`,
  specifically so Jinja's `filesizeformat` renders the quota gauge as "1.0 GB"
  rather than "1.1 GB". It's a soft cap either way; this just keeps the
  displayed figure matching the configured one.
- **The cap is a real operational constraint**, not a hypothetical: the Docker
  host runs other sites on the same disk.
- **`.svg` is an allowed upload but is never previewable inline** — an SVG can
  carry a `<script>` tag, and rendering one on our own origin is stored XSS.
  If you extend `INLINE_PREVIEWABLE_CONTENT_TYPES`, that exclusion is the one to
  leave alone.
- **PDF/`.ai` thumbnailing needs `poppler-utils`** (`pdftoppm`, via
  `pdf2image`). Present in both Docker images, **not** guaranteed on a local dev
  machine — `shutil.which` is checked once at import and thumbnailing is skipped
  silently when it's missing, so a local machine without it isn't broken, just
  thumbnail-less.
- **Uploads are not all-or-nothing.** Each file in a batch is validated
  independently and quota is re-checked after each, so "3 of these 5 landed" is
  a normal outcome to render, not an error path.
- **A JPEG or PNG carries an extra "Render" action**, owned by `ai/`, not by
  this module. `_explorer.html` asks `ai_can_render(doc.content_type)` — a
  callable injected by that blueprint's context processor, guarded with
  `is defined` — so this template never imports it and the explorer still
  renders where that blueprint isn't registered. The dialog itself is
  included once by `order_page.html`, not from here: neither module's
  template knows about the other's. A saved rendering comes back through
  `services.upload()` like any other file, so quota, extension allowlist and
  content sniffing all apply to it — see [ai/CLAUDE.md](../ai/CLAUDE.md).
- **No CSRF layer here**, deliberately — consistent with every other mutating
  route in `app.py`, relying on `SESSION_COOKIE_SAMESITE=Lax`.
  `communications/` has its own precisely because that module sends mail and
  disconnects accounts, a different class of risk. See REQUIREMENTS §11 before
  "fixing" this.
- **One-shot notices** use this module's own session key
  (`routes._flash` / `take_notice`), popped by `order_page()` in `app.py`. The
  app has no flash convention; each part that needs one keeps its own key rather
  than adopting Flask's app-wide.
- **Document types are hide-don't-delete**, like `OrderType`/`SourceOption`, but
  module-owned rather than living in root `models.py` — categorising documents is
  this module's concern. The duplicate-label rule was later extended back to
  those two root-owned lists (REQUIREMENTS §13).
