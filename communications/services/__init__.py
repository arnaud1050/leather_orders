"""
The module's public surface.

Everything outside `communications/` should import from here and nowhere
deeper — that's what §5 of the requirements means by "the rest of the
application must communicate through services". Reaching into
`communications.providers` from a route would work today and break the
first time a second provider exists.

    from communications.services import account_service, email_service

Services own transactions the same way the app's routes do: they mutate
the session and the caller commits, except where a service does something
irreversible externally (sending mail), in which case it commits itself so
the local record can't be lost after the fact.
"""
