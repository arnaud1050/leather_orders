"""
Showcase → the studio's website (showcase/REQUIREMENTS.md SC40–SC49).

Nothing touches the network: `website.set_post` hands the sender a fake
`post` that runs the real protocol (`showcase/protocol.py`, website_modules'
`showcase_protocol.py`) against an in-memory site, so signing, the body
format and the site's answers are the real ones.
"""

import html
import json
from io import BytesIO

import pytest
from PIL import Image

import features
from models import db
from showcase import protocol, services, website
from showcase.models import ShowcaseItem, ShowcasePublication, ShowcaseWebsite

URL = "https://studio.example/showcase/receive"


def jpeg(color=(200, 30, 30)) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (40, 20), color).save(buffer, format="JPEG")
    return buffer.getvalue()


class FakeSite:
    """A receiving website, in memory: managed cards by key, a tombstone
    for each card its admin deleted, and some cards of its own (from
    before atelier), found by photo path for `adopt`."""

    def __init__(self, max_photos=7):
        self.secret = None
        self.cards = {}           # piece key -> {"piece": dict, "photos": {photo key: bytes}}
        self.own = {}             # card id -> {"paths": [...], "caption": str, "category": str|None}
        self.tombstones = set()
        self.max_photos = max_photos
        self.requests = []        # every decoded body that reached it
        self.down = False

    # the transport atelier's sender uses
    def post(self, url, data, headers, timeout):
        if self.down:
            raise ConnectionError("no route to host")
        self.requests.append(json.loads(data))
        status, reply = protocol.handle(self.secret, headers, data, self)
        return _Response(status, reply)

    # the mapping protocol.handle calls
    def ping(self):
        return {"site": "studio.example", "max_photos": self.max_photos}

    def upsert(self, piece, photo_data, force):
        key = piece["key"]
        if key in self.tombstones and not force:
            raise protocol.Gone("Deleted in the website's admin.")
        self.tombstones.discard(key)
        if len(piece["photos"]) > self.max_photos:
            raise protocol.Refused(f"This website shows at most {self.max_photos} photos.")
        held = self.cards.get(key, {}).get("photos", {})
        missing = [p["key"] for p in piece["photos"] if p["key"] not in held and p["key"] not in photo_data]
        if missing:
            raise protocol.NeedPhotos(missing)
        photos = {p["key"]: photo_data.get(p["key"], held.get(p["key"])) for p in piece["photos"]}
        self.cards[key] = {"piece": piece, "photos": photos}
        return {}

    def remove(self, key):
        self.cards.pop(key, None)
        return {}

    def adopt(self, key, piece, sources):
        paths = set(sources.values())
        for card_id, card in list(self.own.items()):
            if set(card["paths"]) == paths:
                del self.own[card_id]
                self.cards[key] = {"piece": piece, "photos": {k: b"site's own copy" for k in sources}}
                matches = (card["caption"] == (piece["description"] or piece["title"])
                           and card["category"] == piece["category"])
                return {"matches": matches}
        raise protocol.Refused("No card on the website has exactly these photos.")

    # what its own admin can do
    def admin_delete(self, key):
        self.cards.pop(key)
        self.tombstones.add(key)

    def sent_photo_keys(self):
        """Photo keys that travelled with each upsert, in order."""
        return [sorted(r.get("photo_data", {})) for r in self.requests if r["action"] == "upsert"]


class _Response:
    def __init__(self, status, reply):
        self.status_code, self._reply = status, reply

    def json(self):
        return self._reply


@pytest.fixture
def on(company):
    features.set_enabled(company.id, "showcase", True)
    db.session.commit()
    return company


@pytest.fixture
def site(on):
    fake = FakeSite()
    website.set_post(fake.post)
    secret, error = website.connect(on.id, URL)
    assert error is None
    fake.secret = secret
    yield fake
    website.set_post(None)


