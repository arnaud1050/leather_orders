"""
The showcase module: feature gate, settings, the order tab and its
reminder, the item editor, photos and their metadata, and the boundary.

Defends showcase/REQUIREMENTS.md (SC1–SC24).
"""

import ast
import html
import json
import os
import pathlib
import re
from datetime import date, timedelta
from io import BytesIO

import pytest
from PIL import Image

import features
from models import Order, OrderType, db
from showcase import config, services
from showcase.models import (
    ShowcaseCategory, ShowcaseDismissal, ShowcaseItem, ShowcaseKioskLink, ShowcasePhoto,
    ShowcaseSpecField,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent


# --- helpers & fixtures -----------------------------------------------------

def jpeg(width=40, height=20, *, gps=False, orientation=None, color=(200, 30, 30)) -> bytes:
    image = Image.new("RGB", (width, height), color)
    exif = Image.Exif()
    if gps:
        exif[0x8825] = {1: "N", 2: (49.0, 16.0, 0.0), 3: "W", 4: (123.0, 7.0, 0.0)}
        exif[0x010F] = "PhoneMaker"  # Make
    if orientation:
        exif[0x0112] = orientation
    buffer = BytesIO()
    image.save(buffer, format="JPEG", exif=exif.tobytes())
    return buffer.getvalue()


def png_with_alpha() -> bytes:
    image = Image.new("RGBA", (30, 30), (0, 0, 0, 0))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def stored_image(photo: ShowcasePhoto) -> Image.Image:
    path = os.path.join(config.SHOWCASE_DIR, str(photo.company_id), photo.stored_filename)
    with open(path, "rb") as handle:
        return Image.open(BytesIO(handle.read()))


@pytest.fixture
def on(company):
    """The company has the feature (switched on yesterday, so today's
    deliveries count for the reminder)."""
    features.set_enabled(company.id, "showcase", True)
    db.session.flush()
    from features.models import CompanyFeature
    row = CompanyFeature.query.filter_by(company_id=company.id).one()
    row.enabled_at = row.enabled_at - timedelta(days=1)
    db.session.commit()
    return company


@pytest.fixture
def delivered(order):
    order.status = "delivered"
    order.pickup_date = date.today()
    db.session.commit()
    return order


@pytest.fixture
def custom_type(company):
    row = OrderType(company_id=company.id, label="Bags", sort_order=0)
    db.session.add(row)
    db.session.commit()
    return row


def tab(order):
    return f"/orders/{order.id}/showcase"


def start(client, order):
    return client.post(f"/showcase/orders/{order.id}/start", data={"next": tab(order)})


def item_of(company, order):
    db.session.expire_all()
    return services.item_for_order(company.id, order.id)


def upload(client, item, *files):
    return client.post(
        f"/showcase/items/{item.id}/photos",
        data={"photos": [(BytesIO(data), name) for name, data in files],
              "notice_section": "photos"},
        content_type="multipart/form-data",
    )


# --- SC1: the feature gate ---------------------------------------------------

def test_without_the_feature_every_page_404s(logged_in, company, delivered):
    db.session.commit()
    for url in ("/showcase", "/settings/showcase", "/showcase/items/1", tab(delivered)):
        assert logged_in.get(url).status_code == 404, url
    assert logged_in.post("/showcase/items", data={}).status_code == 404
    assert logged_in.post(f"/showcase/orders/{delivered.id}/start").status_code == 404


def test_without_the_feature_nothing_shows(logged_in, company, delivered):
    db.session.commit()
    page = logged_in.get(f"/orders/{delivered.id}").get_data(as_text=True)
    assert "/showcase" not in page
    settings = logged_in.get("/settings/general").get_data(as_text=True)
    assert "/settings/showcase" not in settings


def test_a_signed_out_visitor_is_sent_to_login(app, on):
    response = app.test_client().get("/showcase")
    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_with_the_feature_the_pages_and_links_appear(logged_in, on):
    assert logged_in.get("/showcase").status_code == 200
    assert logged_in.get("/settings/showcase").status_code == 200
    page = logged_in.get("/settings/general").get_data(as_text=True)
    assert 'href="/showcase"' in page and 'href="/settings/showcase"' in page


def test_the_order_guide_mentions_showcase_only_with_the_feature(logged_in, company):
    db.session.commit()
    assert "Showcase</strong> tab" not in logged_in.get("/help/orders").get_data(as_text=True)
    features.set_enabled(company.id, "showcase", True)
    db.session.commit()
    assert "Showcase</strong> tab" in logged_in.get("/help/orders").get_data(as_text=True)


def test_switching_off_keeps_everything(logged_in, on, delivered):
    start(logged_in, delivered)
    features.set_enabled(on.id, "showcase", False)
    db.session.commit()
    assert logged_in.get("/showcase").status_code == 404
    assert ShowcaseItem.query.count() == 1


# --- SC2–SC5: settings ------------------------------------------------------

@pytest.mark.parametrize("kind, model", [
    ("categories", ShowcaseCategory), ("spec-fields", ShowcaseSpecField)])
def test_lists_add_hide_reorder_delete(logged_in, on, kind, model):
    logged_in.post(f"/settings/showcase/{kind}", data={"label": "Bags"})
    logged_in.post(f"/settings/showcase/{kind}", data={"label": "Wallets"})
    body = logged_in.post(f"/settings/showcase/{kind}", data={"label": " bags "},
                          follow_redirects=True).get_data(as_text=True)
    assert "already exists" in body
    rows = services.list_rows(model, on.id)
    assert [r.label for r in rows] == ["Bags", "Wallets"]

    logged_in.post(f"/settings/showcase/{kind}/reorder", json={"order": [rows[1].id, rows[0].id]})
    assert [r.label for r in services.list_rows(model, on.id)] == ["Wallets", "Bags"]

    logged_in.post(f"/settings/showcase/{kind}/{rows[0].id}/toggle")
    db.session.expire_all()
    assert db.session.get(model, rows[0].id).is_active is False

    logged_in.post(f"/settings/showcase/{kind}/{rows[1].id}/delete")
    assert [r.label for r in services.list_rows(model, on.id)] == ["Bags"]


def test_a_category_in_use_is_kept(logged_in, on):
    services.add_row(ShowcaseCategory, on.id, "Bags")
    category = services.list_rows(ShowcaseCategory, on.id)[0]
    services.create_item(on.id, "Tote", category_id=category.id)
    body = html.unescape(logged_in.post(f"/settings/showcase/categories/{category.id}/delete",
                                        follow_redirects=True).get_data(as_text=True))
    assert "can't be deleted" in body
    assert ShowcaseCategory.query.count() == 1


def test_a_spec_field_with_values_is_kept(logged_in, on):
    services.add_row(ShowcaseSpecField, on.id, "Leather")
    field = services.list_rows(ShowcaseSpecField, on.id)[0]
    item = services.create_item(on.id, "Tote")
    services.update_details(on.id, item, title="Tote", description="", category_id=None,
                            visibility="public", specs={field.id: "Veg-tan"})
    assert field.can_delete is False


def test_lists_are_per_company(logged_in, on, other_company):
    services.add_row(ShowcaseCategory, other_company.id, "Theirs")
    theirs = services.list_rows(ShowcaseCategory, other_company.id)[0]
    logged_in.post(f"/settings/showcase/categories/{theirs.id}/toggle")
    logged_in.post(f"/settings/showcase/categories/{theirs.id}/delete")
    db.session.expire_all()
    assert db.session.get(ShowcaseCategory, theirs.id).is_active is True
    assert services.list_rows(ShowcaseCategory, on.id) == []


def test_a_new_studio_starts_with_empty_lists_and_public(on):
    assert services.list_rows(ShowcaseCategory, on.id) == []
    assert services.list_rows(ShowcaseSpecField, on.id) == []
    assert services.get_settings(on.id).default_visibility == "public"


def test_default_visibility(logged_in, on):
    logged_in.post("/settings/showcase/visibility", data={"visibility": "in_person"})
    assert services.create_item(on.id, "Wallet").visibility == "in_person"
    body = logged_in.post("/settings/showcase/visibility", data={"visibility": "everywhere"},
                          follow_redirects=True).get_data(as_text=True)
    assert "Choose one" in body
    db.session.expire_all()
    assert services.get_settings(on.id).default_visibility == "in_person"


def test_excluded_order_types(logged_in, on, custom_type, other_company):
    foreign = OrderType(company_id=other_company.id, label="Theirs", sort_order=0)
    db.session.add(foreign)
    db.session.commit()
    logged_in.post("/settings/showcase/order-types",
                   data={"order_types_shown": "1", "excluded": [custom_type.id, foreign.id]})
    assert services.excluded_type_ids(on.id) == {custom_type.id}
    # No marker: nothing changes (hard rule 9).
    logged_in.post("/settings/showcase/order-types", data={})
    assert services.excluded_type_ids(on.id) == {custom_type.id}
    logged_in.post("/settings/showcase/order-types", data={"order_types_shown": "1"})
    assert services.excluded_type_ids(on.id) == set()


# --- SC6–SC8: the order tab -------------------------------------------------

def test_the_tab_needs_a_delivered_order(logged_in, on, order):
    db.session.commit()
    assert logged_in.get(tab(order)).status_code == 404
    assert "Showcase" not in logged_in.get(f"/orders/{order.id}").get_data(as_text=True).split("</nav>")[1].split("<form")[0]


def test_a_delivered_order_gets_the_tab(logged_in, on, delivered):
    page = logged_in.get(f"/orders/{delivered.id}").get_data(as_text=True)
    assert f'href="{tab(delivered)}' in page
    body = logged_in.get(tab(delivered)).get_data(as_text=True)
    assert "Showcase this piece" in body and "Not showcasing this one" in body


def test_an_excluded_type_gets_no_tab(logged_in, on, delivered, custom_type):
    delivered.order_type_id = custom_type.id
    db.session.commit()
    services.set_excluded_types(on.id, {custom_type.id})
    assert logged_in.get(tab(delivered)).status_code == 404


def test_an_existing_piece_keeps_its_tab(logged_in, on, delivered, custom_type):
    start(logged_in, delivered)
    delivered.order_type_id = custom_type.id
    delivered.status = "ready"
    db.session.commit()
    services.set_excluded_types(on.id, {custom_type.id})
    assert logged_in.get(tab(delivered)).status_code == 200


def test_another_companys_order_404s(logged_in, on, other_company):
    from models import Client
    client = Client(company_id=other_company.id, first_name="A", last_name="B")
    db.session.add(client)
    db.session.flush()
    theirs = Order(client_id=client.id, item="Theirs", start=date.today(),
                   due=date.today(), status="delivered")
    db.session.add(theirs)
    db.session.commit()
    assert logged_in.get(tab(theirs)).status_code == 404
    assert start(logged_in, theirs).status_code == 404
    assert logged_in.post(f"/showcase/orders/{theirs.id}/dismiss").status_code == 404


def test_starting_prefills_the_draft(logged_in, on, delivered, custom_type):
    services.add_row(ShowcaseCategory, on.id, "bags")
    delivered.order_type_id = custom_type.id
    db.session.commit()
    response = start(logged_in, delivered)
    assert response.status_code == 302 and response.headers["Location"].endswith(tab(delivered))
    item = item_of(on, delivered)
    assert item.title == "Full-grain briefcase"
    assert item.category.label == "bags"
    assert (item.status, item.visibility) == ("draft", "public")


def test_starting_twice_makes_one_piece(logged_in, on, delivered):
    start(logged_in, delivered)
    start(logged_in, delivered)
    assert ShowcaseItem.query.count() == 1


# --- SC20–SC23: the reminder -------------------------------------------------

def test_a_delivered_order_nags(logged_in, on, delivered):
    assert [o["id"] for o in services.awaiting_orders(on.id)] == [delivered.id]
    page = logged_in.get("/showcase").get_data(as_text=True)
    assert "Waiting for a decision" in page
    nav = logged_in.get("/").get_data(as_text=True)
    assert 'nav-badge nav-badge--new" title="1 delivered order waiting' in nav


def test_the_way_back_is_carried(logged_in, on, delivered):
    """Hard rule 14: from the Showcase page into an order's tab and back."""
    page = logged_in.get("/showcase").get_data(as_text=True)
    assert f'href="{tab(delivered)}?return_to=/showcase"' in page
    body = logged_in.get(f"{tab(delivered)}?return_to=/showcase").get_data(as_text=True)
    assert 'href="/showcase">&larr; Back to showcase' in body or "Back to showcase" in body
    assert f'name="next" value="{tab(delivered)}?return_to=/showcase"' in body


def test_orders_delivered_before_the_feature_dont_nag(on, delivered):
    delivered.pickup_date = date.today() - timedelta(days=30)
    db.session.commit()
    assert services.awaiting_orders(on.id) == []


def test_without_a_pickup_date_the_due_date_counts(on, delivered):
    delivered.pickup_date = None
    delivered.due = date.today()
    db.session.commit()
    assert len(services.awaiting_orders(on.id)) == 1


def test_dismissing_ends_the_nag_and_can_be_undone(logged_in, on, delivered):
    logged_in.post(f"/showcase/orders/{delivered.id}/dismiss", data={"next": tab(delivered)})
    assert services.awaiting_orders(on.id) == []
    assert ShowcaseDismissal.query.one().dismissed_by is not None
    body = logged_in.get(tab(delivered)).get_data(as_text=True)
    assert "Remind me again" in body
    logged_in.post(f"/showcase/orders/{delivered.id}/undismiss", data={"next": tab(delivered)})
    assert len(services.awaiting_orders(on.id)) == 1


def test_showcasing_ends_the_nag_and_deleting_brings_it_back(logged_in, on, delivered):
    start(logged_in, delivered)
    assert services.awaiting_orders(on.id) == []
    item = item_of(on, delivered)
    response = logged_in.post(f"/showcase/items/{item.id}/delete",
                              data={"next": f"/showcase/items/{item.id}"})
    assert response.headers["Location"].endswith(tab(delivered))
    assert len(services.awaiting_orders(on.id)) == 1


def test_an_excluded_type_doesnt_nag(on, delivered, custom_type):
    delivered.order_type_id = custom_type.id
    db.session.commit()
    services.set_excluded_types(on.id, {custom_type.id})
    assert services.awaiting_orders(on.id) == []


def test_the_reminder_is_per_company(on, delivered, other_company):
    features.set_enabled(other_company.id, "showcase", True)
    db.session.commit()
    assert services.awaiting_orders(other_company.id) == []


# --- SC9–SC10: details, publishing, withdrawing, deleting --------------------

@pytest.fixture
def piece(logged_in, on, delivered):
    start(logged_in, delivered)
    return item_of(on, delivered)


def save(client, item, **form):
    data = {"title": item.title, "visibility": item.visibility, "action": "save",
            "next": f"/showcase/items/{item.id}", "notice_section": "details"}
    data.update(form)
    return client.post(f"/showcase/items/{item.id}", data=data, follow_redirects=True)


def test_saving_details_and_specs(logged_in, on, piece):
    services.add_row(ShowcaseSpecField, on.id, "Leather")
    services.add_row(ShowcaseSpecField, on.id, "Hardware")
    leather, hardware = services.list_rows(ShowcaseSpecField, on.id)
    body = save(logged_in, piece, title="Weekender No. 24", description="Built to travel.",
                visibility="in_person", **{f"spec_{leather.id}": "Veg-tan",
                                           f"spec_{hardware.id}": "  "}).get_data(as_text=True)
    assert "Saved." in body
    db.session.expire_all()
    item = db.session.get(ShowcaseItem, piece.id)
    assert (item.title, item.description, item.visibility) == (
        "Weekender No. 24", "Built to travel.", "in_person")
    assert services.shown_specs(on.id, item) == [("Leather", "Veg-tan")]


def test_a_hidden_spec_field_is_not_shown_but_kept(on, piece):
    services.add_row(ShowcaseSpecField, on.id, "Lining")
    field = services.list_rows(ShowcaseSpecField, on.id)[0]
    services.update_details(on.id, piece, title="T", description="", category_id=None,
                            visibility="public", specs={field.id: "Suede"})
    services.toggle_row(ShowcaseSpecField, on.id, field.id)
    assert services.shown_specs(on.id, piece) == []
    services.toggle_row(ShowcaseSpecField, on.id, field.id)
    assert services.shown_specs(on.id, piece) == [("Lining", "Suede")]


def test_a_blank_title_or_foreign_category_is_refused(logged_in, on, piece, other_company):
    assert "A title is required." in save(logged_in, piece, title="  ").get_data(as_text=True)
    services.add_row(ShowcaseCategory, other_company.id, "Theirs")
    theirs = services.list_rows(ShowcaseCategory, other_company.id)[0]
    body = html.unescape(save(logged_in, piece, category_id=str(theirs.id)).get_data(as_text=True))
    assert "isn't available" in body
    db.session.expire_all()
    assert db.session.get(ShowcaseItem, piece.id).category_id is None


def test_a_hidden_category_stays_on_its_piece(on, piece):
    services.add_row(ShowcaseCategory, on.id, "Belts")
    category = services.list_rows(ShowcaseCategory, on.id)[0]
    services.update_details(on.id, piece, title="T", description="", category_id=category.id,
                            visibility="public", specs={})
    services.toggle_row(ShowcaseCategory, on.id, category.id)
    assert category in services.offered_categories(on.id, piece)
    assert services.update_details(on.id, piece, title="T2", description="",
                                   category_id=category.id, visibility="public", specs={}) is None


def test_publishing_needs_a_photo(logged_in, on, piece):
    body = save(logged_in, piece, action="publish").get_data(as_text=True)
    assert "Add at least one photo" in body
    upload(logged_in, piece, ("a.jpg", jpeg()))
    body = save(logged_in, piece, action="publish").get_data(as_text=True)
    assert "Published." in body
    db.session.expire_all()
    item = db.session.get(ShowcaseItem, piece.id)
    assert item.status == "published" and item.published_at is not None


def test_withdraw_then_delete(logged_in, on, piece):
    upload(logged_in, piece, ("a.jpg", jpeg()))
    services.publish(on.id, piece)
    body = logged_in.post(f"/showcase/items/{piece.id}/delete",
                          follow_redirects=True).get_data(as_text=True)
    assert "Withdraw this piece before deleting it." in body
    logged_in.post(f"/showcase/items/{piece.id}/withdraw")
    db.session.expire_all()
    item = db.session.get(ShowcaseItem, piece.id)
    assert item.status == "withdrawn"
    photo_path = os.path.join(config.SHOWCASE_DIR, str(on.id), item.photos[0].stored_filename)
    assert os.path.exists(photo_path)
    logged_in.post(f"/showcase/items/{piece.id}/delete")
    assert ShowcaseItem.query.count() == 0 and ShowcasePhoto.query.count() == 0
    assert not os.path.exists(photo_path)


def test_a_standalone_piece(logged_in, on):
    response = logged_in.post("/showcase/items", data={
        "title": "Card holder", "visibility": "private", "action": "save",
        "notice_section": "details"})
    item = ShowcaseItem.query.one()
    assert item.order_id is None and item.title == "Card holder"
    assert item.status == "draft" and item.visibility == "private"
    assert response.headers["Location"].endswith(f"/showcase/items/{item.id}")
    page = logged_in.get(f"/showcase/items/{item.id}").get_data(as_text=True)
    assert "A piece with no order" in page and "Saved as a draft." in page
    logged_in.post(f"/showcase/items/{item.id}/delete")
    assert ShowcaseItem.query.count() == 0


def test_opening_new_piece_saves_nothing(logged_in, on):
    """SC9a: + New piece opens a form; a mistaken click leaves nothing to delete."""
    services.add_row(ShowcaseSpecField, on.id, "Leather")
    db.session.commit()
    listing = logged_in.get("/showcase").get_data(as_text=True)
    assert 'href="/showcase/items/new"' in listing
    page = logged_in.get("/showcase/items/new")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "New piece" in body and 'name="spec_' in body and 'name="photos"' in body
    assert ShowcaseItem.query.count() == 0


def test_new_piece_saves_details_and_photos_together(logged_in, on):
    services.add_row(ShowcaseCategory, on.id, "Bags")
    services.add_row(ShowcaseSpecField, on.id, "Leather")
    db.session.commit()
    bags = services.list_rows(ShowcaseCategory, on.id)[0]
    leather = services.list_rows(ShowcaseSpecField, on.id)[0]
    logged_in.post("/showcase/items", data={
        "title": "Tote", "description": "Roomy.", "category_id": str(bags.id),
        f"spec_{leather.id}": "Veg tan", "visibility": "public", "action": "publish",
        "photos": [(BytesIO(jpeg(gps=True)), "a.jpg"), (BytesIO(jpeg()), "b.jpg")],
        "notice_section": "details",
    }, content_type="multipart/form-data")
    item = ShowcaseItem.query.one()
    assert item.status == "published" and item.published_at is not None
    assert item.category_id == bags.id and item.description == "Roomy."
    assert services.shown_specs(on.id, item) == [("Leather", "Veg tan")]
    assert len(item.photos) == 2
    assert 0x8825 not in stored_image(item.photos[0]).getexif()  # SC11 still holds


def test_new_piece_published_without_photos_stays_a_draft(logged_in, on):
    body = logged_in.post("/showcase/items", data={
        "title": "Belt", "visibility": "public", "action": "publish",
        "notice_section": "details"}, follow_redirects=True).get_data(as_text=True)
    item = ShowcaseItem.query.one()
    assert item.status == "draft"
    assert "Saved as a draft. Add at least one photo before publishing." in body


def test_a_refused_new_piece_saves_nothing(logged_in, on):
    response = logged_in.post("/showcase/items", data={
        "title": "  ", "description": "Kept", "visibility": "public", "action": "save",
        "notice_section": "details"})
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "A title is required." in body and "Kept" in body
    assert ShowcaseItem.query.count() == 0


def test_the_list_filters(logged_in, on):
    services.add_row(ShowcaseCategory, on.id, "Bags")
    bags = services.list_rows(ShowcaseCategory, on.id)[0]
    services.create_item(on.id, "Tote", category_id=bags.id)
    services.create_item(on.id, "Wallet")
    assert "Wallet" in logged_in.get("/showcase").get_data(as_text=True)
    page = logged_in.get(f"/showcase?category={bags.id}").get_data(as_text=True)
    assert "Tote" in page and "Wallet" not in page
    assert "No pieces match" in logged_in.get("/showcase?status=published").get_data(as_text=True)


def test_another_companys_piece_404s(logged_in, on, other_company):
    theirs = services.create_item(other_company.id, "Theirs")
    assert logged_in.get(f"/showcase/items/{theirs.id}").status_code == 404
    assert save(logged_in, theirs).status_code == 404


# --- SC11–SC13: photos ------------------------------------------------------

def test_a_photo_loses_its_location_and_keeps_its_orientation(logged_in, on, piece):
    upload(logged_in, piece, ("phone.jpg", jpeg(40, 20, gps=True, orientation=6)))
    db.session.expire_all()
    photo = db.session.get(ShowcaseItem, piece.id).photos[0]
    with stored_image(photo) as image:
        assert image.format == "JPEG"
        assert dict(image.getexif()) == {}
        # Orientation 6 means "rotate 90°": the 40×20 sensor image is 20×40.
        assert image.size == (20, 40)
    assert (photo.width, photo.height) == (20, 40)


def test_a_large_photo_is_scaled_down(logged_in, on, piece, monkeypatch):
    monkeypatch.setattr(config, "FULL_EDGE", 50)
    upload(logged_in, piece, ("big.jpg", jpeg(200, 100)))
    db.session.expire_all()
    photo = db.session.get(ShowcaseItem, piece.id).photos[0]
    assert (photo.width, photo.height) == (50, 25)


def test_a_transparent_png_goes_onto_white(logged_in, on, piece):
    upload(logged_in, piece, ("cut-out.png", png_with_alpha()))
    db.session.expire_all()
    with stored_image(db.session.get(ShowcaseItem, piece.id).photos[0]) as image:
        assert image.getpixel((5, 5))[0] > 240


def test_a_non_photo_is_refused(logged_in, on, piece):
    upload(logged_in, piece, ("notes.jpg", b"not an image"))
    db.session.expire_all()
    assert ShowcasePhoto.query.count() == 0
    page = html.unescape(logged_in.get(tab_of(piece)).get_data(as_text=True))
    assert "doesn't look like a photo" in page


def tab_of(item):
    return f"/orders/{item.order_id}/showcase"


def test_the_photo_cap(logged_in, on, piece, monkeypatch):
    monkeypatch.setattr(config, "MAX_PHOTOS_PER_ITEM", 2)
    upload(logged_in, piece, ("1.jpg", jpeg()), ("2.jpg", jpeg()), ("3.jpg", jpeg()))
    assert ShowcasePhoto.query.count() == 2


def test_the_storage_cap(on, piece, monkeypatch):
    monkeypatch.setattr(config, "MAX_TOTAL_BYTES", 10)
    error = services.add_photo(on.id, piece, jpeg(), "a.jpg")
    assert "storage" in error and ShowcasePhoto.query.count() == 0


def test_reorder_sets_the_cover(logged_in, on, piece):
    upload(logged_in, piece, ("1.jpg", jpeg(color=(1, 1, 1))), ("2.jpg", jpeg(color=(2, 2, 2))))
    db.session.expire_all()
    first, second = db.session.get(ShowcaseItem, piece.id).photos
    logged_in.post(f"/showcase/items/{piece.id}/photos/reorder",
                   json={"order": [second.id, first.id, 99999]})
    db.session.expire_all()
    assert db.session.get(ShowcaseItem, piece.id).cover.id == second.id


def test_make_cover_without_dragging(logged_in, on, piece):
    upload(logged_in, piece, ("1.jpg", jpeg()), ("2.jpg", jpeg()), ("3.jpg", jpeg()))
    db.session.expire_all()
    first, second, third = db.session.get(ShowcaseItem, piece.id).photos
    response = logged_in.post(f"/showcase/items/{piece.id}/photos/{third.id}/cover",
                              data={"next": tab_of(piece)})
    assert response.headers["Location"].endswith(tab_of(piece))
    db.session.expire_all()
    assert [p.id for p in db.session.get(ShowcaseItem, piece.id).photos] == [
        third.id, first.id, second.id]


def test_a_published_piece_keeps_its_last_photo(logged_in, on, piece):
    upload(logged_in, piece, ("1.jpg", jpeg()))
    services.publish(on.id, piece)
    db.session.expire_all()
    photo = db.session.get(ShowcaseItem, piece.id).photos[0]
    logged_in.post(f"/showcase/items/{piece.id}/photos/{photo.id}/delete")
    assert ShowcasePhoto.query.count() == 1


def test_photos_are_served_only_to_their_company(logged_in, on, other_company):
    theirs = services.create_item(other_company.id, "Theirs")
    services.add_photo(other_company.id, theirs, jpeg(), "a.jpg")
    photo = ShowcasePhoto.query.one()
    assert logged_in.get(f"/showcase/photos/{photo.id}.jpg").status_code == 404
    mine = services.create_item(on.id, "Mine")
    services.add_photo(on.id, mine, jpeg(), "b.jpg")
    own = ShowcasePhoto.query.filter_by(company_id=on.id).one()
    response = logged_in.get(f"/showcase/photos/{own.id}-thumb.jpg")
    assert response.status_code == 200 and response.mimetype == "image/jpeg"


# --- SC12: photos from the order's documents ---------------------------------

@pytest.fixture
def order_photo(on, delivered, monkeypatch, tmp_path):
    from documents import config as documents_config
    from documents import services as documents_services
    monkeypatch.setattr(documents_config, "DOCUMENT_DIR", str(tmp_path))
    result = documents_services.upload(on.id, delivered.id, [("finished.jpg", jpeg(gps=True))])
    return result.saved[0]


def test_order_images_can_be_copied_in(logged_in, on, piece, order_photo):
    page = logged_in.get(tab_of(piece)).get_data(as_text=True)
    assert "From this order's documents" in page and "finished.jpg" in page
    logged_in.post(f"/showcase/items/{piece.id}/photos/from-order",
                   data={"document_id": [order_photo.id], "next": tab_of(piece)})
    db.session.expire_all()
    photo = db.session.get(ShowcaseItem, piece.id).photos[0]
    assert photo.source_document_id == order_photo.id
    with stored_image(photo) as image:
        assert dict(image.getexif()) == {}
    # Added once: the picker stops offering it, and a second post adds nothing.
    assert "finished.jpg" not in logged_in.get(tab_of(piece)).get_data(as_text=True)
    logged_in.post(f"/showcase/items/{piece.id}/photos/from-order",
                   data={"document_id": [order_photo.id]})
    assert ShowcasePhoto.query.count() == 1


def test_deleting_the_document_keeps_the_photo(logged_in, on, piece, order_photo):
    from documents import services as documents_services
    logged_in.post(f"/showcase/items/{piece.id}/photos/from-order",
                   data={"document_id": [order_photo.id]})
    documents_services.delete(order_photo)
    db.session.expire_all()
    photo = db.session.get(ShowcaseItem, piece.id).photos[0]
    assert logged_in.get(f"/showcase/photos/{photo.id}.jpg").status_code == 200


def test_another_orders_document_is_refused(logged_in, on, piece, order_photo, client_record):
    other = Order(client_id=client_record.id, item="Other", start=date.today(),
                  due=date.today(), status="delivered")
    db.session.add(other)
    db.session.commit()
    standalone = services.create_item(on.id, "Loose")
    standalone.order_id = other.id
    db.session.commit()
    errors = services.add_order_photos(on.id, standalone, [order_photo.id])
    assert errors and ShowcasePhoto.query.count() == 0


# --- SC5: privacy of what can leave the app ----------------------------------

def test_the_preview_never_shows_price_or_client(logged_in, on, piece):
    body = logged_in.get(f"/showcase/items/{piece.id}").get_data(as_text=True)
    preview = body.split('aria-label="Preview"', 1)[1].split("</aside>", 1)[0]
    assert "Alarie" not in preview and "760" not in preview and "$" not in preview


# --- SC24: the module's boundary --------------------------------------------

HOST_MODELS = {"Client", "Company", "Document", "Order", "OrderLine", "OrderType",
               "Payment", "SourceOption", "User"}
SIBLINGS = {"admin", "ai", "billing", "communications", "documents", "inventory"}
SOURCES = sorted((ROOT / "showcase").rglob("*.py"))


def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                yield node.module or "", alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, ""


def test_there_are_sources_to_check():
    assert len(SOURCES) >= 8


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: p.name)
def test_showcase_never_imports_the_host(path):
    for module, name in _imports(path):
        root = module.split(".")[0]
        if module == "models":
            assert name == "db", f"{path.name} imports {name!r} from models"
        assert name not in HOST_MODELS, f"{path.name} imports {name!r}"
        assert root not in {"app", "showcase_adapter"}, f"{path.name} imports {root}"
        assert root not in SIBLINGS, f"{path.name} imports {root!r}"
        assert module != "usage.store", f"{path.name} imports usage.store"


