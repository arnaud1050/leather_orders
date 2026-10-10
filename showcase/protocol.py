"""The Showcase sync protocol: how atelier's Showcase sends finished pieces to a studio's website.

showcase_protocol v1.0 -- shared by both ends. The master copy lives in the website_modules repo
(PycharmProjects/website_modules/showcase_protocol.py), with its tests; leather_orders (the
sender, as `showcase/protocol.py`) and each receiving site commit an unchanged copy, written there
by `python sync.py`. To change it: edit the master, bump the version above, run its tests, then
`python sync.py` and commit each project. Rules: REQUIREMENTS.md, "showcase_protocol.py (SP)".

One direction only: atelier calls the website, never the reverse. Every call is one POST of a
JSON body to one endpoint on the site, signed with a secret the two share:

    X-Showcase-Timestamp   unix seconds when it was sent
    X-Showcase-Signature   hex HMAC-SHA256 of b"<timestamp>." + body, keyed by the secret

The four actions (body["action"]), each answered with JSON, {"ok": true, ...} on success:

    ping    "is this connected?"; the site answers with what it is (e.g. {"site": ..., "max_photos": 7})
    upsert  create or update the card for body["piece"]; photos the site lacks travel in
            body["photo_data"] ({photo key: base64}); answered 409 "need_photos" with the keys
            it still lacks, 410 "gone" if the site's admin deleted that card (unless
            body["force"]), 422 "refused" with a reason a person can act on
    remove  delete the card with body["key"]; a key the site doesn't hold is a success
    adopt   put atelier's keys on a card the site already has (from a one-off import), found by
            each photo's "source" path; changes nothing visible; answers {"matches": bool},
            whether the card already shows what an upsert of body["piece"] would

A piece: {"key", "title", "description", "category" (a name, or null), "specs": [[label, value]],
"photos": [{"key", "sha256"}]}, photos in display order, the first being the cover. Keys are
atelier's, random, and never change; a photo's sha256 is of atelier's copy and only ever compared
for equality.

No Flask import, no dependency beyond the stdlib: `handle()` takes the raw headers and body and
returns (status, payload), so each site wraps it in a three-line route, and the site's own models
stay behind the object it passes in (its "mapping"; see `handle`).
"""
import base64
import binascii
import hashlib
import hmac
import json
import time

VERSION = 1
TIMESTAMP_HEADER = "X-Showcase-Timestamp"
SIGNATURE_HEADER = "X-Showcase-Signature"
# How old (or how far in the future) a signed request may be. Wide enough for a slow upload of
# several photos and two clocks a little apart, narrow enough that a captured request can't be
# replayed later.
MAX_SKEW_SECONDS = 300
ACTIONS = ("ping", "upsert", "remove", "adopt")


class ProtocolError(Exception):
    """A refusal the other end can show to a person: an HTTP status, a stable code, a message."""

    status = 400
    code = "bad_request"

    def __init__(self, message, **extra):
        super().__init__(message)
        self.message = message
        self.extra = extra

    def payload(self):
        return {"ok": False, "error": self.code, "message": self.message, **self.extra}


class Unauthorized(ProtocolError):
    status = 401
    code = "unauthorized"


class NotConfigured(ProtocolError):
    status = 503
    code = "not_configured"


class NeedPhotos(ProtocolError):
    """The site lacks some of the piece's photos: send them (their keys are in `missing`)."""

    status = 409
    code = "need_photos"

    def __init__(self, missing):
        super().__init__("Some photos need to be sent.", missing=sorted(missing))


class Gone(ProtocolError):
    """The site's admin deleted this card; it isn't re-created unless the sender insists."""

    status = 410
    code = "gone"


class Refused(ProtocolError):
    """The site can't take this piece as it is (too many photos, a card not found to adopt...)."""

    status = 422
    code = "refused"


# ---------------------------------------------------------------------------------------------
# Signing (both ends)
# ---------------------------------------------------------------------------------------------

def sign(secret, timestamp, body):
    message = str(int(timestamp)).encode() + b"." + body
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def encode(payload):
    """The body to send: compact JSON, UTF-8. Version added when missing."""
    payload = {"version": VERSION, **payload}
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def signed_headers(secret, body, now=None):
    timestamp = int(time.time() if now is None else now)
    return {
        "Content-Type": "application/json",
        TIMESTAMP_HEADER: str(timestamp),
        SIGNATURE_HEADER: sign(secret, timestamp, body),
    }


def photo_hash(data):
    return hashlib.sha256(data).hexdigest()


def encode_photo(data):
    return base64.b64encode(data).decode("ascii")


def _header(headers, name):
    getter = getattr(headers, "get", None)
    value = getter(name) if getter else None
    if value is None:  # a plain dict with other casing
        lowered = name.lower()
        for key, candidate in dict(headers).items():
            if key.lower() == lowered:
                return candidate
    return value


def verify(secret, headers, body, now=None):
    """Raises Unauthorized unless the body is signed with `secret` within MAX_SKEW_SECONDS."""
    raw_timestamp = _header(headers, TIMESTAMP_HEADER) or ""
    signature = _header(headers, SIGNATURE_HEADER) or ""
    if not raw_timestamp.isdigit() or not signature:
        raise Unauthorized("The request isn't signed.")
    timestamp = int(raw_timestamp)
    if abs((time.time() if now is None else now) - timestamp) > MAX_SKEW_SECONDS:
        raise Unauthorized("The request is too old, or a clock is wrong.")
    if not hmac.compare_digest(sign(secret, timestamp, body), signature):
        raise Unauthorized("The signature doesn't match: the two sides have different secrets.")


