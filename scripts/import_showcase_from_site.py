#!/usr/bin/env python
"""
Copy a website's Recent Commissions into a company's Showcase.

For a studio whose existing site (one of our Flask sites, bymonsieur.ca
first) already shows its finished pieces: every card on the homepage's
Recent Commissions becomes a published, public Showcase piece, with its
photos, its category, its caption as the description, and a title and
"Leather" spec drawn from the caption. Showcase rules SC34–SC36.

**A dry run unless you pass --apply.** Run it in the app's own container,
so it writes to the database and photo folder the app uses:

    docker compose exec atelier-orders \
        python scripts/import_showcase_from_site.py --company "By Monsieur" --site https://bymonsieur.ca
    docker compose exec atelier-orders \
        python scripts/import_showcase_from_site.py --company "By Monsieur" --site https://bymonsieur.ca --apply

(demo: `docker compose -f docker-compose-demo.yml exec demo …`). Locally,
`DATABASE_URL` must be set in the environment before this runs (hard rule
13) unless you mean the real `data/atelier.db`.

**Safe to run again.** Each piece records the card it came from
(`ShowcaseItem.source_ref`, "<host><first photo's path>"); a card already
imported is skipped, so a second run only brings in what's new on the site.
A card whose download fails is left out whole and reported, and the next
run picks it up.

**--remove undoes it**, to start again: it deletes every piece imported
from that site, photos included (again a dry run unless --apply). Pieces
made in the app, and the categories and spec field, are kept.

**It reads the public homepage.** Nothing on the server has to be shared
between the two apps. If the page's markup changes so that no card can be
read, it stops and says so rather than importing half a gallery.
"""

import argparse
import html
import json
import os
import re
import sys
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# A card: <li … data-category="3" …><button class="gallery-item" data-images='[…]'>
CARD = re.compile(r'<li class="reveal-on-scroll" data-category="(\d*)".*?data-images=\'(.*?)\'>', re.S)
# A category filter button: data-category="3">Accessories</button>
CATEGORY = re.compile(
    r'<button type="button" class="gallery-filter[^"]*" data-category="(\d+)">([^<]+)</button>')
# "A custom pochette bag handcrafted in black premium cowhide leather." —
# also without "handcrafted" ("A custom wallet in pink … leather").
CAPTION = re.compile(r"^(?:An? )?(?:custom |signature |bespoke )?(.+?)(?: handcrafted)? in (.+?)\.?$", re.I)
# The opening words to drop when a caption has no material in it.
ARTICLE = re.compile(r"^(?:An? )?(?:custom |signature |bespoke )?", re.I)

MATERIAL_FIELD = "Leather"
MAX_TITLE = 80


class SiteReadError(Exception):
    """Something that stops the whole run, with a message for whoever ran it."""


@dataclass
class Card:
    """One Recent Commissions card, as the homepage shows it."""

    source_ref: str
    category: str | None
    caption: str
    photo_urls: list[str]
    title: str = ""
    material: str = ""


@dataclass
class Report:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    new_categories: list[str] = field(default_factory=list)
    new_spec_fields: list[str] = field(default_factory=list)
    photos: int = 0


def title_and_material(caption: str) -> tuple[str, str]:
    """A short title and the material, from a caption like "A custom
    pochette bag handcrafted in black premium cowhide leather." Falls back
    to the caption itself, shortened, when it doesn't read that way."""
    caption = " ".join(caption.split())
    match = CAPTION.match(caption)
    if match:
        title, material = match.group(1).strip(), match.group(2).strip()
    else:
        title, material = ARTICLE.sub("", caption).rstrip("."), ""
        if len(title) > MAX_TITLE:
            title = title[:MAX_TITLE].rsplit(" ", 1)[0] + "…"
    cap = lambda s: s[:1].upper() + s[1:]  # noqa: E731
    return cap(title) or "Untitled piece", cap(material)