# --- SC25–SC33: catalog mode and kiosk links ---------------------------------

def publish_piece(company_id, title, visibility="public", photo=True):
    item = services.create_item(company_id, title)
    item.visibility = visibility
    if photo:
        services.add_photo(company_id, item, jpeg(), f"{title}.jpg")
        services.publish(company_id, item)
    db.session.commit()
    return item


def payload_of(body: str) -> dict:
    raw = re.search(r'<script type="application/json" id="catalog-data">(.*?)</script>',
                    body, re.S).group(1)
    return json.loads(raw)


@pytest.fixture
def kiosk(logged_in, on):
    """A kiosk link's token, created through Settings like a studio would."""
    logged_in.post("/settings/showcase/kiosk-links", data={"name": "Booth tablet"})
    page = logged_in.get("/settings/showcase").get_data(as_text=True)
    return re.search(r"/k/([A-Za-z0-9_-]+)/", page).group(1)


def test_the_catalog_shows_published_public_and_in_person_pieces_only(on):
    publish_piece(on.id, "Public tote")
    publish_piece(on.id, "Booth wallet", visibility="in_person")
    publish_piece(on.id, "Secret bag", visibility="private")
    publish_piece(on.id, "Draft belt", photo=False)
    withdrawn = publish_piece(on.id, "Old card holder")
    services.withdraw(on.id, withdrawn)
    titles = {i.title for i in services.catalog_items(on.id)}
    assert titles == {"Public tote", "Booth wallet"}


