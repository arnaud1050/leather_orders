# Documents module — business requirements

Living record of the rules this module implements, kept for two reasons:
so future changes don't silently drop a rule nobody wrote down, and so
there's a checklist to test against. Each rule below is implemented and
covered by `tests/test_documents.py` (and `tests/test_settings_options.py`
for the two root-level rules at the end) unless noted otherwise. When a
rule changes, update this file in the same change.

## 1. What a document is

- A document belongs to exactly one **order** (not a client, not the
  company generally). Client-level documents were discussed but are out of
  scope for this module as built.
- A document optionally belongs to one **document type** (category) — see
  §4. Untyped is a valid, permanent state, not just a migration artifact.
- A document can leave this module **as bytes** (`services.read_bytes`,
  over `storage.read`), for a caller that has to hand the file to something
  other than the browser — today, mailing one out from the compose form's
  attachment picker (`communications/REQUIREMENTS.md` SEND-7 … SEND-11).
  `services.get_for_company` is the tenant-checked lookup that goes with
  it, for a caller holding an id but no order. Serving a document *to* the
  browser still goes through `storage.path_for` + `send_file`, which
  streams rather than reading the whole file into memory.

## 2. Storage

- Files live on the local filesystem (`data/order_documents/<company_id>/`),
  not S3 and not a cloud-sync service — chosen deliberately over both for
  the current single-host deployment (see the module's own docstrings for
  the reasoning).
- On-disk filenames are opaque (`uuid4().hex` + sanitized extension), never
  the original filename. The original name is display-only metadata on the
  `Document` row.
- Thumbnails, when generated, live in a `thumbnails/` subdirectory of the
  same per-company folder.
- Path access is re-validated on every read (containment check), not just
  trusted from the stored filename.

## 3. Upload limits

- **Per-file cap: 50MB** (`52,428,800` bytes). A file over this is
  rejected with a message naming the file and its actual size.
- **Per-company total cap: 1GB** (`1,000,000,000` bytes, decimal — chosen
  specifically so the quota gauge reads "1.0 GB", not "1.1 GB"). A file
  that would push the company over this is rejected the same way.
- The cap exists because the deployment host runs other sites on the same
  disk — this is a real operational constraint, not a hypothetical one.
- A multi-file upload is **not all-or-nothing**: each file is validated
  independently, so 3 of 5 fitting under quota while 2 don't is a normal,
  expected outcome, not a failure of the whole batch. Quota is re-checked
  after each file in the same batch (so file 5 can't slip in under a quota
  that files 1-4 already used up).
- An outer `MAX_CONTENT_LENGTH` (200MB) on the Flask app rejects a wildly
  oversized request before it's even fully buffered, ahead of the
  per-file/per-company checks above.

## 4. Allowed file types

Allowed extensions: `.pdf`, `.ai`, `.eps`, `.svg`, `.jpg`, `.jpeg`, `.png`,
`.docx`, `.xlsx`. Nothing else is accepted — this is a small, explicit
list matching what the studio actually produces, not a general-purpose
file host.

Content is **sniffed, not trusted** from the extension or browser-supplied
MIME type:

| Extension | Check |
|---|---|
| `.jpg`/`.jpeg`/`.png` | Decodable by Pillow (`Image.open(...).verify()`) |
| `.pdf` | Starts with `%PDF-` |
| `.ai` | PDF-compatible (`%PDF-`) **or** pure PostScript (`%!PS-Adobe-`) — both valid, tracked differently for thumbnailing (see §5) |
| `.eps` | Starts with `%!PS-Adobe-` |
| `.svg` | Valid UTF-8, contains an `<svg` root within the first 2000 characters |
| `.docx`/`.xlsx` | Valid zip archive containing `[Content_Types].xml` |

A file that fails its own extension's check is rejected with a specific
message (e.g. "That doesn't look like a valid .docx file"), not a generic
error — and never a 500.

## 5. Thumbnails

- Best-effort only. A thumbnail failing to generate **never** blocks the
  upload itself.
- Images (`.jpg`/`.jpeg`/`.png`): via Pillow, resized to fit within
  240×240.
- PDF and PDF-compatible `.ai`: via `pdftoppm` (through `pdf2image`),
  first page only, same resize. Requires the `poppler-utils` system
  package — present in both Docker images, not guaranteed on a local dev
  machine.
- No thumbnail is attempted for `.eps`, pure-PostScript `.ai`, `.svg`,
  `.docx`, `.xlsx` — the UI shows a generic extension badge (e.g. "PDF",
  "DOCX") instead.
- If `pdftoppm` isn't on `PATH` (checked via `shutil.which` once, at
  import), PDF/`.ai` thumbnailing is skipped silently rather than raising.

## 6. Serving files

- **Download** (`documents.download`) always forces
  `Content-Disposition: attachment` with `mimetype="application/octet-stream"`
  — never rendered inline, regardless of type.
- **Open/view** (`documents.view`) renders inline **only** for content
  types in the previewable set: `application/pdf`, `image/jpeg`,
  `image/png`. Everything else redirects to the download route instead.
- `.svg` is deliberately **excluded** from the previewable set even though
  it's an allowed upload type — an SVG can carry a `<script>` tag, and
  rendering one on the app's own origin would be stored XSS. It can only
  ever be downloaded, never opened inline.
- The "Open" icon only appears in the UI when the document's type is in
  the previewable set — it isn't offered for types that would just bounce
  to a download anyway.

## 7. Upload UX

- Selecting a file **uploads immediately** — there is no separate "Upload"
  button to click. (There was one originally; removed at the user's
  request once auto-submit was confirmed working.)