def make_piece(company, title="Crossbody bag", photos=1, *, status="published",
               visibility="public", description="In beige suede.", **extra):
    item = services.create_item(company.id, title, **extra)
    services.update_details(company.id, item, title=title, description=description,
                            category_id=None, visibility=visibility, specs={})
    for n in range(photos):
        assert services.add_photo(company.id, item, jpeg((n * 40 % 255, 30, 30)), f"p{n}.jpg",
                                  source_ref=extra.get("source_ref") and
                                  f"studio.example/uploads/{item.id}-{n}.webp") is None
    if status == "published":
        assert services.publish(company.id, item) is None
    return item


def fresh(item):
    db.session.expire_all()
    return db.session.get(ShowcaseItem, item.id)


def state_of(company, item):
    return website.state(company.id, fresh(item))


# --- SC41: the connection ------------------------------------------------------

def test_connecting_shows_the_secret_once_and_stores_it_encrypted(on, logged_in):
    response = logged_in.post("/settings/showcase/website", data={"url": URL})
    assert response.status_code == 302

    first = html.unescape(logged_in.get("/settings/showcase").get_data(as_text=True))
    row = db.session.get(ShowcaseWebsite, on.id)
    secret = website.crypto.decrypt(row.secret_encrypted)
    assert secret in first and secret not in row.secret_encrypted
    assert secret not in logged_in.get("/settings/showcase").get_data(as_text=True)


@pytest.mark.parametrize("url", ["", "studio.example", "ftp://studio.example/x",
                                 "http://studio.example/showcase/receive"])
def test_a_bad_address_is_refused(on, url):
    secret, error = website.connect(on.id, url)
    assert secret is None and error and db.session.get(ShowcaseWebsite, on.id) is None


def test_connecting_sends_nothing_and_send_test_only_pings(site, on):
    assert site.requests == []
    assert website.test_connection(on.id) is None
    assert [r["action"] for r in site.requests] == ["ping"]
    row = db.session.get(ShowcaseWebsite, on.id)
    assert (row.site_name, row.max_photos, row.check_error) == ("studio.example", 7, None)


def test_send_test_reports_a_wrong_secret(site, on):
    site.secret = "something else"
    error = website.test_connection(on.id)
    assert "different secrets" in error
    assert db.session.get(ShowcaseWebsite, on.id).check_error == error


def test_deleting_the_connection_keeps_what_was_sent(site, on):
    item = make_piece(on)
    website.send(on.id, item, "send")
    assert website.disconnect(on.id)
    assert state_of(on, item) is None  # no website, no state
    assert ShowcasePublication.query.count() == 1 and site.cards


# --- SC43: nothing reaches the website without a button ----------------------------------

def test_publishing_editing_and_withdrawing_send_nothing(site, on, logged_in):
    item = make_piece(on, status="draft")
    logged_in.post(f"/showcase/items/{item.id}", data={"title": "Bag", "action": "publish",
                                                       "visibility": "public"})
    logged_in.post(f"/showcase/items/{item.id}", data={"title": "Bag 2", "action": "save"})
    logged_in.post(f"/showcase/items/{item.id}/withdraw")
    assert site.requests == []


def test_a_new_public_piece_waits_for_its_button(site, on):
    item = make_piece(on)
    current = state_of(on, item)
    assert (current.code, current.action, current.button) == (
        "not_on_website", "send", "Send to website")


@pytest.mark.parametrize("status, visibility", [("draft", "public"), ("published", "private"),
                                                ("published", "in_person")])
def test_pieces_not_meant_for_the_website_have_no_state(site, on, status, visibility):
    assert state_of(on, make_piece(on, status=status, visibility=visibility)) is None