def test_present_renders_the_catalog_without_the_app(logged_in, on):
    publish_piece(on.id, "Public tote")
    body = logged_in.get("/showcase/present").get_data(as_text=True)
    assert 'class="view-switch' not in body  # no app nav
    data = payload_of(body)
    assert [p["title"] for p in data["pieces"]] == ["Public tote"]
    assert data["serviceWorker"] is None
    assert 'href="/showcase">Exit' in body


def test_present_needs_the_feature(logged_in, company):
    db.session.commit()
    assert logged_in.get("/showcase/present").status_code == 404


def test_the_payload_carries_nothing_from_the_order(logged_in, on, delivered):
    start(logged_in, delivered)
    item = item_of(on, delivered)
    services.add_photo(on.id, item, jpeg(), "a.jpg")
    services.publish(on.id, item)
    data = payload_of(logged_in.get("/showcase/present").get_data(as_text=True))
    piece = data["pieces"][0]
    assert set(piece) == {"id", "title", "category_id", "category", "description",
                          "specs", "photos"}
    text = json.dumps(data)
    assert "Alarie" not in text and "760" not in text and "order" not in text


def test_a_new_link_is_shown_once_and_stored_hashed(logged_in, on):
    logged_in.post("/settings/showcase/kiosk-links", data={"name": "Market iPad"})
    page = logged_in.get("/settings/showcase").get_data(as_text=True)
    token = re.search(r"/k/([A-Za-z0-9_-]+)/", page).group(1)
    assert '<svg class="showcase-qr"' in page and "Market iPad" in page
    link = ShowcaseKioskLink.query.one()
    assert token not in link.token_hash and len(link.token_hash) == 64
    again = logged_in.get("/settings/showcase").get_data(as_text=True)
    assert token not in again and "Market iPad" in again


