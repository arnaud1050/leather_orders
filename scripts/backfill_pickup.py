#!/usr/bin/env python
"""
Mark every existing order as picked up at the studio — dry run by default.

`Order.picked_up` arrived defaulting to False ("shipped"), which keeps every
order taxed the way it was before. For a studio whose orders are nearly all
collected in person, that's the wrong answer, and this sets them all to
"picked up" in one go.

**It can change money.** An order's tax is only frozen once its invoice is
issued (billing F2); until then it's recalculated on every read. Switching
an uninvoiced order to "picked up" moves its tax from the client's province
to the studio's, so its total, its balance due and the client's lifetime
value can all move — including on orders long since delivered and paid.
Issued invoices don't move. So this prints every order whose total would
change, and writes nothing unless `--apply` is given.

Run from the project root, with the venv active:

    python scripts/backfill_pickup.py                 # report only
    python scripts/backfill_pickup.py --apply         # write it

On a deployment (the container already has the code and the volume):

    docker compose exec <service> python scripts/backfill_pickup.py

`--company-id N` limits it to one tenant. `DATABASE_URL` has to be set in the
environment, before this imports `app` (hard rule 13). With `--apply`, a
SQLite database is copied to a timestamped `.bak` beside itself first.
"""

import argparse
import os
import shutil
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa  # noqa: E402  (must follow the sys.path fix-up)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_URL = "sqlite:///" + os.path.join(PROJECT_ROOT, "data", "atelier.db")


def _summary(order) -> tuple[float, str]:
    labels = " + ".join(line.label for line in order.tax_lines) or "no tax"
    return order.total, labels


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--apply", action="store_true", help="write the change (default: report only)")
    parser.add_argument("--company-id", type=int, help="only this tenant's orders")
    parser.add_argument("--no-backup", action="store_true", help="skip the SQLite backup taken before --apply")
    args = parser.parse_args()

    url = os.environ.get("DATABASE_URL", DEFAULT_URL)
    print(f"database: {url}")
    path = sa.engine.make_url(url).database if url.startswith("sqlite:") else None
    if args.apply and path and os.path.exists(path) and not args.no_backup:
        backup = f"{path}.{datetime.now().strftime('%Y%m%d-%H%M%S')}.bak"
        shutil.copy2(path, backup)
        print(f"backup:   {backup}")

    from app import app  # noqa: E402  (must follow the backup above)
    from models import Client, Order, db

    with app.app_context():
        query = Order.query.join(Client).filter(Order.picked_up.is_(False))
        if args.company_id is not None:
            query = query.filter(Client.company_id == args.company_id)
        orders = query.order_by(Order.id).all()

        changed = []
        for order in orders:
            before = _summary(order)
            order.picked_up = True
            after = _summary(order)
            if abs(before[0] - after[0]) >= 0.005 or before[1] != after[1]:
                changed.append((order, before, after))

        print(f"\n{len(orders)} order(s) not yet marked picked up; "
              f"{len(orders) - len(changed)} keep the same total.")
        if changed:
            print(f"{len(changed)} would change total (not invoiced, or invoice still a draft):\n")
            for order, (old_total, old_tax), (new_total, new_tax) in changed:
                print(
                    f"  #{order.id:<5} {order.status:<11} {order.client.name[:24]:<24} "
                    f"client in {order.client.province or '-':<3} "
                    f"{old_tax} ${old_total:,.2f} -> {new_tax} ${new_total:,.2f}  "
                    f"(paid ${order.amount_paid:,.2f})"
                )
            print(
                "\nIf the studio's province isn't set in Settings > Invoicing, picked-up\n"
                "orders are charged no tax at all - set it before applying."
            )

        if args.apply:
            db.session.commit()
            print(f"\napplied: {len(orders)} order(s) marked picked up.")
        else:
            db.session.rollback()
            print("\ndry run - nothing written. Re-run with --apply to write it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
