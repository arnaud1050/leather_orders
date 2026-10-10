# showcase/ — the studio's portfolio

> Part of the `leather_orders` app — see the root [CLAUDE.md](../CLAUDE.md)
> for stack, conventions and design language.

**This file is the why. [REQUIREMENTS.md](REQUIREMENTS.md) is the what**
(SC1–SC49). The whole feature, including the part not built yet (the
Share button), is planned in [docs/roadmap.md](../docs/roadmap.md),
"Planned: Showcase".

Finished pieces become a portfolio the studio controls from one place. Built
so far: the pieces themselves (photos, details, visibility, draft →
published → withdrawn), the order page's Showcase tab, the reminder on
delivered orders, Settings → Showcase, **catalog mode** — the
full-screen gallery and slideshow, opened signed in ("Present") or by a
kiosk link on a tablet — and **sending pieces to the studio's website**. **Generic on purpose**: nothing
here may assume leather, bags or the word "commission" — a ceramicist uses
it unchanged, which is why categories and spec fields are the studio's own
lists rather than columns.

## Sold per company

Everything sits behind the `showcase` feature ([features/](../features/CLAUDE.md)),
switched on per company from `/admin`. `routes._gate` 404s the whole
blueprint without it; `app.py`'s tab route asks `order_tab_context()`, which
checks again. Switching it off hides everything and deletes nothing.

## How self-contained it is

As strict as `ai/`: **no host model, ever** (SC24, enforced in
`tests/test_showcase.py`). An order is a plain `order_id` integer here, with
no foreign key. What the module needs to know about orders — the delivered
ones, one order's summary, its image documents, the company's order types —
comes from the five hooks in `hooks.py`, which `app.py` fills from
`showcase_adapter.py` at registration. That file is the only one that knows
a piece can come from an `Order`, the same seam `billing_adapter.py` is for
billing. Reaching into `documents/` directly would be a module importing a
sibling, so order images come through the adapter too.

Templates are the one soft spot: `_editor.html` uses the app-wide
`local_datetime` filter (registered by `communications/`) for the published
date, so the date reads in the company's time zone rather than UTC.

## Decisions worth knowing before changing something

- **Photos are copies, re-encoded** (SC11, SC12). A phone photo's EXIF
  carries GPS coordinates; a piece photographed at a client's home would
  otherwise publish their address. Re-encoding through Pillow is what
  strips it — after `ImageOps.exif_transpose`, or portrait photos end up on
  their side. Copies rather than references so deleting an order document
  can't break a published piece. Storage is `data/showcase/<company_id>/`,
  its own cap, because the disk is shared with every other site.
- **The reminder starts when the feature does** (SC20). Without that, the
  day Showcase is switched on every order ever delivered would nag at once.
  "Delivered on" is the pickup date, or the due date when none was
  recorded — there's no status history to read the real day from.
- **The order tab asks before it creates** (SC21). Until a piece exists the
  tab offers "Showcase this piece" or "Not showcasing this one". That's the
  decision the reminder is about, and it keeps every editor action tied to
  an existing piece instead of half the routes having a create-on-first-
  write variant. **+ New piece** (no order) is the one exception, on
  purpose (SC9a): it used to create a draft on the click, so a stray click
  left a piece to delete. Its page (`new.html`) posts details and photos in
  one form, and only that post creates; the editor takes over from there.
- **One piece per order** (SC7). A commission is one piece; batch work is
  what the excluded order types are for.
- **Delete is behind a dialog, and only once withdrawn** (SC10). It takes
  photos and writing with it, which nobody can re-add in a click — the same
  reason the order page's Delete has one. Requiring a withdraw first means
  deleting is never also an unannounced unpublish; and a piece whose card
  is on the website can't be deleted until it's taken off (SC46), or
  atelier would lose the only record that the card is there.
- **The star button exists because of tablets** (SC15). HTML drag and drop
  does nothing with touch, and the studio works from a tablet. The Settings
  lists share the app's existing drag-only reorder, a known gap everywhere.
- **No rename** for categories and spec fields, matching every other
  company list in the app: hide the old one, add the new one.

## Catalog mode and kiosk links

- **A kiosk link is a capability URL** (SC27–SC30). The token in
  `/k/<token>/` is the whole permission — no sign-in, so a tablet at a
  market never holds a session that reaches the invoices. That's why the
  kiosk routes are a **second blueprint** (`kiosk.py`, `showcase_kiosk`):
  the main one's gate reads `current_user`, which for a kiosk is nobody or,
  worse, somebody else. Every kiosk request resolves its token first, and
  app.py's two signed-in guards (staff → /admin, forced password change)
  skip `showcase_kiosk.*` endpoints. Only a SHA-256 of the token is stored,
  so the link is shown once; deleting keeps the row with `revoked_at`.
- **Everything under one path.** The page, photos, logo, service worker and
  manifest all live under `/k/<token>/`, so the worker's scope is exactly
  the link and the token never travels in a query string. `no-referrer`
  keeps it from reaching the font host.
- **Photos through a kiosk are checked against the catalog** (SC28), not
  just the company: photo ids are sequential, and a valid link mustn't
  open a draft's photos by counting.
- **Offline is the service worker's job** (SC31), rendered from
  `templates/showcase/catalog_sw.js`. Network first for the page, so a
  piece published at the bench appears on the next load; saved copy first
  for photos, whose URL never changes what it shows; scripts and styles
  refresh in the background. A 404 for the page deletes the saved copy,
  which is what makes deleting a lost tablet's link actually stop it.