def test_a_kiosk_link_opens_the_catalog_signed_out(app, on, kiosk):
    publish_piece(on.id, "Public tote")
    client = app.test_client()
    response = client.get(f"/k/{kiosk}/")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert "noindex" in response.headers["X-Robots-Tag"]
    data = payload_of(body)
    assert [p["title"] for p in data["pieces"]] == ["Public tote"]
    assert data["serviceWorker"] == f"/k/{kiosk}/sw.js"
    assert ">Exit<" not in body
    assert client.get(f"/k/{kiosk}").status_code == 301
    db.session.expire_all()
    assert ShowcaseKioskLink.query.one().last_used_at is not None


def test_an_unknown_link_404s(app, on):
    assert app.test_client().get("/k/not-a-real-token/").status_code == 404


def test_a_deleted_link_stops_working(logged_in, app, on, kiosk):
    link = ShowcaseKioskLink.query.one()
    logged_in.post(f"/settings/showcase/kiosk-links/{link.id}/delete")
    assert app.test_client().get(f"/k/{kiosk}/").status_code == 404
    assert "deleted" in logged_in.get("/settings/showcase").get_data(as_text=True)
    assert "Booth tablet" not in logged_in.get("/settings/showcase").get_data(as_text=True)
    db.session.expire_all()
    assert ShowcaseKioskLink.query.one().revoked_at is not None


