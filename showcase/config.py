"""
Where showcase photos live, and the limits on them.

All overridable via the environment, same convention as `documents/config.py`.
"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The same bind-mounted data/ volume as the database and order documents,
# so photos survive a rebuild for the same reason the database does.
SHOWCASE_DIR = os.environ.get("SHOWCASE_DIR", os.path.join(BASE_DIR, "data", "showcase"))

# What a phone produces is well under this; it catches the accidental RAW
# export or scanned poster, before Pillow spends memory decoding it.
MAX_UPLOAD_BYTES = int(os.environ.get("SHOWCASE_MAX_UPLOAD_BYTES", 25 * 1024 * 1024))
# Decimal, like documents' cap, so the figure reads back as configured.
# Stored copies are re-encoded (~0.5–1.5MB each), so this is a lot of
# pieces; the server's disk is shared with every other site on it.
MAX_TOTAL_BYTES = int(os.environ.get("SHOWCASE_STORAGE_LIMIT_BYTES", 500_000_000))
# Pillow refuses anything past ~179MP on its own (decompression-bomb
# guard); this is our own, lower, line for "not a photo of a piece".
MAX_PIXELS = 60_000_000

MAX_PHOTOS_PER_ITEM = 10
# Long edge of the stored copy, and of its thumbnail.
FULL_EDGE = 2400
THUMB_EDGE = 640
JPEG_QUALITY = 85

# What an upload may be. HEIC isn't here: Pillow can't read it without an
# extra native library, and iOS Safari already converts a photo to JPEG
# when the file input doesn't list HEIC as acceptable.
ACCEPTED_FORMATS = {"JPEG", "MPO", "PNG", "WEBP"}
ACCEPT_ATTRIBUTE = "image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp"