def test_the_button_sends_the_piece_with_its_photos(site, on, logged_in):
    item = make_piece(on, photos=2)
    response = logged_in.post(f"/showcase/items/{item.id}/website",
                              data={"action": "send", "notice_section": "website"})
    assert response.status_code == 302

    card = site.cards[item.public_key]
    assert card["piece"]["title"] == "Crossbody bag"
    assert [p.public_key for p in fresh(item).photos] == list(card["photos"])
    assert state_of(on, item).code == "up_to_date"
    assert "added to the website" in logged_in.get(f"/showcase/items/{item.id}").get_data(as_text=True)


def test_an_edit_reads_changed_and_resends_no_photo_it_already_has(site, on):
    item = make_piece(on, photos=2)
    website.send(on.id, item, "send")
    services.update_details(on.id, item, title="Crossbody bag, No. 2", description="",
                            category_id=None, visibility="public", specs={})
    assert state_of(on, item).code == "changed"

    ok, message = website.send(on.id, fresh(item), "send")
    assert ok and message == "updated on the website"
    assert site.cards[item.public_key]["piece"]["title"] == "Crossbody bag, No. 2"
    assert [len(keys) for keys in site.sent_photo_keys()] == [2, 0]


def test_a_new_photo_is_the_only_one_sent(site, on):
    item = make_piece(on, photos=1)
    website.send(on.id, item, "send")
    services.add_photo(on.id, fresh(item), jpeg((1, 2, 3)), "new.jpg")
    item = fresh(item)
    assert state_of(on, item).code == "changed"
    website.send(on.id, item, "send")
    assert site.sent_photo_keys()[-1] == [item.photos[1].public_key]


def test_a_reorder_reads_changed_and_sends_no_photo(site, on):
    item = make_piece(on, photos=2)
    website.send(on.id, item, "send")
    services.reorder_photos(on.id, fresh(item), [fresh(item).photos[1].id])
    assert state_of(on, item).code == "changed"
    website.send(on.id, fresh(item), "send")
    assert site.sent_photo_keys()[-1] == []


def test_a_photo_the_site_lost_is_sent_again(site, on):
    item = make_piece(on, photos=2)
    website.send(on.id, item, "send")
    lost = fresh(item).photos[0].public_key
    del site.cards[item.public_key]["photos"][lost]
    services.update_details(on.id, fresh(item), title="Renamed", description="",
                            category_id=None, visibility="public", specs={})

    ok, _ = website.send(on.id, fresh(item), "send")
    assert ok and site.sent_photo_keys()[-2:] == [[], [lost]]


def test_a_stale_button_sends_nothing(site, on):
    item = make_piece(on)
    ok, message = website.send(on.id, item, "take_off")
    assert not ok and "Nothing to send" in message and site.requests == []


# --- SC44: only what may be public leaves ------------------------------------------------

def test_the_payload_carries_only_the_public_fields(site, on, delivered_order_piece):
    piece = website.piece_payload(on.id, delivered_order_piece)
    assert set(piece) == {"key", "title", "description", "category", "specs", "photos"}
    assert all(set(p) == {"key", "sha256"} for p in piece["photos"])
    blob = json.dumps(piece)
    for private in ("Alarie", "760", "$", "ORD-", "@"):
        assert private not in blob


@pytest.fixture
def delivered_order_piece(on, order):
    order.status = "delivered"
    db.session.commit()
    item = services.start_for_order(on.id, {"id": order.id, "item": order.item, "order_type": None})
    services.add_photo(on.id, item, jpeg(), "a.jpg")
    return item


def test_only_shown_specs_are_sent(site, on):
    from showcase.models import ShowcaseSpecField
    services.add_row(ShowcaseSpecField, on.id, "Leather")
    services.add_row(ShowcaseSpecField, on.id, "Lining")
    leather, lining = services.list_rows(ShowcaseSpecField, on.id)
    item = make_piece(on)
    services.update_details(on.id, item, title=item.title, description="", category_id=None,
                            visibility="public", specs={leather.id: "Suede", lining.id: ""})
    assert website.piece_payload(on.id, fresh(item))["specs"] == [["Leather", "Suede"]]