def test_links_stop_with_the_feature_or_the_company(app, on, kiosk):
    features.set_enabled(on.id, "showcase", False)
    db.session.commit()
    assert app.test_client().get(f"/k/{kiosk}/").status_code == 404
    features.set_enabled(on.id, "showcase", True)
    on.is_active = False
    db.session.commit()
    assert app.test_client().get(f"/k/{kiosk}/").status_code == 404


def test_a_kiosk_link_ignores_whoever_is_signed_in(app, on, kiosk, platform_admin, user):
    """Staff would be sent to /admin, and a user owing a password change to
    Settings, by app.py's guards — a kiosk link belongs to neither (SC30)."""
    db.session.commit()
    staff = app.test_client()
    staff.post("/login", data={"email": "platform@example.com", "password": "changeme"})
    assert staff.get(f"/k/{kiosk}/").status_code == 200
    user.must_change_password = True
    db.session.commit()
    owing = app.test_client()
    owing.post("/login", data={"email": "admin@example.com", "password": "changeme"})
    assert owing.get(f"/k/{kiosk}/").status_code == 200


def test_a_kiosk_serves_only_catalog_photos(app, on, kiosk, other_company):
    shown = publish_piece(on.id, "Public tote")
    hidden = publish_piece(on.id, "Secret bag", visibility="private")
    draft = services.create_item(on.id, "Draft")
    services.add_photo(on.id, draft, jpeg(), "d.jpg")
    theirs = publish_piece(other_company.id, "Theirs")
    client = app.test_client()
    ok = client.get(f"/k/{kiosk}/photos/{shown.photos[0].id}.jpg")
    assert ok.status_code == 200 and ok.mimetype == "image/jpeg"
    assert client.get(f"/k/{kiosk}/photos/{shown.photos[0].id}-thumb.jpg").status_code == 200
    for item in (hidden, draft, theirs):
        assert client.get(f"/k/{kiosk}/photos/{item.photos[0].id}.jpg").status_code == 404


