"""
Turning a conversation into the text a model is given.

A pure function over plain dicts — no database, no vendor, no Flask. That's
deliberate: this is where the judgement calls live (what gets sent, in what
order, what gets dropped when there's too much), and every one of them is
testable without a network call or a fixture.

## The shape the host hands over

```python
{
    "subject": "Messenger bag enquiry",
    "counterparty": "jean@example.com",     # optional
    "messages": [                            # oldest first
        {"sender": "Jean Tremblay", "direction": "incoming",
         "sent_at": "2026-07-01", "body": "Do you make messenger bags?"},
        ...
    ],
}
```

`app.py` builds this from an `EmailThread`; nothing in this module knows
that's where it came from.

## Why the whole thread

The **entire** conversation goes, oldest first (`R-2`). Drafting from the
latest message alone produces a reply that re-asks what was answered three
messages ago — worse than no suggestion, because it takes longer to notice
and fix than to write from scratch.

Bodies arrive already trimmed of quoted history (`R-4`) — the host passes
`body_display`, not `body_text`. Without that, a five-message thread ships
message one five times and spends most of the budget on repetition.

## Why direction rather than the display label

Each message is labelled from its `direction`, not from whoever sent it.
The app's own `sender_label` renders outgoing mail as "You", which is right
on a page a human is reading and ambiguous in a prompt — "You" is what the
model calls *itself*. So the transcript says "the studio" and "the client",
which can't be misread from either end.
"""

from ai import config

_STUDIO = "the studio (us)"
_CLIENT = "the client (them)"

_OMITTED = "[…earlier messages omitted for length…]"
_TRUNCATED = "\n[…message truncated for length…]"


def _label(message: dict) -> str:
    who = _CLIENT if message.get("direction") == "incoming" else _STUDIO
    sender = (message.get("sender") or "").strip()
    sent_at = (message.get("sent_at") or "").strip()
    parts = [p for p in (sender, who, sent_at) if p]
    return " — ".join(parts)


def _render_message(message: dict) -> str:
    body = (message.get("body") or "").strip() or "(no text)"
    return f"--- {_label(message)} ---\n{body}"


def render(conversation: dict, max_chars: int | None = None) -> str:
    """The transcript to hand a model, oldest message first.

    Over budget, the **oldest** messages are dropped and a marker takes
    their place (`R-3`): a long thread's recent turns are what a reply has
    to answer, and an opening "hello, do you make bags?" is the least
    costly thing to lose.

    A single message longer than the whole budget is truncated rather than
    dropped — otherwise a thread whose only message is a 100k-character
    brief would render as an empty transcript, and the model would answer
    a question it was never shown.
    """
    budget = config.THREAD_CONTEXT_MAX_CHARS if max_chars is None else max_chars
    messages = list(conversation.get("messages") or [])

    header = f"Subject: {conversation.get('subject') or '(no subject)'}"

    # Newest first while filling, so what's dropped is the oldest; reversed
    # back at the end, because the transcript has to read forwards.
    kept: list[str] = []
    used = len(header)
    dropped_any = False
    for message in reversed(messages):
        rendered = _render_message(message)
        cost = len(rendered) + 2  # the blank line joining it to the next
        if used + cost > budget:
            if not kept:
                # The first (newest) message alone busts the budget. Keep a
                # truncated version rather than nothing at all.
                room = max(budget - used - len(_TRUNCATED) - 2, 200)
                kept.append(rendered[:room] + _TRUNCATED)
            dropped_any = True
            break
        kept.append(rendered)
        used += cost

    kept.reverse()
    if dropped_any:
        kept.insert(0, _OMITTED)

    return "\n\n".join([header, *kept])