# --- SC46: taking off, and deleting ------------------------------------------------------

def test_withdrawing_waits_to_be_taken_off(site, on):
    item = make_piece(on)
    website.send(on.id, item, "send")
    services.withdraw(on.id, fresh(item))
    current = state_of(on, item)
    assert (current.code, current.action) == ("to_take_off", "take_off")
    assert item.public_key in site.cards  # still there until the button

    ok, message = website.send(on.id, fresh(item), "take_off")
    assert ok and message == "taken off the website" and item.public_key not in site.cards
    assert state_of(on, item) is None


def test_switching_away_from_public_waits_to_be_taken_off(site, on):
    item = make_piece(on)
    website.send(on.id, item, "send")
    services.update_details(on.id, fresh(item), title=item.title, description="",
                            category_id=None, visibility="in_person", specs={})
    assert state_of(on, item).code == "to_take_off"


def test_publishing_again_after_taking_off_offers_a_new_send(site, on):
    item = make_piece(on)
    website.send(on.id, item, "send")
    services.withdraw(on.id, fresh(item))
    website.send(on.id, fresh(item), "take_off")
    services.publish(on.id, fresh(item))
    current = state_of(on, item)
    assert (current.code, current.label, current.action) == (
        "not_on_website", "Taken off the website", "send")


def test_a_piece_on_the_website_cant_be_deleted(site, on):
    item = make_piece(on)
    website.send(on.id, item, "send")
    services.withdraw(on.id, fresh(item))
    assert services.delete_item(on.id, fresh(item)) == "Take this piece off the website before deleting it."
    website.send(on.id, fresh(item), "take_off")
    assert services.delete_item(on.id, fresh(item)) is None
    assert ShowcasePublication.query.count() == 0


# --- SC47: failures --------------------------------------------------------------------

def test_an_unreachable_site_leaves_the_piece_waiting_with_the_error(site, on):
    item = make_piece(on)
    site.down = True
    ok, message = website.send(on.id, item, "send")
    assert not ok and "couldn't be reached" in message
    current = state_of(on, item)
    assert (current.code, current.action) == ("not_on_website", "send")
    assert "couldn't be reached" in current.error

    site.down = False
    assert website.send(on.id, fresh(item), "send")[0]
    assert state_of(on, item).error is None


def test_a_refusal_is_shown_and_too_many_photos_are_warned_first(site, on):
    site.max_photos = 2
    website.test_connection(on.id)
    item = make_piece(on, photos=3)
    assert "at most 2 photos" in state_of(on, item).warning

    ok, message = website.send(on.id, fresh(item), "send")
    assert not ok and "at most 2 photos" in message
    assert state_of(on, item).error == message


# --- SC48: a card deleted on the website --------------------------------------------------

def test_a_card_deleted_on_the_website_is_not_recreated_unasked(site, on):
    item = make_piece(on)
    website.send(on.id, item, "send")
    site.admin_delete(item.public_key)
    services.update_details(on.id, fresh(item), title="Renamed", description="",
                            category_id=None, visibility="public", specs={})

    ok, message = website.send(on.id, fresh(item), "send")
    assert not ok and "removed on the website" in message
    assert item.public_key not in site.cards
    current = state_of(on, item)
    assert (current.code, current.action, current.button) == (
        "removed_on_site", "send_again", "Send again")
    assert item not in [i for i, _ in website.pending(on.id)]

    ok, _ = website.send(on.id, fresh(item), "send_again")
    assert ok and site.cards[item.public_key]["piece"]["title"] == "Renamed"
    assert state_of(on, item).code == "up_to_date"


# --- SC43: the review page ----------------------------------------------------------------