def test_the_service_worker_and_manifest(app, on, kiosk):
    client = app.test_client()
    sw = client.get(f"/k/{kiosk}/sw.js")
    assert sw.status_code == 200 and sw.mimetype == "application/javascript"
    assert "caches.open" in sw.get_data(as_text=True)
    manifest = json.loads(client.get(f"/k/{kiosk}/manifest.webmanifest").get_data(as_text=True))
    assert manifest["start_url"] == manifest["scope"] == f"/k/{kiosk}/"
    assert manifest["display"] == "fullscreen" and manifest["name"] == "By Monsieur"


def logo_png(color, opaque=False):
    """A mark of `color` on transparency, or filling its box if `opaque`."""
    from PIL import ImageDraw

    image = Image.new("RGBA", (60, 20), color if opaque else (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((10, 4, 50, 16), outline=color, width=3)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_the_logo(app, logged_in, on, kiosk):
    import brand
    client = app.test_client()
    assert client.get(f"/k/{kiosk}/logo.png").status_code == 404
    brand.set_logo(on.id, logo_png((0, 0, 0, 255)))
    db.session.commit()
    assert client.get(f"/k/{kiosk}/logo.png").mimetype == "image/png"
    data = payload_of(client.get(f"/k/{kiosk}/").get_data(as_text=True))
    assert data["logo"] == f"/k/{kiosk}/logo.png"
    assert logged_in.get("/showcase/logo.png").status_code == 200


@pytest.mark.parametrize("color, opaque, plate", [
    ((255, 255, 255, 255), False, False),   # white on transparency: straight on
    ((20, 20, 20, 255), False, True),       # dark: needs the light plate
    ((255, 255, 255, 255), True, False),    # fills its own box: as it is
])
def test_only_a_dark_logo_gets_a_plate_in_catalog_mode(logged_in, on, color, opaque, plate):
    """SC26 / brand BL12: decided from the logo's own pixels."""
    import brand
    brand.set_logo(on.id, logo_png(color, opaque))
    db.session.commit()
    body = logged_in.get("/showcase/present").get_data(as_text=True)
    assert 'class="catalog__logo' in body
    assert ("catalog__logo--plate" in body) is plate


def test_the_qr_code_is_an_svg():
    from showcase.kiosk import qr_svg
    svg = str(qr_svg("https://example.test/k/abc/"))
    assert svg.startswith('<svg class="showcase-qr"') and "<path" in svg