- **The page is standalone** (`catalog.html`, `static/assets/css/showcase-catalog.css`,
  `static/assets/js/showcase-catalog.js`): no base.html, no nav, dark,
  Inter, none of the app's chrome. The logo is the studio's (brand/), and
  only a dark one gets a light plate: brand reads its tone from the pixels,
  so a white logo on transparency goes straight onto the dark page.
- **The QR code is server-side SVG** (`qrcode`, pure Python), so Settings
  needs no script library to show it.

## The website

Built 2026-10-07 (roadmap step 4, "Sending items to the website" and
"Every case, at a glance"). The decisions that shape it:

- **Nothing reaches the website without a button** (SC43, the studio's
  own decision: no surprises). Saving, publishing, withdrawing and
  connecting change atelier and catalog mode only. A piece's state on the
  website is *derived* (SC42) by comparing it with what was last sent
  (`ShowcasePublication.sent_hash`), never stored as a status, so it can't
  drift from reality; the review page lists what a press would do.
- **One direction, loose coupling.** Atelier calls the site; the site never
  calls back. All atelier knows is a URL and a shared secret. The photos
  travel inside the call (only the ones the site isn't known to hold), so
  the site never has to reach atelier, which matters because demo's atelier
  sits behind the demo slot's password.
- **The site touches only what atelier manages.** Every card atelier sends
  carries the piece's random key (SC40); the site changes no card without
  one. Its own cards, old collections included, stay its own, and are
  never pulled back into atelier.
- **The import is linked, not re-sent** (SC45). The one-off import's pieces
  are held back (*Not linked*) until **Link imported pieces** has the site
  stamp atelier's keys on the cards they came from, by photo path (SC39).
  After that they're managed cards exactly as if atelier had created them,
  with no duplicate and no photo uploaded twice. Sending an unlinked piece
  would double its card, which is why only an explicit **Send as a new
  card** does.
- **A card deleted on the site stays deleted** (SC48) until the studio
  presses **Send again**: the site keeps a tombstone and answers "gone".
- **The protocol is shared, not re-implemented.** `protocol.py` is a synced
  copy of website_modules' `showcase_protocol.py`, the same file each
  receiving site carries (SC49), so signing and the body format can't
  drift between the two ends. Edit the master there, never this copy.
- **The secret is encrypted at rest** (`crypto.py`, `SHOWCASE_ENCRYPTION_KEY`),
  through the root `crypto.py`, the one host helper a module may import
  (hard rule 4). It signs every call, so a stolen database alone mustn't
  be enough to post cards on the studio's site.
- **The website's limits are its own.** bymonsieur takes 7 photos a card,
  atelier allows 10. Send test records the site's limit, the editor warns
  before sending, and the site refuses with a reason if sent anyway.

## Layers

| File | What it's for |
|---|---|
| `config.py` | Storage directory, size limits, image edges, accepted formats. Env-overridable. |
| `models.py` | The tables, all with `company_id`; labels for statuses and visibilities. |
| `migrations.py` | This module's column migrations (hard rule 12), and giving rows that predate website keys one each (SC40). |
| `hooks.py` | The host's order hooks, and safe answers when they're unwired. |
| `images.py` | Decode, orient, strip, scale, re-encode. Raises `ImageError` with a message for the studio. |
| `storage.py` | Photo bytes on disk, by opaque generated name, containment re-checked. |
| `services.py` | The public API: settings, lists, pieces, photos, the reminder. `company_id` first. |
| `routes.py` | The blueprint, the context processor (nav count, tab state), `order_tab_context()` for app.py, `/showcase/present`, kiosk-link settings, the website's buttons, review page and Settings section. |
| `kiosk.py` | The kiosk links' blueprint (`/k/<token>/…`), `render_catalog()` shared with Present, the QR code. |
| `website.py` | The website connection, the payload, each piece's derived state, sending, linking the import. Nothing in it runs except from a route a button posts to. |
| `protocol.py` | **Synced copy** of website_modules' `showcase_protocol.py`: signing, the body, `send()`. Never edit it here. |
| `crypto.py` | The website secret's encryption box (`SHOWCASE_ENCRYPTION_KEY`), over the root `crypto.py`. |
| `templates/showcase/` | `list.html`, `item.html`, `new.html` (a piece with no order, before it exists), `settings.html`; `_editor.html` (shared by the item page and the order tab) and `_order_tab.html`; `_details_fields.html` (the details fields, shared by the editor and `new.html`); `catalog.html` and `catalog_sw.js`; `website.html` (the review page), `website_link.html` (linking the import) and `_website_results.html`. |

The catalog page's script and stylesheet are in the host's
`static/assets/` (`showcase-catalog.js`, `showcase-catalog.css`), per the
root CLAUDE.md's one-static-folder rule.

## Importing a studio's existing website

`scripts/import_showcase_from_site.py` (SC34–SC39) copies a site's Recent
Commissions into Showcase, once per deployment. It's a host script, not
module code: it knows our Flask sites' homepage markup, which nothing under
`showcase/` should. It reads the public page rather than the other app's
database, so demo and production need nothing shared between containers.
`ShowcaseItem.source_ref` is what makes it re-runnable, and with
`ShowcasePhoto.source_ref` what linking (SC45) uses to find the cards it
brought in. Once a website is connected it refuses to run: it's a one-off,
done before connecting.

## Not built yet

The Share button, step 5 of the roadmap's build order.
