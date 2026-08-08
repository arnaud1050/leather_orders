"""
The only file in this module that knows Google's image API exists.

Sibling of `openai_client.py`, same three rules: **lazy import** (the app
boots and every other feature works without `google-genai` installed),
**every vendor exception becomes an `AIError`** whose message is written for
the person looking at the screen, and **nothing from the vendor's exception
is passed through verbatim** — an API error can echo request details back,
and this message is rendered in the browser.

Still no registry and no provider base class: one image vendor, named
directly. See `openai_client.py` for why.

## What "nano banana" is, concretely

Gemini's image model, called with a plain Google AI Studio API key. It's
**image-to-image** here: the source mockup goes in as inline image data
alongside the text prompt, and what comes back is a response whose parts
may include text *and* image data. Only the image parts matter; the model
often narrates what it did, and that narration is not the deliverable.
"""

from ai.errors import AIError

_MISSING_LIBRARY = (
    "The google-genai package isn't installed on this server, so images "
    "can't be generated. Add `google-genai` to requirements.txt and redeploy."
)

# Keyed by HTTP status, same approach and same reasoning as
# openai_client._BY_STATUS: it's the one thing the vendor's errors carry
# that doesn't move when they reorganise their exception classes.
_BY_STATUS = {
    400: "Google rejected the request — usually the source image or the "
         "prompt. Try a different image, or simplify the extra details.",
    401: "Google rejected the API key. Check the key saved under Settings → AI.",
    403: "This Google key isn't allowed to use that model. Check the key's "
         "permissions, or try a different model under Settings → AI.",
    404: "Google doesn't recognise that model name. Check the model under "
         "Settings → AI — the available names change from time to time.",
    429: "Google is rate-limiting or the account is out of quota. Wait a "
         "moment and try again, or check the billing on that account.",
    500: "Google had a server error. Try again.",
    503: "Google is overloaded right now. Try again in a moment.",
}

_TIMEOUT = "Google took too long to answer and the request was given up on. Try again."
_GENERIC = "Couldn't reach Google. Try again in a moment."

# A refusal is not a failure of ours, and saying "try again" would be
# wrong — the same request will be refused again.
_NO_IMAGE = (
    "Google answered without an image. That usually means the request was "
    "declined rather than that something broke — try rewording the extra "
    "details, or a different source image."
)


def _translate(exc: Exception) -> AIError:
    """A vendor exception, as a sentence for a person. Matched on status
    code and class *name*, never on imported exception classes — importing
    them would defeat the lazy import this file exists to keep."""
    name = type(exc).__name__
    if "Timeout" in name or "Deadline" in name:
        return AIError(_TIMEOUT)
    status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if status in _BY_STATUS:
        return AIError(_BY_STATUS[status])
    return AIError(_GENERIC)


def _first_image(response) -> tuple[bytes, str] | None:
    """The first inline image in the response, as (bytes, content_type).

    Walked defensively rather than indexed: the response may carry text
    parts, image parts, or both in any order, and a shape that doesn't match
    what we expect has to read as "no image" rather than raise an
    AttributeError the user would see as a crash.
    """
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            data = getattr(inline, "data", None)
            if data:
                content_type = getattr(inline, "mime_type", None) or "image/png"
                return data, content_type
    return None


def generate_image(
    *, api_key: str, model: str, instructions: str,
    source_image: bytes, source_content_type: str, timeout: int,
) -> tuple[bytes, str]:
    """One image from one source image plus a prompt, or an `AIError`.

    Returns `(bytes, content_type)`. No retries: a person is watching, and
    each attempt is a separate charge — silently billing twice for one
    button press is worse than saying "try again".
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise AIError(_MISSING_LIBRARY) from exc

    client = genai.Client(api_key=api_key)
    try:
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=source_image, mime_type=source_content_type),
                instructions,
            ],
            config=types.GenerateContentConfig(
                http_options=types.HttpOptions(timeout=timeout * 1000),  # ms
            ),
        )
    except Exception as exc:  # noqa: BLE001 — every vendor error becomes AIError
        raise _translate(exc) from exc

    image = _first_image(response)
    if image is None:
        raise AIError(_NO_IMAGE)
    return image
