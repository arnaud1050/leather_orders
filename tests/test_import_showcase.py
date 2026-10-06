"""
scripts/import_showcase_from_site.py — copying a website's Recent
Commissions into Showcase (showcase/REQUIREMENTS.md SC34–SC36).

Nothing here touches the network: `run()` takes the fetcher, and the tests
hand it a fake site whose markup mirrors bymonsieur.ca's homepage.
"""

import html
import importlib.util
import io
import json
import pathlib

import pytest
from PIL import Image

from models import db
from showcase import services, storage
from showcase.models import ShowcaseCategory, ShowcaseItem, ShowcasePhoto, ShowcaseSpecField

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "import_showcase_from_site.py"
spec = importlib.util.spec_from_file_location("import_showcase_from_site", SCRIPT)
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)

SITE = "https://studio.example"


def jpeg(color=(120, 60, 30)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (80, 60), color).save(out, format="JPEG")
    return out.getvalue()


def card(category, *photos):
    """One <li> as bymonsieur's index.html renders it."""
    images = [{"alt": cap, "caption": cap, "src": src, "thumb": src.replace(".", "-thumb.")}
              for src, cap in photos]
    data = html.escape(json.dumps(images), quote=False).replace("'", "&#39;")
    return (f'<li class="reveal-on-scroll" data-category="{category}" data-views="all" '
            f'style="transition-delay:0s">\n<button type="button" class="gallery-item" '
            f"data-images='{data}'>")


PAGE = f"""
<h2 id="gallery-heading">Recent Commissions</h2>
<button type="button" class="gallery-filter is-active" data-category="">All</button>
<button type="button" class="gallery-filter" data-category="1">Bags</button>
<button type="button" class="gallery-filter" data-category="9">Pouches &amp; Clutches</button>
<ul class="gallery-grid">
{card(9, ("/uploads/aaa.webp", "A custom pochette bag handcrafted in black premium cowhide leather."))}
{card(1, ("/uploads/bbb.webp", "A signature crossbody bag handcrafted in beige premium suede."),
         ("/uploads/ccc.webp", "A signature crossbody bag handcrafted in beige premium suede."))}
{card("", ("/uploads/ddd.webp", "Joe's market table, Saturday."))}
</ul>
"""


class FakeSite:
    def __init__(self, page=PAGE, broken=()):
        self.page, self.broken, self.calls = page, set(broken), []

    def __call__(self, url):
        self.calls.append(url)
        if url == SITE:
            return self.page.encode()
        if any(url.endswith(b) for b in self.broken):
            raise RuntimeError("404")
        return jpeg()


# --- SC35: reading the page ---------------------------------------------------

def test_cards_are_read_in_page_order_with_their_category_and_photos():
    cards = importer.parse_cards(PAGE, SITE)

    assert [c.title for c in cards] == ["Pochette bag", "Crossbody bag",
                                        "Joe's market table, Saturday"]
    assert [c.category for c in cards] == ["Pouches & Clutches", "Bags", None]
    assert cards[1].photo_urls == [f"{SITE}/uploads/bbb.webp", f"{SITE}/uploads/ccc.webp"]
    assert cards[0].material == "Black premium cowhide leather"
    assert cards[2].material == ""
    assert cards[0].source_ref == "studio.example/uploads/aaa.webp"


def test_a_page_with_no_cards_stops_the_run():
    with pytest.raises(importer.SiteReadError, match="No Recent Commissions cards"):
        importer.parse_cards("<html>redesigned</html>", SITE)


@pytest.mark.parametrize("caption, title, material", [
    ("A custom pochette bag handcrafted in black premium cowhide leather.",
     "Pochette bag", "Black premium cowhide leather"),
    ("A custom wallet in pink premium Epsom cowhide leather",
     "Wallet", "Pink premium Epsom cowhide leather"),
    ("A custom passport cover and its companion cardholder",
     "Passport cover and its companion cardholder", ""),
    ("Joe's market table, Saturday.", "Joe's market table, Saturday", ""),
])
def test_titles_and_materials_from_bymonsieurs_captions(caption, title, material):
    """Real caption shapes from bymonsieur.ca's Recent Commissions."""
    assert importer.title_and_material(caption) == (title, material)


def test_a_long_caption_makes_a_short_title():
    title, material = importer.title_and_material("word " * 40)
    assert len(title) <= importer.MAX_TITLE + 1 and title.endswith("…") and material == ""


# --- SC34, SC36: importing ------------------------------------------------------

