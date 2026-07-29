"""
Provider lookup by name.

The indirection earns its keep in exactly one way: `EmailAccount.provider`
is a string in the database, and this is the single place that turns it
into a class. Adding Microsoft Graph is a new module plus two dict entries
here — no `if provider == "gmail"` anywhere else to find and update.

Imports are deferred into the functions because the Gmail modules import
Google's libraries at module level, and this package has to stay importable
on a machine where those aren't installed (see config.dependencies_ok).
"""

from communications.providers.base import ProviderError

# Display names for the UI. Also doubles as "what could a company connect",
# so a provider that's implemented but not listed here simply isn't offered.
PROVIDER_LABELS = {
    "gmail": "Gmail",
}


def email_provider_for(account):
    """The EmailProvider implementation for an account's provider string."""
    if account.provider == "gmail":
        from communications.providers.gmail_provider import GmailProvider

        return GmailProvider(account)
    raise ProviderError(f"No email provider registered for {account.provider!r}.")


def calendar_provider_for(account):
    """The CalendarProvider implementation for an account's provider string."""
    if account.provider == "gmail":
        from communications.providers.gmail_provider import GoogleCalendarProvider

        return GoogleCalendarProvider(account)
    raise ProviderError(f"No calendar provider registered for {account.provider!r}.")