def test_the_review_page_lists_whats_waiting_and_sends_only_the_ticked(site, on, logged_in):
    a = make_piece(on, "Wallet")
    b = make_piece(on, "Belt")
    on_site = make_piece(on, "Tote")
    website.send(on.id, on_site, "send")
    services.withdraw(on.id, fresh(on_site))
    make_piece(on, "Draft", status="draft")

    page = html.unescape(logged_in.get("/showcase/website").get_data(as_text=True))
    assert "Wallet" in page and "Belt" in page and "Tote" in page and "Draft" not in page
    assert "will be taken off" in page

    logged_in.post("/showcase/website", data={"item_id": [str(a.id), str(on_site.id)]})
    assert set(site.cards) == {a.public_key}
    assert state_of(on, b).code == "not_on_website"
    results = html.unescape(logged_in.get("/showcase/website").get_data(as_text=True))
    assert "2 of 2 done" in results and "taken off the website" in results


def test_the_review_page_ignores_another_companys_pieces(site, on, other_company, logged_in):
    features.set_enabled(other_company.id, "showcase", True)
    theirs = make_piece(other_company, "Not yours")
    logged_in.post("/showcase/website", data={"item_id": [str(theirs.id)]})
    assert site.requests == []


def test_the_showcase_page_counts_whats_waiting(site, on, logged_in):
    make_piece(on, "Wallet")
    item = make_piece(on, "Belt")
    website.send(on.id, item, "send")
    services.update_details(on.id, fresh(item), title="Belt 2", description="",
                            category_id=None, visibility="public", specs={})
    page = logged_in.get("/showcase").get_data(as_text=True)
    assert "1 to add, 1 to update" in page and "Review and send" in page


def test_without_a_website_nothing_mentions_it(on, logged_in):
    item = make_piece(on)
    assert "Send to website" not in logged_in.get(f"/showcase/items/{item.id}").get_data(as_text=True)
    assert "Review and send" not in logged_in.get("/showcase").get_data(as_text=True)
    assert logged_in.get("/showcase/website").status_code == 302


def test_the_website_pages_belong_to_the_feature(company, logged_in):
    for path in ("/showcase/website", "/showcase/website/link"):
        assert logged_in.get(path).status_code == 404
    assert logged_in.post("/settings/showcase/website", data={"url": URL}).status_code == 404


# --- SC45: linking the one-off import ------------------------------------------------------

def imported(company, title="Pochette", photos=2, **kwargs):
    return make_piece(company, title, photos, source_ref=f"studio.example/uploads/{title}.webp",
                      **kwargs)


def site_card_for(site, item, caption=None, category=None):
    item = fresh(item)
    site.own[item.id] = {"paths": ["/" + p.source_ref.split("/", 1)[1] for p in item.photos],
                         "caption": caption if caption is not None else item.description,
                         "category": category}


def test_an_imported_piece_is_never_sent_until_linked(site, on):
    item = imported(on)
    current = state_of(on, item)
    assert (current.code, current.action) == ("not_linked", "send_as_new")
    assert website.pending(on.id) == []
    assert website.send(on.id, fresh(item), "send")[0] is False and site.requests == []


def test_linking_adopts_the_card_without_sending_content(site, on, logged_in):
    item = imported(on)
    site_card_for(site, item)
    page = logged_in.get("/showcase/website/link").get_data(as_text=True)
    assert "Pochette" in page

    logged_in.post("/showcase/website/link", data={"item_id": [str(item.id)]})
    adopt = site.requests[-1]
    assert adopt["action"] == "adopt" and "photo_data" not in adopt
    assert sorted(adopt["sources"].values()) == sorted(
        f"/uploads/{item.id}-{n}.webp" for n in range(2))
    assert state_of(on, item).code == "up_to_date"
    assert website.link_candidates(on.id) == []


def test_a_linked_card_that_differs_reads_changed_until_sent(site, on):
    item = imported(on)
    site_card_for(site, item, caption="The website's older caption")
    ok, message = website.link(on.id, fresh(item))
    assert ok and "differs" in message
    assert state_of(on, item).code == "changed"
    assert [r["action"] for r in site.requests] == ["adopt"]

    website.send(on.id, fresh(item), "send")
    assert site.sent_photo_keys()[-1] == []  # the site already holds every photo


