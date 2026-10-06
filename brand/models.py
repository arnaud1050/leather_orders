"""
Table: `company_brands` — one row per company that has a logo.

Imports only `db` from the host, so billing's boundary is untouched and any
module could depend on this package; the foreign key names its table by
string, like `features/`.
"""

from models import db


class CompanyBrand(db.Model):
    __tablename__ = "company_brands"

    company_id = db.Column(db.Integer, db.ForeignKey("companies.id"), primary_key=True)
    # Opaque name of the logo file; only brand/logos.py knows it's a path.
    # A fresh name per upload, so a replaced logo gets a new URL.
    logo_filename = db.Column(db.String(80))
