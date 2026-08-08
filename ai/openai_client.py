"""
The only file in this module that knows OpenAI's API exists.

Same containment `communications/providers/gmail_provider.py` gives Gmail:
everything above this speaks prompts and strings, so swapping the vendor is
this file plus one call site. There's deliberately **no registry and no
provider base class** — that machinery exists in `communications/` because a
second mail provider was a real prospect, and a registry for one
implementation is a guess about the future dressed up as architecture. If a
second text vendor ever lands, this file's signature is the seam.

**The import is lazy**, matching how communications handles the Google
libraries: the app boots, the settings page renders and every other feature
works on a machine where `openai` isn't installed. Only pressing the button
finds out, and it says so.

Every vendor exception is translated into an `AIError` whose message is
written for the person looking at the screen. Nothing from the vendor's
exception is passed through verbatim — an API error can echo request
details back, and this message is rendered in the browser.
"""

from ai.errors import AIError

_MISSING_LIBRARY = (
    "The openai package isn't installed on this server, so suggestions "
    "can't be generated. Add `openai` to requirements.txt and redeploy."
)

# Keyed by HTTP status, because that's the one thing every OpenAI error
# type carries and it doesn't change when they reorganise their exception
# classes. Anything unlisted falls through to the generic message.
_BY_STATUS = {
    401: "OpenAI rejected the API key. Check the key saved under Settings → AI.",
    403: "This OpenAI key isn't allowed to use that model. Check the key's "
         "permissions, or try a different model under Settings → AI.",
    404: "OpenAI doesn't recognise that model name. Check the model under "
         "Settings → AI — the available names change from time to time.",
    429: "OpenAI is rate-limiting or the account is out of credit. Wait a "
         "moment and try again, or check the billing on that account.",
    500: "OpenAI had a server error. Nothing was charged; try again.",
    503: "OpenAI is overloaded right now. Try again in a moment.",
}

_TIMEOUT = (
    "OpenAI took too long to answer and the request was given up on. "
    "Try again."
)
_GENERIC = "Couldn't reach OpenAI. Try again in a moment."


def _translate(exc: Exception) -> AIError:
    """A vendor exception, as a sentence for a person.

    Matched on status code and class *name* rather than on imported
    exception classes — importing them would mean importing `openai` at
    module level, which is the thing this file exists to avoid.
    """
    name = type(exc).__name__
    if "Timeout" in name:
        return AIError(_TIMEOUT)
    status = getattr(exc, "status_code", None)
    if status in _BY_STATUS:
        return AIError(_BY_STATUS[status])
    if "Connection" in name:
        return AIError(_GENERIC)
    return AIError(_GENERIC)


def generate_reply(
    *, api_key: str, model: str, instructions: str, conversation: str, timeout: int
) -> str:
    """One completion, or an `AIError`. No retries beyond the client's own
    single retry — a person is watching, and a slow second attempt on a
    rate limit is worse than being told to try again."""
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AIError(_MISSING_LIBRARY) from exc

    client = OpenAI(api_key=api_key, timeout=timeout, max_retries=1)
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                # The company's prompt is the system message and the
                # transcript is the user message, so a thread can't talk the
                # model out of its instructions just by containing text that
                # looks like instructions.
                {"role": "system", "content": instructions},
                {"role": "user", "content": conversation},
            ],
        )
    except Exception as exc:  # noqa: BLE001 — every vendor error becomes AIError
        raise _translate(exc) from exc

    try:
        text = (response.choices[0].message.content or "").strip()
    except (AttributeError, IndexError) as exc:
        raise AIError("OpenAI returned a response in a shape this app didn't "
                      "understand. Try again.") from exc

    if not text:
        # A refusal or a length-capped empty completion. Rare, and silently
        # filling the box with nothing would read as the button being broken.
        raise AIError("OpenAI returned an empty reply. Try again, or adjust "
                      "the prompt under Settings → AI.")
    return text
