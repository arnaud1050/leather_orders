"""
Sending pieces to the studio's website (SC40–SC49).

**Nothing here runs by itself.** Every call to the website starts from a
button the studio pressed (SC43): saving, publishing or withdrawing a piece
changes atelier and catalog mode only. What a piece needs on the website is
worked out on every read by comparing it with what was last sent
(`ShowcasePublication`), never stored as a status (hard rule 10).

The wire format and the signing are `protocol.py`, a synced copy of
website_modules' `showcase_protocol.py` (never edit it here). This file is
atelier's side: the connection, the payload, each piece's state, sending,
and linking the one-off import's pieces to the cards they came from.

The payload is built by `piece_payload()` alone, which can only emit a
piece's title, description, category name, shown specs and photo keys:
never a price, client, order or note (SC44, tested).
"""

import hashlib
import json
import secrets
from dataclasses import dataclass
from urllib.parse import urlparse

from models import db

from showcase import crypto, protocol, services, storage
from showcase.models import ShowcaseItem, ShowcasePublication, ShowcaseWebsite, _utcnow

# Tests replace this with a fake that answers like a site; None means
# protocol.send's own default, `requests.post`.
_post = None


def set_post(post) -> None:
    global _post
    _post = post


# ---------------------------------------------------------------------------
# The connection (SC41)
# ---------------------------------------------------------------------------

def get_website(company_id: int) -> ShowcaseWebsite | None:
    return db.session.get(ShowcaseWebsite, company_id)


def check_url(url: str) -> str | None:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        return "Enter the full address of the website's receiver, starting with https://."
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1"):
        return "Use an https:// address: the secret signs every call, but the photos travel in it."
    return None


def connect(company_id: int, url: str) -> tuple[str | None, str | None]:
    """(secret, None) on success — the secret to paste into the website,
    shown once — or (None, error)."""
    error = check_url(url)
    if error:
        return None, error
    if get_website(company_id) is not None:
        return None, "A website is already connected. Delete that connection first."
    secret = secrets.token_urlsafe(32)
    db.session.add(ShowcaseWebsite(company_id=company_id, endpoint_url=url.strip(),
                                   secret_encrypted=crypto.encrypt(secret)))
    db.session.commit()
    return secret, None


def disconnect(company_id: int) -> bool:
    """Deletes the connection. Cards already on the website stay there, and
    what was sent is kept, so connecting the same site again carries on."""
    website = get_website(company_id)
    if website is None:
        return False
    db.session.delete(website)
    db.session.commit()
    return True


def _call(website: ShowcaseWebsite, payload: dict) -> tuple[int, dict]:
    """(status, reply), or raises protocol.SendError with a message."""
    try:
        secret = crypto.decrypt(website.secret_encrypted)
    except crypto.SecretDecryptionError as exc:
        raise protocol.SendError(str(exc)) from exc
    return protocol.send(website.endpoint_url, secret, payload, post=_post)


def _message(reply: dict, status: int) -> str:
    return reply.get("message") or f"The website answered {status}."


def test_connection(company_id: int) -> str | None:
    """Ping the site and remember what it said. Error string or None."""
    website = get_website(company_id)
    if website is None:
        return "No website is connected."
    website.checked_at = _utcnow()
    try:
        status, reply = _call(website, {"action": "ping"})
    except protocol.SendError as exc:
        website.check_error = str(exc)
        db.session.commit()
        return website.check_error
    if not reply.get("ok"):
        website.check_error = _message(reply, status)
        db.session.commit()
        return website.check_error
    website.check_error = None
    website.site_name = str(reply.get("site") or "")[:200] or None
    max_photos = reply.get("max_photos")
    website.max_photos = max_photos if isinstance(max_photos, int) and max_photos > 0 else None
    db.session.commit()
    return None


# ---------------------------------------------------------------------------
# What is sent (SC44)
# ---------------------------------------------------------------------------

def photo_sha(photo) -> str:
    """The stored file's hash, worked out once for photos added before it
    was recorded at upload."""
    if not photo.content_sha256:
        path = storage.path_for(photo.company_id, photo.stored_filename)
        if path is None:
            return ""
        with open(path, "rb") as handle:
            photo.content_sha256 = hashlib.sha256(handle.read()).hexdigest()
        db.session.commit()
    return photo.content_sha256


def piece_payload(company_id: int, item: ShowcaseItem) -> dict:
    """The only thing that ever leaves for the website. Built field by
    field from what may be public (SC5), so nothing else can slip in."""
    return {
        "key": item.public_key,
        "title": item.title,
        "description": item.description or "",
        "category": item.category.label if item.category else None,
        "specs": [[label, value] for label, value in services.shown_specs(company_id, item)],
        "photos": [{"key": p.public_key, "sha256": photo_sha(p)} for p in item.photos],
    }