def parse_cards(page: str, site: str) -> list[Card]:
    """Every card on the page, in page order (SC35). Raises when there are
    none, since that means the markup changed, not that the gallery is empty."""
    host = urlparse(site).netloc
    categories = {key: html.unescape(label).strip() for key, label in CATEGORY.findall(page)}
    cards = []
    for category_key, raw in CARD.findall(page):
        try:
            images = json.loads(html.unescape(raw))
        except ValueError as exc:
            raise SiteReadError("A card's photo list couldn't be read; the site's markup "
                               "has probably changed.") from exc
        images = [i for i in images if isinstance(i, dict) and i.get("src")]
        if not images:
            continue
        caption = (images[0].get("caption") or images[0].get("alt") or "").strip()
        title, material = title_and_material(caption)
        cards.append(Card(
            source_ref=f"{host}{images[0]['src']}",
            category=categories.get(category_key),
            caption=caption,
            photo_urls=[urljoin(site, i["src"]) for i in images],
            title=title, material=material,
        ))
    if not cards:
        raise SiteReadError(f"No Recent Commissions cards found on {site}. Either the "
                           "gallery is switched off, or its markup changed and this "
                           "script needs updating.")
    return cards


def default_fetch(url: str) -> bytes:
    """GET a URL's bytes. Uses the system trust store where `truststore` is
    installed (a Windows dev machine whose Python can't verify the chain);
    the server's Linux image needs nothing extra."""
    import requests

    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass
    response = requests.get(url, timeout=30, headers={"User-Agent": "atelier-showcase-import/1"})
    response.raise_for_status()
    return response.content


def regrouped_imports(company_id: int, site: str, cards: list[Card]) -> list[str]:
    """Titles of pieces imported earlier that no longer match a card one to
    one (SC38): their photo is now a later photo of some card, or their
    card now has more photos than they do. Happens when the site starts
    grouping photos it used to show as separate cards."""
    from showcase import services

    host = urlparse(site).netloc
    by_ref = {card.source_ref: card for card in cards}
    later_photos = {f"{host}{urlparse(url).path}" for card in cards for url in card.photo_urls[1:]}
    return [item.title for item in services.items_imported_from(company_id, host)
            if item.source_ref in later_photos
            or (item.source_ref in by_ref
                and len(item.photos) < len(by_ref[item.source_ref].photo_urls))]


def run(company_id: int, site: str, *, apply: bool, fetch=default_fetch,
        limit: int | None = None, log=print) -> Report:
    """Import the site's cards into the company's Showcase. Dry run unless
    `apply`. Must run inside an app context."""
    from models import db
    from showcase import services
    from showcase.models import ShowcaseCategory, ShowcaseSpecField

    page = fetch(site).decode("utf-8", errors="replace")
    cards = parse_cards(page, site)
    regrouped = regrouped_imports(company_id, site, cards)
    if regrouped:
        raise SiteReadError(
            f"The site's cards have changed shape since the last import: "
            f"{len(regrouped)} piece(s) imported earlier now belong to a bigger card "
            f"(e.g. {regrouped[0]!r}). Importing on top would leave duplicates and "
            f"pieces missing photos. Delete the earlier import first with --remove "
            f"--apply, then import again.")
    if limit:
        cards = cards[:limit]
    report = Report()

    def find_or_plan(model, label, planned):
        if not label:
            return None
        for row in services.list_rows(model, company_id):
            if row.label.strip().lower() == label.lower():
                return row
        if label not in planned:
            planned.append(label)
        if apply:
            services.add_row(model, company_id, label)
            return next(r for r in services.list_rows(model, company_id)
                        if r.label.strip().lower() == label.lower())
        return None

    for card in cards:
        if services.item_for_source(company_id, card.source_ref) is not None:
            report.skipped.append(card.title)
            continue
        category = find_or_plan(ShowcaseCategory, card.category, report.new_categories)
        material_field = (find_or_plan(ShowcaseSpecField, MATERIAL_FIELD, report.new_spec_fields)
                          if card.material else None)
        if not apply:
            report.created.append(card.title)
            report.photos += len(card.photo_urls)
            log(f"  would add  {card.title}  [{card.category or 'no category'}, "
                f"{len(card.photo_urls)} photo(s)]")
            continue

        # Every photo first: a card is imported whole or not at all.
        try:
            photos = [(url, fetch(url)) for url in card.photo_urls]
        except Exception as exc:  # noqa: BLE001 — reported, retried next run
            report.failed.append(f"{card.title}: couldn't download a photo ({exc})")
            continue
        item = services.create_item(company_id, card.title,
                                    category_id=category.id if category else None,
                                    source_ref=card.source_ref)
        error = services.update_details(
            company_id, item, title=card.title, description=card.caption,
            category_id=category.id if category else None, visibility="public",
            specs={material_field.id: card.material} if material_field else {},
        )
        errors = [error] if error else []
        for url, data in photos:
            photo_error = services.add_photo(company_id, item, data, url.rsplit("/", 1)[-1])
            if photo_error:
                errors.append(photo_error)
            else:
                report.photos += 1
        if not item.photos:
            # Not one photo could be used: nothing worth keeping, and the
            # next run will try the card again.
            services.delete_item(company_id, item)
            report.failed.append(f"{card.title}: {'; '.join(errors) or 'no usable photo'}")
            continue
        services.publish(company_id, item)
        db.session.commit()
        report.created.append(card.title)
        log(f"  added      {card.title}")
        if errors:
            report.failed.append(f"{card.title}: imported, but {'; '.join(errors)}")
    return report


