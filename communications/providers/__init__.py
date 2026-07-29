"""
Provider implementations — the only place in the codebase that knows a
specific vendor API exists.

Everything outside this package deals in the neutral dataclasses defined
in `base.py` (FetchedThread, FetchedMessage, FetchedEvent) and the
`EmailProvider` / `CalendarProvider` interfaces. That's what makes §17 of
the requirements true rather than aspirational: adding Microsoft Graph
means adding a module here and one line in `registry.py`, and nothing in
services/, sync/, routes.py or the templates changes.
"""

from communications.providers.registry import (  # noqa: F401
    calendar_provider_for, email_provider_for, PROVIDER_LABELS,
)
