"""
Platform administration — provisioning companies and the people in them.

**This is a host-level blueprint, not a self-contained module**, and the
distinction matters because CLAUDE.md's hard rule 4 says a module never
imports host models. `billing/`, `communications/`, `inventory/`,
`documents/` and `ai/` each own a slice of *domain* the app happens to
need, and each is kept ignorant of `Company`, `User` and `Order` on
purpose — that's what makes them liftable.

This package is the opposite thing. Its entire subject matter *is*
`Company` and `User`; a version of it that couldn't see them would have
nothing to administer. So it imports `models.py` freely, and the
boundary tests (`tests/test_billing_boundary.py`,
`tests/test_ai_boundary.py`) deliberately don't cover it. It lives in its
own package rather than in `app.py` only because `app.py` is already 2200
lines and this adds a dozen routes — the same reason a `routes/` split
was left as an option there.

What it does *not* do is read tenant data. Every query in the app filters
`current_user.company_id` (hard rule 1), and a cross-tenant reporting view
would mean auditing all 67 of those call sites. So the pages here read
`Company` and `User` plus a few counts, and the way a platform admin looks
at a studio's actual orders is to impersonate someone inside it — which
goes through the ordinary, already-filtered code path rather than round it.

Layout:
    models.py     PlatformSettings — installation-wide config, a singleton
    services.py   provisioning, user management, impersonation, the guards
    routes.py     the blueprint; registered from app.py with TIME_ZONES
    templates/    admin_companies.html (roster), admin_company.html (one
                  tenant's detail page), admin_users.html (platform staff
                  roster), admin_settings.html (installation-wide config),
                  _admin_nav.html (the Companies/Admin Users/Settings
                  sub-nav shared by the three top-level pages — same
                  convention as _settings_nav.html)
"""

# Brand-new table, so db.create_all() picks it up on its own — this import
# is what puts it in SQLAlchemy's metadata before that runs. Same
# convention as inventory/__init__.py and ai/__init__.py.
from admin import models  # noqa: F401 — registers the table with db.create_all()