# ---------------------------------------------------------------------------------------------
# Sending (atelier)
# ---------------------------------------------------------------------------------------------

class SendError(Exception):
    """The website couldn't be reached, or didn't answer as a Showcase receiver."""


def send(url, secret, payload, post=None, timeout=60, now=None):
    """POST one signed request; returns (status, reply). Raises SendError when there's no
    Showcase answer at all (unreachable, timeout, a page that isn't the receiver).

    `post` is `requests.post` unless given (tests pass a fake)."""
    body = encode(payload)
    if post is None:
        import requests

        post = requests.post
    try:
        response = post(url, data=body, headers=signed_headers(secret, body, now=now),
                        timeout=timeout)
    except Exception as error:  # requests' own exceptions, or a fake's
        raise SendError(f"The website couldn't be reached ({error.__class__.__name__}).") from error
    try:
        reply = response.json()
    except ValueError:
        reply = None
    if not isinstance(reply, dict) or "ok" not in reply:
        raise SendError(f"The website answered {response.status_code}, but not as a Showcase "
                        "receiver. Check the address.")
    return response.status_code, reply


# ---------------------------------------------------------------------------------------------
# Receiving (the website)
# ---------------------------------------------------------------------------------------------

def _text(value, field, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise ProtocolError(f"'{field}' must be text.")
    return value


def _key(value, field):
    if not isinstance(value, str) or not value or len(value) > 64 or not value.isalnum():
        raise ProtocolError(f"'{field}' must be a key (letters and digits).")
    return value


def _piece(raw):
    if not isinstance(raw, dict):
        raise ProtocolError("'piece' is missing.")
    photos = raw.get("photos")
    if not isinstance(photos, list) or not photos:
        raise ProtocolError("A piece needs at least one photo.")
    clean_photos, seen = [], set()
    for photo in photos:
        if not isinstance(photo, dict):
            raise ProtocolError("Each photo must be an object.")
        key = _key(photo.get("key"), "photo key")
        sha = photo.get("sha256")
        if not isinstance(sha, str) or len(sha) != 64:
            raise ProtocolError("Each photo needs its sha256.")
        if key in seen:
            raise ProtocolError("A photo is listed twice.")
        seen.add(key)
        clean_photos.append({"key": key, "sha256": sha})
    specs = raw.get("specs") or []
    if not isinstance(specs, list) or not all(
            isinstance(s, list) and len(s) == 2 and all(isinstance(x, str) for x in s) for s in specs):
        raise ProtocolError("'specs' must be [label, value] pairs.")
    return {
        "key": _key(raw.get("key"), "piece key"),
        "title": _text(raw.get("title"), "title", required=True),
        "description": _text(raw.get("description"), "description") or "",
        "category": _text(raw.get("category"), "category"),
        "specs": [list(s) for s in specs],
        "photos": clean_photos,
    }


def _photo_data(raw, piece):
    """{photo key: bytes} for the photos sent, each checked against the hash the piece lists."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ProtocolError("'photo_data' must map photo keys to base64.")
    declared = {p["key"]: p["sha256"] for p in piece["photos"]}
    data = {}
    for key, encoded in raw.items():
        if key not in declared:
            raise ProtocolError("A photo was sent that the piece doesn't list.")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, TypeError, ValueError):
            raise ProtocolError("A photo isn't valid base64.") from None
        if photo_hash(content) != declared[key]:
            raise ProtocolError("A photo arrived damaged (its hash doesn't match).")
        data[key] = content
    return data


def handle(secret, headers, body, site, now=None):
    """Check and dispatch one request. Returns (HTTP status, JSON-able payload).

    `site` is the receiving site's own mapping, with four methods:

        site.ping()                                  -> dict
        site.upsert(piece, photo_data, force)        -> dict   (photo_data: {key: bytes})
        site.remove(key)                             -> dict
        site.adopt(key, piece, sources)              -> dict   (sources: {photo key: path})

    Each may raise NeedPhotos, Gone or Refused (or ProtocolError); anything else propagates, so
    the site's own error handling and logs see real bugs.
    """
    try:
        if not secret:
            raise NotConfigured("This site has no Showcase secret set yet.")
        verify(secret, headers, body, now=now)
        try:
            request = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ProtocolError("The body isn't JSON.") from None
        if not isinstance(request, dict):
            raise ProtocolError("The body must be a JSON object.")
        if request.get("version") != VERSION:
            raise ProtocolError(f"Unsupported protocol version (this site speaks {VERSION}).")
        action = request.get("action")
        if action == "ping":
            result = site.ping()
        elif action == "upsert":
            piece = _piece(request.get("piece"))
            result = site.upsert(piece, _photo_data(request.get("photo_data"), piece),
                                 bool(request.get("force")))
        elif action == "remove":
            result = site.remove(_key(request.get("key"), "piece key"))
        elif action == "adopt":
            piece = _piece(request.get("piece"))
            sources = request.get("sources")
            if not isinstance(sources, dict) or set(sources) != {p["key"] for p in piece["photos"]}:
                raise ProtocolError("'sources' must give a path for every photo.")
            if not all(isinstance(v, str) and v for v in sources.values()):
                raise ProtocolError("Each source must be a path.")
            result = site.adopt(piece["key"], piece, dict(sources))
        else:
            raise ProtocolError(f"Unknown action (expected one of {', '.join(ACTIONS)}).")
    except ProtocolError as error:
        return error.status, error.payload()
    return 200, {"ok": True, **(result or {})}
