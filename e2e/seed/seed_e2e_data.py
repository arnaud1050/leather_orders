#!/usr/bin/env python
"""
Seeds a deterministic, known dataset for the Playwright E2E suite.

Not a general-purpose fixture loader and not `sample_data.py` — that one is
the demo dataset (10 clients, 12 orders, estimated prices, day-offsets from
whatever day it happens to be seeded), meant for humans looking at a demo
instance. This script exists purely so E2E tests can assert on exact,
predictable values: "Ada Lovelace has 2 orders totalling $530", not "some
client has some orders".

Like every script that touches the database, `DATABASE_URL` must already be
set in the environment *before* this imports `app` — the engine is built
during app.py's module-level `create_all()` (root CLAUDE.md hard rule 13).
The Node harness (e2e/global-setup.ts) sets it to a scratch file before
spawning this script, so nothing here ever touches the real
`data/atelier.db`.

Importing `app` also runs the normal boot path (create_all, migrations,
seed_if_empty, ensure_platform_admin) — on an empty scratch database that
creates the usual "By Monsieur" bootstrap tenant with its default admin.
That's left in place deliberately rather than worked around: it's harmless
clutter that no E2E test targets, and its mere presence alongside our own
"E2E Test Studio" company is a small free check that tenant isolation holds
even with two companies in the same database.

Run directly (with the venv active and DATABASE_URL already exported):

    python e2e/seed/seed_e2e_data.py [path-to-data.json]

The data file defaults to e2e/seed/e2e-data.json but can be swapped for a
different fixture set without touching this script — see
E2E_SEED_DATA_FILE in e2e/.env.example.
"""

import json
import os
import sys
from datetime import date, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from app import app  # noqa: E402  (must follow the sys.path fix-up)
from admin.services import add_user  # noqa: E402
from models import Client, Order, OrderLine, Payment, create_company, db  # noqa: E402

DEFAULT_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "e2e-data.json")


def _load_data(data_file: str) -> dict:
    with open(data_file, encoding="utf-8") as fh:
        return json.load(fh)


def _resolve(offset_days: int) -> date:
    return date.today() + timedelta(days=offset_days)


def seed(data: dict) -> None:
    company, admin = create_company(
        data["company"]["name"],
        data["adminUser"]["email"],
        data["adminUser"]["password"],
        admin_full_name=data["adminUser"].get("fullName"),
    )

    # create_company() always sets must_change_password=True (CO4h) — the
    # single provisioning path makes no exception for a test harness. Every
    # other E2E test wants to start from an already-usable session, so this
    # one flag is flipped by hand immediately after provisioning; the forced
    # -change flow itself is covered separately by the "fresh" user below,
    # who keeps the flag exactly as production would set it.
    admin.must_change_password = False
    db.session.add(admin)

    fresh = data["freshUser"]
    error = add_user(company, fresh["email"], fresh["password"], fresh.get("fullName", ""))
    if error:
        raise RuntimeError(f"failed to create fresh user: {error}")

    for client_spec in data["clients"]:
        client = Client(
            company_id=company.id,
            first_name=client_spec["firstName"],
            last_name=client_spec["lastName"],
            email=client_spec.get("email"),
            phone=client_spec.get("phone"),
        )
        db.session.add(client)
        db.session.flush()  # assigns client.id

        for order_spec in client_spec.get("orders", []):
            order = Order(
                client_id=client.id,
                item=order_spec["item"],
                start=_resolve(order_spec["startOffsetDays"]),
                due=_resolve(order_spec["dueOffsetDays"]),
                status=order_spec["status"],
                is_rush=order_spec.get("isRush", False),
            )
            db.session.add(order)
            db.session.flush()  # assigns order.id

            db.session.add(
                OrderLine(
                    order_id=order.id,
                    description=order_spec["item"],
                    quantity=1,
                    unit_price=order_spec["price"],
                )
            )

            for payment_spec in order_spec.get("payments", []):
                db.session.add(
                    Payment(
                        order_id=order.id,
                        amount=payment_spec["amount"],
                        paid_date=_resolve(payment_spec["paidOffsetDays"]),
                        method=payment_spec.get("method", "cash"),
                    )
                )

    db.session.commit()
    total_orders = sum(len(c.get("orders", [])) for c in data["clients"])
    print(
        f"Seeded '{company.name}' (company id={company.id}): "
        f"{len(data['clients'])} clients, {total_orders} orders."
    )


def main() -> int:
    data_file = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DATA_FILE
    with app.app_context():
        print(f"database: {app.config['SQLALCHEMY_DATABASE_URI']}")
        print(f"data file: {data_file}")
        data = _load_data(data_file)
        seed(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