def test_a_dry_run_writes_nothing(company):
    report = importer.run(company.id, SITE, apply=False, fetch=FakeSite(), log=lambda *_: None)

    assert len(report.created) == 3 and report.photos == 4
    assert report.new_categories == ["Pouches & Clutches", "Bags"]
    assert report.new_spec_fields == ["Leather"]
    assert ShowcaseItem.query.count() == 0
    assert ShowcaseCategory.query.count() == 0 and ShowcaseSpecField.query.count() == 0


def test_applying_creates_published_public_pieces(company):
    report = importer.run(company.id, SITE, apply=True, fetch=FakeSite(), log=lambda *_: None)

    assert len(report.created) == 3 and report.photos == 4 and not report.failed
    items = {i.title: i for i in ShowcaseItem.query.all()}
    bag = items["Crossbody bag"]
    assert (bag.status, bag.visibility) == ("published", "public")
    assert bag.category.label == "Bags" and len(bag.photos) == 2
    assert bag.description == "A signature crossbody bag handcrafted in beige premium suede."
    assert services.shown_specs(company.id, bag) == [("Leather", "Beige premium suede")]
    assert bag.source_ref == "studio.example/uploads/bbb.webp" and bag.order_id is None
    assert items["Joe's market table, Saturday"].category is None
    assert len(services.catalog_items(company.id)) == 3


def test_running_again_skips_what_was_imported(company):
    importer.run(company.id, SITE, apply=True, fetch=FakeSite(), log=lambda *_: None)
    site = FakeSite()

    report = importer.run(company.id, SITE, apply=True, fetch=site, log=lambda *_: None)

    assert report.created == [] and len(report.skipped) == 3
    assert ShowcaseItem.query.count() == 3
    assert site.calls == [SITE]  # no photo downloaded twice


def test_existing_categories_and_spec_fields_are_reused(company):
    services.add_row(ShowcaseCategory, company.id, "bags")
    services.add_row(ShowcaseSpecField, company.id, "Leather")

    importer.run(company.id, SITE, apply=True, fetch=FakeSite(), log=lambda *_: None)

    assert [c.label for c in services.list_rows(ShowcaseCategory, company.id)] == [
        "bags", "Pouches & Clutches"]
    assert ShowcaseSpecField.query.count() == 1


def test_a_card_whose_photo_fails_is_left_out_whole_and_retried(company):
    report = importer.run(company.id, SITE, apply=True,
                          fetch=FakeSite(broken=["ccc.webp"]), log=lambda *_: None)

    assert len(report.created) == 2 and "Crossbody bag" in report.failed[0]
    assert services.item_for_source(company.id, "studio.example/uploads/bbb.webp") is None
    again = importer.run(company.id, SITE, apply=True, fetch=FakeSite(), log=lambda *_: None)
    assert again.created == ["Crossbody bag"] and ShowcaseItem.query.count() == 3


def test_photos_are_re_encoded_like_any_upload(company):
    importer.run(company.id, SITE, apply=True, fetch=FakeSite(), log=lambda *_: None)

    photo = ShowcasePhoto.query.first()
    assert photo.stored_filename.endswith(".jpg") and photo.width == 80


def test_imports_are_per_company(company, other_company):
    importer.run(company.id, SITE, apply=True, fetch=FakeSite(), log=lambda *_: None)

    report = importer.run(other_company.id, SITE, apply=True, fetch=FakeSite(),
                          log=lambda *_: None)

    assert len(report.created) == 3
    assert ShowcaseItem.query.filter_by(company_id=other_company.id).count() == 3


# --- SC38: the site regrouped its cards -----------------------------------------

BAG = "A signature crossbody bag handcrafted in beige premium suede."
UNGROUPED = PAGE.replace(
    card(1, ("/uploads/bbb.webp", BAG), ("/uploads/ccc.webp", BAG)),
    card(1, ("/uploads/bbb.webp", BAG)) + "\n" + card(1, ("/uploads/ccc.webp", BAG)))


def test_cards_with_several_photos_make_one_piece_each(company):
    report = importer.run(company.id, SITE, apply=True, fetch=FakeSite(), log=lambda *_: None)

    assert report.created.count("Crossbody bag") == 1
    assert len(services.item_for_source(company.id, "studio.example/uploads/bbb.webp").photos) == 2