- The file picker itself is a tile styled and sized identically to a
  document card in the grid (dashed border, circular "+" icon, "Add file"
  label) — not the browser's native, unstyled file input control.
- Multi-file selection is supported (`multiple` on the file input); one
  `change` event covers the whole batch, one upload request, one redirect.
- Rejections (disallowed type, oversized, over quota, failed content
  sniff) are surfaced as a visible message on the page after redirect —
  never a silent no-op and never a 500. Delivered via a one-shot
  session-scoped notice (`documents.routes._flash` / `take_notice`),
  popped and shown once.
- A storage-quota gauge (bar + "`X` of `Y` used") is always visible at the
  bottom of the Documents area, recomputed on every page load.

## 8. Document types (categories)

- Company-configurable, managed at **Settings → Orders → "Document
  types"**.
- **Hide, don't delete**: a type can't be deleted once any document
  references it (`can_delete` = no `Document` row has that
  `document_type_id`). Hiding (`is_active=False`) is the only way to
  retire one from new use while what's already tagged with it keeps its
  place.
- **Duplicate labels are rejected**, case-insensitively and trimmed,
  checked against *all* of a company's types — active and hidden alike
  (a hidden type is still a real row someone could unhide into a
  collision). The same label **is** allowed for a different company —
  the check is scoped per-tenant. Rejection shows a message ("A document
  type called "X" already exists.") rather than failing silently.
- **Reordered by drag-and-drop** in settings (HTML5 native drag events,
  no library). The new order **saves immediately on drop** — no separate
  "Save order" button — via a `fetch()` POST of the full ordered id list
  to `/settings/document-types/reorder`. Ids that don't belong to the
  current company are silently ignored, not trusted.

## 9. Order page: flat vs. sectioned layout

- **Zero document types configured for the company**: the order page's
  Documents area is a single flat grid with one "Add file" tile. This is
  unchanged from before document types existed — a company that hasn't
  touched the feature sees no difference at all.
- **One or more document types exist for the company** (active or
  hidden — see §8): the flat grid is replaced entirely by sections, one
  per type, in `sort_order`:
  - Each **active** type always gets a section, even if empty on this
    particular order, with its own "Add file" tile scoped to that type
    (uploads through it get that `document_type_id`).
  - A **hidden** type gets a section **only** on an order that already
    has a document tagged with it — merged into the same sort-order
    sequence as the active types (not forced to the end) — but with
    **no** add tile, since hidden means "not offered for new use."
  - A hidden type with **no** documents on a given order gets no section
    on that order at all.
  - An **"Other"** section is always appended **last**, once any document
    type exists for the company (regardless of whether this order
    currently has any untyped documents) — it holds documents with no
    type, whether pre-dating this feature or deliberately left
    uncategorized. **It has its own "Add file" tile**, same as an active
    type's section: uploading through it explicitly keeps the document
    untyped (`document_type_id = None`), so it stays reachable as a
    permanent "no specific category" option rather than only appearing
    after something already landed there by accident.

## 10. Tenant isolation

- Every route is scoped to `current_user.company_id`. An id belonging to
  another company: 404s on document download/view/delete; is silently
  ignored (not applied) in the reorder payload; is silently treated as "no
  type" if passed as `document_type_id` on upload.
- `Document.company_id` is denormalized (not read via a join through
  order → client → company) so every query can filter by company directly.

## 11. Security posture

- **No CSRF token layer** on any route in this module — deliberately
  consistent with every other mutating route in `app.py` (`edit_order`,
  `delete_payment`, etc.), which rely on the app-wide
  `SESSION_COOKIE_SAMESITE=Lax` rather than a token. This is a conscious
  choice, not an oversight — `communications/` has its own CSRF layer
  specifically because *that* module sends mail and disconnects accounts,
  a different class of risk.
- Generated (never user-supplied) filenames on disk close off path
  traversal via a crafted upload name.

## 12. Migrations / legacy data

- The old placeholder `documents` table (fake "Mockup"/"Invoice" rows,
  no real files behind them) is **dropped outright** on any install that
  still has it — nothing in it was worth migrating forward.
- `order_documents.document_type_id` is added via a column migration for
  installs that had the table before document types existed.

## 13. Duplicate-label prevention elsewhere (same rule, different tables)

Extended to two root-owned (non-`documents/`-module) settings lists on the
user's request, using the identical case-insensitive / active+hidden /
per-tenant rule as §8:

- **Order types** (Settings → Orders, `OrderType` in `models.py`).
- **"How did you hear about us?" options** (Settings → Clients,
  `SourceOption` in `models.py`).

Both show the same "already exists" style message, via a small
settings-page-scoped one-shot notice (`app.py`'s `_flash_settings_notice`
/ `_take_settings_notice`) separate from the documents module's own
notice — each part of the app that needs this keeps its own session key
rather than the app adopting a single global flash convention.

## Implementation reference

| Concern | File |
|---|---|
| Config (limits, allowed types, previewable types) | `documents/config.py` |
| `Document` / `DocumentType` models | `documents/models.py` |
| Validation + content sniffing | `documents/validation.py` |
| Thumbnail generation | `documents/thumbnails.py` |
| Storage (disk paths) | `documents/storage.py` |
| Public API (upload, sections, type CRUD, reorder) | `documents/services.py` |
| Routes / blueprint | `documents/routes.py` |
| Order page partial (flat + sectioned layout) | `documents/templates/documents/_explorer.html` |
| Settings partial (type list, drag-and-drop) | `documents/templates/documents/_settings_types.html` |
| Column/table migrations | `documents/migrations.py` |
| Tests | `tests/test_documents.py`, `tests/test_settings_options.py` |
