# Showcase — business requirements & rules

The living spec for `showcase/`. When behaviour changes on purpose, update
the rule here in the same commit — if a rule and the code disagree, one of
them is a bug. [CLAUDE.md](CLAUDE.md) explains the *why*; the full feature
plan (catalog mode, website sync, sharing) is in
[docs/roadmap.md](../docs/roadmap.md), "Planned: Showcase".

## 1. Sold per company

- **SC1 — Gated by the `showcase` feature.** Every route of the blueprint
  404s for a company without it (`features.require`, FE7), checked once in
  a `before_request` hook so a new route is covered by default; a signed-out
  visitor is sent to sign in instead. The order page's tab, the nav item, its
  badge and the Settings sub-nav link render only with the feature. Switching
  the feature off deletes nothing (FE5).

## 2. Settings → Showcase

- **SC2 — Per-company settings, created on first read.** One row per
  company; a new studio needs no seed row. The default visibility for a new
  piece is **public** until the studio changes it.
- **SC3 — Categories and spec fields are company lists**, ordered by drag,
  hidden rather than deleted once in use: a category is in use when a piece
  has it, a spec field when a piece has a non-blank value for it. Labels are
  unique per company, case-insensitively, hidden rows included. Both lists
  start **empty** for a new studio (hard rule 16). Ids from another company
  are ignored by every list action.
- **SC4 — Order types never showcased.** Ticking an order type keeps its
  orders away from the Showcase tab and the reminder. The form carries
  `order_types_shown`; without it a post changes nothing (hard rule 9). Ids
  that aren't one of the company's order types are ignored.

## 3. Pieces

- **SC5 — What can leave the app.** A piece shows its title, category,
  description and specs — only active spec fields with a non-blank value, in
  Settings order — and its photos. Never a price, a client, an order number,
  a date of delivery or a note. The editor's preview shows exactly that.
- **SC6 — The order page's Showcase tab** appears when the company has the
  feature and the order either already has a piece, or is `delivered` and
  of a type that isn't excluded. Anywhere else the tab's URL 404s.
- **SC7 — At most one piece per order.** Starting one for an order that
  already has one returns the existing piece.
- **SC8 — Starting from an order prefills the draft**: the title is the
  order's item, the category is the one whose name matches the order type's
  (case-insensitively), the visibility is the company default. Starting
  clears a dismissal, if there was one.
- **SC9 — Publishing needs a title and at least one photo.** A save that
  publishes writes the details first, then publishes; a refusal says what's
  missing. A piece stores when it was first published.
- **SC10 — Withdraw, then delete.** A published piece can be withdrawn
  (nothing is deleted, it can be published again) but not deleted. A draft
  or withdrawn piece can be deleted, behind a confirm dialog; its photos go
  with it, the order and the order's documents don't, and the order's tab
  asks the question again.

## 4. Photos

- **SC11 — Every photo is re-encoded, its metadata stripped.** Uploads are
  decoded and saved again as JPEG: the EXIF block (GPS position, camera,
  time) does not survive, the orientation tag is applied first so nothing
  ends up on its side, transparency lands on white, and the long edge is at
  most `FULL_EDGE` (2400px) with a `THUMB_EDGE` (640px) thumbnail. Only JPEG,
  PNG and WebP are accepted; anything else, or anything Pillow can't open,
  is refused with a reason.
- **SC12 — Photos from the order are copies.** The order's JPEG/PNG
  documents are offered for picking, through the host's hooks; the chosen
  ones are copied in (and re-encoded, SC11) and no longer offered. Deleting
  the document afterwards leaves the piece's photo intact. A document that
  isn't on the piece's own order is refused.
- **SC13 — Limits.** At most `MAX_PHOTOS_PER_ITEM` (10) photos per piece,
  `MAX_UPLOAD_BYTES` (25MB) per upload, and `MAX_TOTAL_BYTES` (500MB) of
  stored photos per company. Each file of a batch is judged alone, so some
  can land while others are refused, and the message says both.
- **SC14 — A published piece keeps a photo.** Its last photo can't be
  deleted; add another first, or withdraw it.
