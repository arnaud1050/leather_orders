"""
Synchronisation: pulling provider data into the app's own tables.

Phase 1 polls (§11). The shape is deliberately compatible with webhooks
later: `sync_account` takes an explicit `since` and is safe to call at any
frequency on any subset of accounts, so a Gmail push notification would
call the same function with a tighter window rather than needing a second
code path.
"""