def test_a_linked_piece_taken_off_and_published_again_is_sent_normally(site, on):
    item = imported(on)
    site_card_for(site, item)
    website.link(on.id, fresh(item))
    services.withdraw(on.id, fresh(item))
    website.send(on.id, fresh(item), "take_off")
    assert state_of(on, item) is None  # withdrawn, off the website: nothing to do

    services.publish(on.id, fresh(item))
    current = state_of(on, item)
    assert (current.code, current.action) == ("not_on_website", "send")
    assert website.send(on.id, fresh(item), "send")[0] and item.public_key in site.cards


def test_a_card_that_cant_be_found_stays_unlinked(site, on):
    item = imported(on)
    ok, message = website.link(on.id, item)
    assert not ok and "exactly these photos" in message
    current = state_of(on, item)
    assert current.code == "not_linked" and "exactly these photos" in current.error
    assert item in website.link_candidates(on.id)


def test_an_unlinked_piece_can_be_sent_as_a_new_card(site, on):
    item = imported(on)
    ok, _ = website.send(on.id, item, "send_as_new")
    assert ok and item.public_key in site.cards
    assert state_of(on, item).code == "up_to_date"
    assert website.link_candidates(on.id) == []


def test_a_piece_imported_without_photo_origins_cant_be_linked(site, on):
    # As demo's first imports were: the piece knows its card, its photos don't.
    item = services.create_item(on.id, "Old import", source_ref="studio.example/uploads/old.webp")
    services.add_photo(on.id, item, jpeg(), "old.jpg")
    services.publish(on.id, item)
    ok, message = website.link(on.id, item)
    assert not ok and "imported before photo origins" in message and site.requests == []


def test_the_import_refuses_once_a_website_is_connected(site, on, capsys):
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "import_showcase_from_site.py"
    spec = importlib.util.spec_from_file_location("importer", path)
    importer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(importer)
    db.session.commit()

    assert importer.main(["--company", str(on.id), "--site", "https://studio.example"]) == 2
    assert "won't run" in capsys.readouterr().err


def test_remove_keeps_imported_pieces_whose_card_is_managed(site, on):
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "import_showcase_from_site.py"
    spec = importlib.util.spec_from_file_location("importer", path)
    importer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(importer)
    linked, loose = imported(on, "Linked"), imported(on, "Loose")
    site_card_for(site, linked)
    website.link(on.id, fresh(linked))

    assert importer.remove(on.id, "https://studio.example", apply=True, log=lambda *_: None) == (1, 2)
    assert fresh(linked) is not None and fresh(loose) is None


# --- SC40: keys ----------------------------------------------------------------------------

def test_every_piece_and_photo_gets_its_own_key(on):
    a, b = make_piece(on, photos=2), make_piece(on)
    keys = [a.public_key, b.public_key] + [p.public_key for p in a.photos + b.photos]
    assert len(set(keys)) == 5 and all(len(k) == 32 for k in keys)


def test_the_migration_keys_rows_that_predate_keys(app, on):
    import sqlalchemy as sa
    from showcase import migrations
    item = make_piece(on, photos=1)
    db.session.execute(sa.text("UPDATE showcase_items SET public_key = NULL"))
    db.session.execute(sa.text("UPDATE showcase_photos SET public_key = NULL, content_sha256 = NULL"))
    db.session.commit()

    migrations.run_migrations()
    item = fresh(item)
    assert len(item.public_key) == 32 and len(item.photos[0].public_key) == 32
    # The hash is worked out from the file the first time it's needed.
    assert website.photo_sha(item.photos[0]) == protocol.photo_hash(
        open(services.storage.path_for(on.id, item.photos[0].stored_filename), "rb").read())