@pytest.mark.parametrize("apply", [False, True])
def test_importing_over_an_ungrouped_import_stops_and_says_remove(company, apply):
    importer.run(company.id, SITE, apply=True, fetch=FakeSite(UNGROUPED), log=lambda *_: None)
    assert ShowcaseItem.query.count() == 4

    with pytest.raises(importer.SiteReadError, match="--remove --apply"):
        importer.run(company.id, SITE, apply=apply, fetch=FakeSite(), log=lambda *_: None)
    assert ShowcaseItem.query.count() == 4


def test_after_remove_the_regrouped_site_imports_cleanly(company):
    importer.run(company.id, SITE, apply=True, fetch=FakeSite(UNGROUPED), log=lambda *_: None)
    importer.remove(company.id, SITE, apply=True, log=lambda *_: None)

    report = importer.run(company.id, SITE, apply=True, fetch=FakeSite(), log=lambda *_: None)

    assert len(report.created) == 3 and ShowcaseItem.query.count() == 3


def test_an_unchanged_site_is_not_mistaken_for_a_regroup(company):
    importer.run(company.id, SITE, apply=True, fetch=FakeSite(), log=lambda *_: None)

    assert importer.regrouped_imports(company.id, SITE, importer.parse_cards(PAGE, SITE)) == []


# --- SC37: removing an import ---------------------------------------------------

def test_remove_deletes_only_this_sites_imports_and_their_photos(company, other_company, tmp_path):
    importer.run(company.id, SITE, apply=True, fetch=FakeSite(), log=lambda *_: None)
    importer.run(other_company.id, SITE, apply=True, fetch=FakeSite(), log=lambda *_: None)
    importer.run(company.id, "https://studio.example.org", apply=True,
                 fetch=lambda url: FakeSite()(SITE if url == "https://studio.example.org" else url),
                 log=lambda *_: None)
    made_here = services.create_item(company.id, "Made in the app")
    files = [p.stored_filename for p in ShowcasePhoto.query.all()
             if p.item.company_id == company.id and p.item.source_ref.startswith("studio.example/")]
    assert len(files) == 4 and all(storage.path_for(company.id, f) for f in files)

    assert importer.remove(company.id, SITE, apply=False, log=lambda *_: None) == (3, 4)
    assert ShowcaseItem.query.filter_by(company_id=company.id).count() == 7  # dry run

    assert importer.remove(company.id, SITE, apply=True, log=lambda *_: None) == (3, 4)
    left = ShowcaseItem.query.filter_by(company_id=company.id).all()
    assert len(left) == 4 and made_here in left  # the .org imports and the app's piece
    assert ShowcaseItem.query.filter_by(company_id=other_company.id).count() == 3
    assert ShowcaseCategory.query.filter_by(company_id=company.id).count() == 2
    assert not any(storage.path_for(company.id, f) for f in files)


def test_after_remove_the_import_runs_again_from_scratch(company):
    importer.run(company.id, SITE, apply=True, fetch=FakeSite(), log=lambda *_: None)
    importer.remove(company.id, SITE, apply=True, log=lambda *_: None)

    report = importer.run(company.id, SITE, apply=True, fetch=FakeSite(), log=lambda *_: None)

    assert len(report.created) == 3 and ShowcaseItem.query.count() == 3


def test_the_command_line(company, capsys, monkeypatch):
    db.session.commit()
    monkeypatch.setattr(importer, "default_fetch", FakeSite())
    run = importer.run
    monkeypatch.setattr(importer, "run",
                        lambda *a, **k: run(*a, fetch=importer.default_fetch, **k))

    assert importer.main(["--company", "By Monsieur", "--site", SITE]) == 0
    out = capsys.readouterr().out
    assert "Dry run" in out and "would add 3 piece(s) with 4 photo(s)" in out
    assert "Nothing was written" in out and "doesn't have Showcase switched on" in out

    assert importer.main(["--company", "By Monsieur", "--site", SITE, "--apply"]) == 0
    assert "added 3 piece(s)" in capsys.readouterr().out

    assert importer.main(["--company", "By Monsieur", "--site", SITE, "--remove"]) == 0
    assert "would delete 3 piece(s) with 4 photo(s)" in capsys.readouterr().out
    assert ShowcaseItem.query.count() == 3
    assert importer.main(["--company", "By Monsieur", "--site", SITE, "--remove", "--apply"]) == 0
    assert "deleted 3 piece(s)" in capsys.readouterr().out
    assert ShowcaseItem.query.count() == 0
    assert importer.main(["--company", "Nobody", "--site", SITE]) == 2
