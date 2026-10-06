"""
Where a studio's logo lives, and the limits on it. Env-overridable, same
convention as the modules' config files.
"""

import os

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The bind-mounted data/ volume, like the database and every other upload.
BRAND_DIR = os.environ.get("BRAND_DIR", os.path.join(_BASE_DIR, "data", "brand"))

# The same as nginx's client_max_body_size, so nginx never refuses a file
# the app would have explained, and a photo off a phone fits (BL3).
LOGO_MAX_BYTES = int(os.environ.get("BRAND_LOGO_MAX_BYTES", 10 * 1024 * 1024))

# A logo whose visible pixels average at least this luminance (0 = black,
# 1 = white) counts as light: it needs a dark background (BL12).
LIGHT_LOGO_LUMINANCE = 0.6
# Below this difference between a logo's luminance and its background's,
# the logo won't stand out (BL14).
MIN_CONTRAST = 0.35
