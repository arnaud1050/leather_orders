"""
The one exception type this module raises outward.

Everything that can go wrong here — a missing library, a rejected key, a
rate limit, a timeout, an empty completion — reaches the user as the same
kind of thing: a sentence beside the button saying why nothing happened.
So there's one exception carrying a message written for a person, rather
than a hierarchy the caller would only ever flatten back into one branch.

**The message is shown verbatim in the browser**, which is the constraint
that matters: it must never carry an API key, a prompt, or a vendor stack
trace. `openai_client.py` is where raw vendor exceptions are translated
into these, and that translation is the boundary.
"""


class AIError(Exception):
    """Something went wrong talking to a vendor. The message is safe to
    show to the user and is written for them, not for a log."""
