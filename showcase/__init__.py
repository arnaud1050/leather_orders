"""
Showcase: the studio's portfolio of finished pieces.

A self-contained module, sold per company (the `showcase` feature, see
features/). It owns its tables, its photo storage, its blueprint and its
templates, and never imports a host model: what it knows about orders
arrives through hooks the host registers (`hooks.py`, filled from
`showcase_adapter.py`). See showcase/CLAUDE.md for the why and
showcase/REQUIREMENTS.md for the rules.

Layout:
    config.py      where photos live, size limits
    models.py      the tables
    migrations.py  this module's column migrations (none yet)
    hooks.py       the host's order hooks
    images.py      re-encoding uploads: EXIF stripped, orientation applied
    storage.py     photo bytes on disk
    services.py    the public API — company_id first, always
    routes.py      the blueprint, gated by the feature
    kiosk.py       catalog mode's kiosk links (/k/<token>/), a second
                   blueprint authorised by the token, not by a sign-in
    templates/     showcase/list.html, item.html, settings.html, the
                   _editor.html / _order_tab.html partials, and catalog
                   mode's catalog.html + catalog_sw.js
"""

from showcase import models  # noqa: F401 — registers the tables with db.create_all()
