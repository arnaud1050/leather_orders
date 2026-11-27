"""
Turn off one account's two-factor sign-in from the server (REQUIREMENTS CO4v).

The last resort, for when nobody can do it from /admin: the only platform
admin has lost their phone *and* their recovery codes. Reaching the server
already takes the SSH key, so a stolen password still isn't enough. Run it
inside the app's container, so it writes that deployment's database:

    docker compose exec atelier-orders python scripts/reset_two_factor.py someone@studio.ca

A platform admin reset this way is walked through setting it up again at
their next sign-in (CO4u). Prints what it did; changes nothing for an
address that has no account or no two-factor.

`DATABASE_URL`, when used, has to be set in the environment before this
imports `app` (hard rule 13).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (must follow the sys.path fix-up)
import two_factor  # noqa: E402
from models import User, db, normalise_email  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python scripts/reset_two_factor.py EMAIL", file=sys.stderr)
        return 2
    email = normalise_email(argv[1])
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        if user is None:
            print(f"No account for {email}.", file=sys.stderr)
            return 1
        if not user.two_factor_enabled:
            print(f"{email} doesn't have two-factor sign-in on. Nothing changed.")
            return 0
        two_factor.disable(user)
        db.session.commit()
        print(f"Two-factor sign-in turned off for {email}.")
        if user.is_staff:
            print("They'll be asked to set it up again at their next sign-in.")
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
