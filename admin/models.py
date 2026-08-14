"""
Table: `platform_settings`.

Installation-wide configuration — not any tenant's, not any user's. A
company's own `/settings` has nothing to do with this table and this
table has nothing to do with a company; the two are kept apart the same
way `Company.timezone` and, say, a future rate limit on the whole
deployment would be kept apart.

Brand new table, so `db.create_all()` covers it and there's no
`migrations.py` here — see admin/CLAUDE.md.
"""

from models import db


class PlatformSettings(db.Model):
    """The one row of configuration that belongs to the whole installation.

    A singleton — always `id=1`, created lazily on first read by
    `admin.services.get_platform_settings()`, the same "created on first
    use" contract as the billing letterhead (`invoicing.profile_for()`)
    and the default inventory unit (`_ensure_default_unit()`). A fresh
    database needs no seed row for this to work, and `seed_if_empty()`
    doesn't have to know this table exists.

    `announcement` and `is_active` are two columns rather than one
    blank-means-off field, on purpose: an admin drafting a maintenance
    notice a day ahead wants to save the wording, flip it on when the
    window actually starts, and flip it back off afterwards *without*
    losing the text — the same recurring window comes around again.
    """

    __tablename__ = "platform_settings"

    id = db.Column(db.Integer, primary_key=True)
    announcement = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=False)