- **SC15 — The first photo is the cover.** Order is set by dragging, or by
  the star on a photo, which makes it the cover without dragging (touch
  screens can't drag). Ids not on the piece are ignored.
- **SC16 — Visibility.** `public`: catalog mode and, once connected, the
  website. `in_person`: catalog mode only, never online. `private`: kept
  here, shown nowhere.
- **SC17 — Photos are served only to their company.** Another company's
  photo id 404s, like another company's piece or order.

## 5. Pages

- **SC18 — Saves report in their section** (MOD8): Photos, Details and This
  piece on the editor; the decision on the order tab; Waiting for a decision
  and Pieces on the Showcase page; one slot per list on Settings.
- **SC19 — Saving keeps your place** (hard rules 14 and 17). Every form
  posts `next`, the page it's on, including its `return_to`; only a local
  path is honoured. Links from the Showcase page and an item page into an
  order's tab carry `return_to` back.

## 6. The reminder

- **SC20 — Which orders nag.** Delivered on or after the day the company's
  current spell of Showcase began (FE10) — "delivered" meaning the pickup
  date, or the due date when none was recorded — of a type that isn't
  excluded, with neither a piece nor a dismissal. Derived on every read,
  never stored (hard rule 10). Orders delivered before the feature arrived
  never nag, though they can still be showcased from their tab.
- **SC21 — How it nags.** A purple count on the nav's Showcase item ("worth
  knowing", hard rule 7), a purple dot on the order's Showcase tab, and a
  "Waiting for a decision" list on the Showcase page. It stays until the
  order is showcased or dismissed.
- **SC22 — "Not showcasing this one" is reversible.** It records who and
  when; the order's tab then offers "Remind me again".
- **SC23 — Deleting an order's piece brings its reminder back**, since the
  order has neither a piece nor a dismissal again.

## 7. Catalog mode and kiosk links

- **SC25 — What the catalog shows.** Published pieces whose visibility is
  public or in person only, newest first, each with at least one photo.
  Never a draft, a withdrawn piece or a private one. The same set whether
  it's opened by a signed-in user (`/showcase/present`) or a kiosk link.
- **SC26 — What the page carries.** The catalog arrives as one JSON
  payload: per piece its title, category, description, shown specs (SC5)
  and photo links — nothing from the order, no price, no client — plus the
  studio's name and logo. No app navigation: the page is standalone. The
  page is near-black, so only a **dark** logo sits on a light plate; a
  light or opaque one is shown as it is (the tone read from its pixels,
  brand BL12).
- **SC27 — A kiosk link is shown once and stored hashed.** Creating one
  (Settings → Showcase → Catalog mode) shows its URL and a QR code on that
  one page load and never again; the database keeps only a SHA-256 of the
  token. A lost link is replaced by deleting it and creating another.
- **SC28 — A kiosk link serves only the catalog.** Its photo URLs answer
  only for photos of pieces in the catalog (SC25): a draft's, a private
  piece's or another company's photo 404s even with a valid token.
- **SC29 — When a kiosk link stops.** An unknown or deleted token, a company
  without the feature, or a deactivated company all answer 404 on every
  kiosk URL. Deleting keeps the row (`revoked_at`) and takes it off the list.
  Each link shows when it was created and last opened (noted at most every
  five minutes).
- **SC30 — The token is the permission, not the session.** Kiosk URLs never
  consult `current_user`: they work signed out, and app.py's staff and
  password-change redirects don't apply to them. Responses carry
  `Referrer-Policy: no-referrer` and `X-Robots-Tag: noindex`.
- **SC31 — Offline.** A kiosk link registers a service worker scoped to the
  link. It saves the page, its script and styles, the logo and every
  catalog photo, drops photos no longer listed, serves the page network
  first (so new pieces appear) and everything else from the saved copy. A
  404 for the page deletes the saved copy, so a deleted link stops working
  on the device the next time it's online. `/showcase/present` has no
  service worker.
- **SC32 — Stays on, full screen.** Screen Wake Lock where the browser has
  it, a Full screen button only where the Fullscreen API exists, and a web
  manifest (`display: fullscreen`, scoped to the link) for Add to Home
  Screen. Each is skipped silently where unsupported.
- **SC33 — Gallery, viewer, slideshow.** Category chips (only categories in
  use) filter the grid; a tile opens the viewer (photos by swipe, buttons or
  arrow keys, pieces by buttons or up/down, Esc closes). The slideshow
  starts from its button or after `IDLE_MINUTES` (3) without a touch:
  pieces in random order, slow pan-and-zoom with crossfades (crossfades only
  under `prefers-reduced-motion`), the title, category and studio. Space
  pauses; any other key, click or touch opens the piece on screen, and that
  same touch can't also press a button in the viewer.

## 8. Importing a studio's existing website

`scripts/import_showcase_from_site.py` — run by hand, once per deployment
(docs/deployment.md has the commands).

- **SC34 — Imports are traceable and never repeated.** Each imported piece
  records the card it came from in `source_ref` ("<host><first photo's
  path>"; null for pieces made in the app). A card whose `source_ref`
  already exists in the company is skipped, so running the import again
  adds only what's new on the site. The website sync (roadmap step 4) is
  meant to use the same column to link a piece to its existing card
  instead of posting a duplicate.
- **SC35 — What it reads.** The site's public homepage: every Recent
  Commissions card, its photos in order, its category (by the filter
  button's name) and its caption. A page with no readable card stops the
  run with a message, never a partial import.
- **SC36 — What it makes.** A **dry run unless `--apply`**, listing each
  piece and the categories and spec field it would create. With `--apply`,
  each card becomes a published, **public** piece with no order: its
  caption as the description, a title and a "Leather" spec drawn from the
  caption ("A custom pochette bag handcrafted in black … leather" →
  "Pochette bag", "Black … leather"; a caption that doesn't read that way
  becomes the title, shortened), its category (matched by name,
  case-insensitively, or created), and its photos through the usual
  re-encoding (SC11). A card is imported whole or not at all: if a photo
  can't be downloaded, nothing of that card is written and the run reports
  it; the next run tries again.

## 9. Boundary

- **SC24 — The module never imports the host.** Only `db` from `models`,
  plus `features` and `usage` (never `usage.store`); no host model, no
  `app.py`, no `showcase_adapter.py`, no sibling module. Orders and their
  images arrive through the hooks in `hooks.py`, filled by the host's
  `showcase_adapter.py`.

## Test coverage map

| Rules | Where |
| --- | --- |
| SC1–SC17, SC19–SC31 | `tests/test_showcase.py` |
| SC34–SC36 | `tests/test_import_showcase.py` (a fake site with bymonsieur's markup; checked once against the live bymonsieur.ca on 2026-10-06 — a full dry run, and a limited `--apply` on a throwaway database, twice) |
| SC18 | `tests/test_save_notices.py` — `test_every_showcase_page_is_wired`, `test_a_showcase_save_reports_in_its_section` |

**Not covered by tests:** the drag-to-reorder scripts (the JSON endpoints
behind them are), the editor's layout, and the catalog page's own script
(SC32, SC33, and the service worker's behaviour in SC31; its routes and
payload are tested). Those were checked by hand in a browser on 2026-10-05:
gallery, chips, viewer keys, slideshow handover, offline reload with the
server stopped, and a dead link wiping the saved copy.