def remove(company_id: int, site: str, *, apply: bool, log=print) -> tuple[int, int]:
    """Delete every piece this script imported from `site`, photos included
    (SC37), so an import can be run again from scratch. Pieces made in the
    app, categories and spec fields stay. Dry run unless `apply`. Returns
    (pieces, photos)."""
    from showcase import services

    items = services.items_imported_from(company_id, urlparse(site).netloc)
    photos = 0
    for item in items:
        photos += len(item.photos)
        if not apply:
            log(f"  would delete  {item.title}")
            continue
        services.withdraw(company_id, item)  # SC10: never deleted while published
        services.delete_item(company_id, item)
    return len(items), photos


def _company(identifier: str):
    from models import Company, db

    if identifier.isdigit():
        return db.session.get(Company, int(identifier))
    return Company.query.filter(Company.name == identifier).first()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--company", required=True, help="the company's name, or its id")
    parser.add_argument("--site", required=True, help="the website, e.g. https://bymonsieur.ca")
    parser.add_argument("--apply", action="store_true",
                        help="actually import (without it, only list what would happen)")
    parser.add_argument("--limit", type=int,
                        help="only the first N cards, to try it out. A site that shuffles "
                             "its gallery shows a different first N each time")
    parser.add_argument("--remove", action="store_true",
                        help="instead of importing, delete every piece imported from this "
                             "site (with --apply), to start again")
    args = parser.parse_args(argv)
    # A Windows console can't print every character in a caption; replace
    # what it can't show rather than crash halfway through a run.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    from app import app
    import features

    with app.app_context():
        print(f"database: {app.config['SQLALCHEMY_DATABASE_URI']}")
        company = _company(args.company)
        if company is None:
            print(f"No company called {args.company!r}.", file=sys.stderr)
            return 2
        if args.remove:
            print(f"{'Deleting' if args.apply else 'Dry run'}: pieces imported from "
                  f"{args.site} in {company.name}")
            pieces, photos = remove(company.id, args.site, apply=args.apply)
            verb = "deleted" if args.apply else "would delete"
            print(f"\n{verb} {pieces} piece(s) with {photos} photo(s). Pieces made in the "
                  "app, categories and spec fields are kept.")
            if not args.apply:
                print("\nNothing was deleted. Run again with --apply to delete.")
            return 0
        if not features.is_enabled(company.id, "showcase"):
            print(f"Note: {company.name} doesn't have Showcase switched on (Admin -> "
                  "Features); the pieces are imported but won't show until it is.")
        print(f"{'Importing' if args.apply else 'Dry run'}: {args.site} -> {company.name}")
        try:
            report = run(company.id, args.site, apply=args.apply, limit=args.limit)
        except SiteReadError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    verb = "added" if args.apply else "would add"
    print(f"\n{verb} {len(report.created)} piece(s) with {report.photos} photo(s); "
          f"{len(report.skipped)} already imported, skipped.")
    if report.new_categories:
        print(f"new categories: {', '.join(report.new_categories)}")
    if report.new_spec_fields:
        print(f"new spec field: {', '.join(report.new_spec_fields)}")
    for line in report.failed:
        print(f"problem: {line}", file=sys.stderr)
    if not args.apply:
        print("\nNothing was written. Run again with --apply to import.")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
