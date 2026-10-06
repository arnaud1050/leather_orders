"""
Table: `platform_settings`.

Installation-wide configuration — not any tenant's, not any user's. A
company's own `/settings` has nothing to do with this table and this
table has nothing to do with a company; the two are kept apart the same
way `Company.timezone` and, say, a future rate limit on the whole
deployment would be kept apart.

`db.create_all()` created the table; the schedule and time zone columns
were added after it shipped, so they're in `admin/migrations.py` (hard
rule 12).
"""

from models import DEFAULT_TIMEZONE, db


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

    `starts_at` / `ends_at` optionally bound *when* an active banner shows,
    so a notice can be set up ahead of time and take itself down after.
    Both are naive UTC, like every other timestamp in the app, and either
    may be null for "no bound on that side". Whether the banner is showing
    right now is derived from the switch, the window and the clock on
    every read — nothing flips `is_active` when a window opens or closes
    (hard rule 10), so there's no job to run and nothing to drift.
    """

    __tablename__ = "platform_settings"

    id = db.Column(db.Integer, primary_key=True)
    announcement = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=False)
    starts_at = db.Column(db.DateTime)
    ends_at = db.Column(db.DateTime)
    # The zone platform staff read and type times in. Staff have no
    # company, so no `Company.timezone` to borrow. Display only: stored
    # times stay UTC, so changing it moves no instant.
    timezone = db.Column(db.String(60), nullable=False, default=DEFAULT_TIMEZONE)
