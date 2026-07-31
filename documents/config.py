"""
Configuration for the documents module — where files live, and the limits
enforced on them.

All overridable via the environment, same convention as
`communications/config.py`.
"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Same data/ directory the SQLite file and communications' attachments live
# in — the bind-mounted volume in both Docker deployments, so uploads
# survive a rebuild for the same reason the database does.
DOCUMENT_DIR = os.environ.get("DOCUMENT_DIR", os.path.join(BASE_DIR, "data", "order_documents"))

# Per-file cap catches an accidental huge upload; per-company cap is the
# real constraint — this app's Docker host runs other sites on the same
# disk, so unbounded storage isn't hypothetical.
MAX_FILE_BYTES = int(os.environ.get("DOCUMENT_MAX_FILE_BYTES", 50 * 1024 * 1024))       # 50MB
# Decimal (1_000_000_000), not 1024**3 — Jinja's filesizeformat renders in
# decimal SI units, so a binary gibibyte would show as "1.1 GB" rather than
# "1.0 GB" on the quota bar. Same number either way for what's actually a
# soft cap; this just keeps the displayed figure matching the config value.
MAX_TOTAL_BYTES = int(os.environ.get("DOCUMENT_STORAGE_LIMIT_BYTES", 1_000_000_000))    # 1GB/company

# Small and explicit on purpose — this is what the studio actually produces
# (Illustrator patterns, renderings, order sheets), not a general-purpose
# file host. Extension is checked first (cheap reject before touching
# bytes); validation.py separately sniffs content against this list.
ALLOWED_EXTENSIONS = {".pdf", ".ai", ".eps", ".svg", ".jpg", ".jpeg", ".png", ".docx", ".xlsx"}

# What the "open" (preview-in-tab) action is allowed to render inline.
# Deliberately excludes .svg even though it's an allowed upload type — SVG
# can carry a <script> tag, and rendering one on our own origin is stored
# XSS. Everything else here is either raster (can't execute script) or PDF
# (rendered by the browser's own PDF viewer, not as HTML).
INLINE_PREVIEWABLE_CONTENT_TYPES = {"application/pdf", "image/jpeg", "image/png"}

THUMBNAIL_MAX_DIMENSION = 240