def payload_hash(piece: dict) -> str:
    return hashlib.sha256(
        json.dumps(piece, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Each piece's state (SC42, SC43)
# ---------------------------------------------------------------------------

@dataclass
class State:
    code: str
    label: str
    action: str | None = None   # what the button does: send, take_off, send_again, send_as_new
    error: str | None = None    # the last send's failure, shown with the state
    warning: str | None = None  # something the site will refuse, said before sending

    @property
    def button(self) -> str | None:
        return ACTION_BUTTONS.get(self.action)


ACTION_BUTTONS = {
    "send": "Send to website",
    "send_again": "Send again",
    "send_as_new": "Send as a new card",
    "take_off": "Take off the website",
}
# What a line of the review page says will happen to the card.
ACTION_OUTCOMES = {
    "send": "will be added",
    "send_again": "will be added again",
    "send_as_new": "will be added as a new card",
    "take_off": "will be taken off",
}


def is_wanted_online(item: ShowcaseItem) -> bool:
    return item.status == "published" and item.visibility == "public"


def state(company_id: int, item: ShowcaseItem, website: ShowcaseWebsite | None = None) -> State | None:
    """Where the piece stands with the website, and the one thing to do
    about it, if any. None when the website isn't concerned with it."""
    website = website if website is not None else get_website(company_id)
    if website is None:
        return None
    pub = item.publication
    wanted = is_wanted_online(item)
    error = pub.last_error if pub is not None else None
    warning = None
    if wanted and website.max_photos and len(item.photos) > website.max_photos:
        warning = (f"The website shows at most {website.max_photos} photos per card and this "
                   f"piece has {len(item.photos)}: it will refuse it until some are deleted.")

    if pub is not None and pub.removed_on_site:
        return State("removed_on_site", "Removed on the website",
                     "send_again" if wanted else None, error, warning)
    if pub is not None and pub.on_site:
        if not wanted:
            return State("to_take_off", "On the website, but no longer public", "take_off", error)
        if payload_hash(piece_payload(company_id, item)) != pub.sent_hash:
            return State("changed", "Changed since last sent", "send", error, warning)
        return State("up_to_date", "On the website, up to date", None, error)
    if item.source_ref and not (pub is not None and (pub.send_as_new or pub.linked)):
        # SC45: an imported piece is never sent until it's linked to its
        # card, or the studio chooses a new card — or it would be doubled.
        return State("not_linked", "Not linked to its website card",
                     "send_as_new" if wanted else None, error, warning)
    if wanted:
        if pub is not None and pub.sent_at and not pub.on_site:
            return State("not_on_website", "Taken off the website", "send", error, warning)
        return State("not_on_website", "Not on the website", "send", error, warning)
    return None


def pending(company_id: int) -> list[tuple[ShowcaseItem, State]]:
    """Pieces with something to send, for the review page (SC43). Imported
    pieces not linked yet aren't offered here: linking comes first, and a
    new card is a choice made on the piece itself."""
    website = get_website(company_id)
    if website is None:
        return []
    rows = []
    for item in ShowcaseItem.query.filter_by(company_id=company_id).order_by(
            ShowcaseItem.updated_at.desc()).all():
        current = state(company_id, item, website)
        if current is not None and current.action in ("send", "take_off"):
            rows.append((item, current))
    return rows


def summary(company_id: int) -> dict:
    counts = {"add": 0, "update": 0, "take_off": 0}
    for _, current in pending(company_id):
        if current.action == "take_off":
            counts["take_off"] += 1
        elif current.code == "changed":
            counts["update"] += 1
        else:
            counts["add"] += 1
    return counts


# ---------------------------------------------------------------------------
# Sending (SC43, SC47)
# ---------------------------------------------------------------------------

def _publication(company_id: int, item: ShowcaseItem) -> ShowcasePublication:
    if item.publication is None:
        item.publication = ShowcasePublication(company_id=company_id)
        db.session.flush()
    return item.publication


def _fail(pub: ShowcasePublication, message: str) -> tuple[bool, str]:
    pub.last_error = message
    db.session.commit()
    return False, message


def send(company_id: int, item: ShowcaseItem, action: str) -> tuple[bool, str]:
    """Carry out one button press for one piece: (succeeded, message).
    `action` must be the one the piece's state offers right now, so a stale
    page can't send something that no longer applies."""
    website = get_website(company_id)
    if website is None:
        return False, "No website is connected."
    current = state(company_id, item, website)
    if current is None or current.action != action:
        return False, "Nothing to send for this piece any more."
    pub = _publication(company_id, item)
    pub.last_attempt_at = _utcnow()
    if action == "send_as_new":
        pub.send_as_new = True

    try:
        if action == "take_off":
            status, reply = _call(website, {"action": "remove", "key": item.public_key})
        else:
            status, reply = _upsert(website, company_id, item, pub,
                                    force=action == "send_again")
    except protocol.SendError as exc:
        return _fail(pub, str(exc))

    if action == "take_off":
        if not reply.get("ok"):
            return _fail(pub, _message(reply, status))
        pub.on_site = False
        pub.sent_hash = pub.sent_photos = None
        pub.last_error = None
        db.session.commit()
        return True, "taken off the website"

    if status == 410:
        # SC48: deleted in the site's own admin; not re-created unasked.
        pub.on_site, pub.removed_on_site = False, True
        pub.sent_hash = pub.sent_photos = None
        pub.last_error = None
        db.session.commit()
        return False, "removed on the website, so it wasn't sent. Use Send again to put it back"
    if not reply.get("ok"):
        return _fail(pub, _message(reply, status))
    was_on_site = pub.on_site
    piece = piece_payload(company_id, item)
    pub.on_site, pub.removed_on_site = True, False
    pub.sent_hash = payload_hash(piece)
    pub.sent_photos = json.dumps({p["key"]: p["sha256"] for p in piece["photos"]})
    pub.sent_at = _utcnow()
    pub.last_error = None
    db.session.commit()
    return True, "updated on the website" if was_on_site else "added to the website"


def _upsert(website, company_id, item, pub, *, force: bool) -> tuple[int, dict]:
    """Sends only the photos the site isn't known to hold; if it says it
    lacks others (its admin deleted one, say), sends those once more."""
    piece = piece_payload(company_id, item)
    sent = json.loads(pub.sent_photos) if pub.sent_photos else {}
    by_key = {p.public_key: p for p in item.photos}
    wanted = [p["key"] for p in piece["photos"] if sent.get(p["key"]) != p["sha256"]]

    def body(keys):
        payload = {"action": "upsert", "piece": piece, "photo_data": _photo_data(by_key, keys)}
        if force:
            payload["force"] = True
        return payload

    status, reply = _call(website, body(wanted))
    if status == 409 and reply.get("error") == "need_photos":
        missing = [k for k in reply.get("missing", []) if k in by_key]
        status, reply = _call(website, body(sorted(set(wanted) | set(missing))))
    return status, reply


def _photo_data(by_key: dict, keys) -> dict:
    data = {}
    for key in keys:
        photo = by_key[key]
        path = storage.path_for(photo.company_id, photo.stored_filename)
        if path is None:
            raise protocol.SendError("A photo's file is missing from this app's storage.")
        with open(path, "rb") as handle:
            data[key] = protocol.encode_photo(handle.read())
    return data


# ---------------------------------------------------------------------------
# Linking the one-off import (SC45)
# ---------------------------------------------------------------------------

def link_candidates(company_id: int) -> list[ShowcaseItem]:
    """Imported pieces not linked yet, whatever their status."""
    if get_website(company_id) is None:
        return []
    return [
        item for item in ShowcaseItem.query.filter(
            ShowcaseItem.company_id == company_id, ShowcaseItem.source_ref.isnot(None),
        ).order_by(ShowcaseItem.id).all()
        if item.publication is None
        or not (item.publication.on_site or item.publication.linked
                or item.publication.send_as_new or item.publication.removed_on_site)
    ]


def _source_path(source_ref: str | None) -> str | None:
    """"bymonsieur.ca/uploads/x.webp" -> "/uploads/x.webp"."""
    if not source_ref or "/" not in source_ref:
        return None
    return source_ref[source_ref.index("/"):]


def link(company_id: int, item: ShowcaseItem) -> tuple[bool, str]:
    """Ask the site to put this piece's keys on the card it was imported
    from, changing nothing visible. Afterwards the card is managed exactly
    as if atelier had created it."""
    website = get_website(company_id)
    if website is None:
        return False, "No website is connected."
    if item not in link_candidates(company_id):
        return False, "already linked"
    sources = {p.public_key: _source_path(p.source_ref) for p in item.photos}
    if not item.photos or None in sources.values():
        return False, ("imported before photo origins were recorded, so its card can't be "
                       "found. Delete it and import again, or send it as a new card")
    piece = piece_payload(company_id, item)
    pub = _publication(company_id, item)
    pub.last_attempt_at = _utcnow()
    try:
        status, reply = _call(website, {"action": "adopt", "piece": piece, "sources": sources})
    except protocol.SendError as exc:
        return _fail(pub, str(exc))
    if not reply.get("ok"):
        return _fail(pub, _message(reply, status))
    pub.on_site, pub.linked, pub.removed_on_site = True, True, False
    pub.sent_photos = json.dumps({p["key"]: p["sha256"] for p in piece["photos"]})
    # Up to date only if the card already shows what atelier would send;
    # otherwise it reads "Changed since last sent", and nothing is sent
    # until the studio presses the button.
    pub.sent_hash = payload_hash(piece) if reply.get("matches") else ""
    pub.sent_at = _utcnow()
    pub.last_error = None
    db.session.commit()
    return True, "linked" if reply.get("matches") else "linked; its card differs, so it reads Changed"
