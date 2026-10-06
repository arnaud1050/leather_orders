"""
Table: `company_features`.

A row means "this company has this feature". No row means it doesn't —
there is no `enabled` column, because a stored false beside an absent row
would be two ways of saying the same thing that could disagree.

Imports only `db` from the host, the allowance every module gets, so a
module can depend on `features` without depending on `Company`. The foreign
key names the table by string for the same reason `documents/` does.
"""

from models import db


class CompanyFeature(db.Model):
    __tablename__ = "company_features"
    __table_args__ = (
        db.UniqueConstraint("company_id", "feature_key", name="uq_company_feature"),
    )

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(
        db.Integer, db.ForeignKey("companies.id"), nullable=False, index=True,
    )
    # A key from `features.FEATURES`. A row whose key has since left the
    # catalog is kept but reads as off (FE6) — removing a feature from the
    # code shouldn't need a data migration to stop it showing.
    feature_key = db.Column(db.String(40), nullable=False)
    # Naive UTC, like every other timestamp here. When the current spell of
    # "on" began — the obvious thing to want once a feature is billed.
    enabled_at = db.Column(db.DateTime, nullable=False)
