"""
Communications integration module.

A self-contained, provider-agnostic layer for connecting a company's
mailbox and calendar. The rest of the app never touches Gmail (or any
other provider) directly — it goes through `communications.services`:

    from communications.services import email_service
    email_service.send_email(account, to=..., subject=..., body=...)

Layout, and why it's split this way:

    config.py       env-driven settings + "is this module usable at all"
    crypto.py       encrypt/decrypt for OAuth tokens at rest
    models.py       EmailAccount / EmailThread / EmailMessage / ...
    oauth/          the OAuth dance, per identity provider
    providers/      the only code that knows a vendor API exists
    services/       what the rest of the app calls
    sync/           scheduled/manual mailbox + calendar synchronisation
    storage/        where attachment bytes live
    jobs.py         background job entry points
    routes.py       the Flask blueprint (settings UI, OAuth callback, ...)

Everything hangs off `company_id`, matching the tenant boundary the rest
of the app already uses (see CLAUDE.md, "Multi-tenancy").

Importing this package must never fail because Google's libraries aren't
installed — the app has to boot regardless (see config.DEPENDENCIES_OK),
so vendor imports live inside `providers/` and `oauth/`, not here.
"""

from communications import models  # noqa: F401 — registers tables with db.create_all()
