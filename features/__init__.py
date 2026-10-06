"""
Per-company features: what a studio has switched on.

Some parts of the app are meant to be sold separately, as an extra or a
higher tier, so a platform admin turns them on per company from `/admin`.
This package is the one place that answers "does this company have X?".
See features/CLAUDE.md for the why and features/REQUIREMENTS.md for the
rules (FE1–FE9).

Any module may import it, the same allowance as `usage` and `crypto.py`: it
imports only `db` from the host (via `features.models`), never a host model,
the app or a sibling module (`tests/test_features.py` enforces this).

Two ways to ask, both keyed by catalog name:

- `is_enabled(company_id, key)` — in code. An unknown key raises, so a typo
  fails the first test that reaches it instead of reading as "off" forever.
- `has_feature(key)` — in templates, for the signed-in user's company. Put
  there by `register(app)`.

A route that belongs to a feature calls `require(key)` first: 404 when the
company doesn't have it (FE7) — the page doesn't exist for them, rather
than being forbidden.
"""

from datetime import datetime, timezone

from flask import abort
from flask_login import current_user

from features import models  # noqa: F401 — registers the table with db.create_all()
from features.models import CompanyFeature
from models import db

# key -> (label, what it does — both shown on the admin company page)
#
# The complete list. Adding a feature is an entry here, not a migration.
# Keys are stored in `company_features`, so never rename one: retire it and
# add another.
FEATURES: dict[str, tuple[str, str]] = {
    "showcase": (
        "Showcase",
        "A portfolio of finished pieces: published to the studio's website, "
        "shared to social media, and shown full screen at client meetings "
        "and markets.",
    ),
}


def _check(key: str) -> None:
    if key not in FEATURES:
        raise KeyError(f"Unknown feature {key!r} — add it to features.FEATURES first.")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enabled_keys(company_id: int | None) -> set[str]:
    """The catalog features this company has. Empty for no company (FE3):
    a null `company_id` means "not a tenant", never "every feature"."""
    if company_id is None:
        return set()
    rows = db.session.query(CompanyFeature.feature_key).filter_by(company_id=company_id)
    return {key for (key,) in rows if key in FEATURES}


def is_enabled(company_id: int | None, key: str) -> bool:
    _check(key)
    return key in enabled_keys(company_id)


def enabled_since(company_id: int | None, key: str) -> datetime | None:
    """When this company's current spell of the feature began (naive UTC),
    or None when it doesn't have it (FE10). A feature switched off and on
    again starts a new spell."""
    _check(key)
    if company_id is None:
        return None
    return (
        db.session.query(CompanyFeature.enabled_at)
        .filter_by(company_id=company_id, feature_key=key)
        .scalar()
    )


def enabled_by_company() -> dict[int, set[str]]:
    """Every company's features at once, for the admin roster — one query
    rather than one per row."""
    result: dict[int, set[str]] = {}
    for company_id, key in db.session.query(
        CompanyFeature.company_id, CompanyFeature.feature_key,
    ):
        if key in FEATURES:
            result.setdefault(company_id, set()).add(key)
    return result


def set_enabled(company_id: int, key: str, enabled: bool) -> None:
    """Switch one feature on or off for one company. Idempotent.

    Doesn't commit: the caller decides what else belongs in the same
    transaction. Turning a feature off removes the row and nothing else
    (FE5) — whatever the feature stored for this company stays, so turning
    it back on brings it all back.
    """
    _check(key)
    row = CompanyFeature.query.filter_by(company_id=company_id, feature_key=key).first()
    if enabled and row is None:
        db.session.add(CompanyFeature(
            company_id=company_id, feature_key=key, enabled_at=_utcnow(),
        ))
    elif not enabled and row is not None:
        db.session.delete(row)


def _current_company_id() -> int | None:
    if not current_user.is_authenticated:
        return None
    return current_user.company_id


def require(key: str) -> None:
    """404 unless the signed-in user's company has this feature (FE7)."""
    if not is_enabled(_current_company_id(), key):
        abort(404)


def register(app) -> None:
    """Make `has_feature(key)` available to every template."""

    @app.context_processor
    def _inject_has_feature():
        # A callable, like the admin and inventory badge processors: the
        # query only runs on a page that actually asks.
        def has_feature(key: str) -> bool:
            return is_enabled(_current_company_id(), key)

        return {"has_feature": has_feature}
