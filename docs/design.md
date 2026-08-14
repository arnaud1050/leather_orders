# Design language

> Part of the `leather_orders` app — see the root [CLAUDE.md](../CLAUDE.md) for orientation. The non-negotiable bans are restated as hard rules 5–7 there; this file is the reasoning behind them, plus everything else about how the UI is styled.

Evolved from an initial "leathersmith's ledger" concept (stitched dividers, brass/tan/
oxblood/sage palette, Fraunces serif) toward something cleaner and more neutral —
that theme was tried and explicitly moved away from. Current direction, in order of
changes made:

1. Recolored toward a "quiet luxury" bone/charcoal palette (inspired by bymonsieur.ca)
2. Display font tried as Cormorant Garamond (serif), then Space Grotesk (sans) —
   both rejected. **Current: no separate display font.** `--font-display` is just
   `sans-serif` / removed from most rules, so headings inherit `--font-body` (Inter).
   Don't reintroduce a display font without checking first — this has been tried
   twice and reverted both times.
3. Status/accent colors moved away from brown/earthy tones (they read as
   indistinguishable from each other) to four deliberately distinct, higher-contrast
   hues — see below.
4. Hairlines/borders moved from a light tan (`#d9d3c6`) to a dark charcoal-grey
   (`#83898d` currently) — user explicitly dislikes brownish lines.
5. Decorative elements reduced over time: the stitched dashed top/bottom rules and
   the "brass rivet" concept are gone from the markup. `.day__rivet` still exists in
   CSS/template (marks "today") but now uses `--day-today` (red), not brass.
6. Nav arrows are inline SVG chevrons (stroke, `currentColor`), not text glyphs —
   swapped in `calendar.html` inside `.stamp` links.
7. Footer is pinned to the bottom of the viewport via a flex sticky-footer pattern
   (`body` → flex column, `.ledger` → `flex: 1 0 auto` + flex column,
   `.ledger__footer` → `margin-top: auto`), so it no longer jumps up/down between
   4-row and 6-row months.
8. Gap between page sections (order/client/settings pages: Line items, Invoice,
   Payments, Documents, Company details, etc.) is a single `margin-bottom: 40px`
   declared once on the section-wrapper classes themselves (`.detail-orders`,
   `.detail-documents`, `.detail-payments`, `.detail-lines`, `.detail-invoice`,
   `.invoice-admin`), not on whatever form/list/note happens to end each section.
   It used to be the latter, and different endings (a `.detail-form` at 40px, a
   `.detail-note` at ~12px, `.settings-form` at 8px) produced visibly inconsistent
   gaps. **If you add a new section to one of these pages, put it in one of these
   wrapper classes (or add the class to this rule) rather than tuning the margin
   on whatever's inside it** — margin collapsing between the border/padding-less
   `<section>` and its last child means this 40px wins over a smaller child margin
   automatically, so you don't need to zero anything out.
9. Page width bumped from 980px to **1200px** for more breathing room, especially on
   the timeline's Gantt bars and the wide list tables (`/orders`, `/clients`,
   `/invoices`). `.ledger`, `.view-switch` and `.site-footer` all move together —
   they're kept at the same `max-width` on purpose, and always have been; change all
   three if you change one.
10. **The app has exactly two delete conventions — a trash icon, or a button
    labeled "Delete" — and nothing else.** A row inside a `.doc-list` (order
    line items, payments, inventory materials/others, documents) deletes via
    an `.icon-btn.icon-btn--danger` rendering `icon_trash()`
    (`inventory/templates/inventory/_icons.html` — shared across modules
    despite the name, same reuse as `.doc-list__delete-form` itself once was)
    inside a `.doc-list__actions` wrapper and an `.icon-form` around the lone
    `<form>` so it doesn't take up flex layout space, matching the inventory
    list's own Actions column. A row inside a `.settings-source-list`
    (`SourceOption`, `OrderType`, `InventoryType`, `InventoryUnit`,
    `DocumentType`, sender rules and their field mappings) deletes via a
    plain `.btn-secondary` reading **"Delete"**, next to that same list's
    "Hide"/"Unhide" button where the row isn't hard-deletable. Both read the
    same regardless of whether the underlying delete is hard (a sender rule)
    or governed by `can_delete`/hide-don't-delete (`SourceOption` and
    friends) — the convention is about how the control looks, not what it
    does underneath. The order page's **"This order"** section is the one
    place the "Delete" button appears outside a `.settings-source-list`: it
    deletes a whole record rather than a row in a list, so neither container
    applies, and it takes the label rather than inventing a third look.
    It's also the only delete in the app behind a confirm dialog, because
    it's the only one that isn't a single row someone can re-add.
    **Never introduce a text "Remove" button** — the order
    page's Line items and Payments sections, and three sender-rule buttons in
    `communications/templates/integrations.html`, used to read "Remove" and
    were switched to match this (trash icon for the two `.doc-list`
    sections, "Delete" for the two `.settings-source-list`-shaped ones)
    specifically so this rule holds everywhere, not just in the newer
    modules it was first built for.

Current tokens (top of `style.css`):
- `--paper` / `--paper-deep` — background (ivory / mid grey)
- `--ink` / `--ink-soft` — text
- `--hairline` — borders/dividers, dark charcoal-grey, **not** brown
- `--status-progress` (steel blue), `--status-ready` (gold/amber),
  `--status-delivered` (forest green), `--status-rush` (brick red) — deliberately
  distinct hues; do not converge these back toward a single earthy/brown family.
  These were the four *order statuses* until the lifecycle landed; now they're
  three stage colours plus rush, which is no longer a stage:
  - **`confirmed` and `in_progress` share `--status-progress`.** They're one
    stored stage under two names (`Order.display_status`), so a second hue
    would imply a second stage.
  - **`tentative` reuses `--badge-neutral`**, not a new colour — that token
    already means "deliberately non-committal, nobody has decided yet", which
    is what a tentative order is. Its timeline bar is **outlined and dashed
    rather than filled**: every other bar is committed studio time, and a
    solid bar in any colour reads as booked. The dashes carry the meaning; the
    colour only has to stay out of the way.
  - **`cancelled` uses `--ink-soft`** and gets no badge weight at all. It isn't
    undecided, worth knowing, urgent or broken — it's just over. Rows render
    muted via `.is-cancelled`, with the strikethrough on the item name only
    (striking a row of numbers makes them unreadable for no gain).
  - **`--status-rush` fills the whole bar**, overriding the stage colour.
    An overlay treatment (an inset ring plus a marker) was tried first and
    reverted: urgency is the thing that has to register first when scanning
    a schedule, and layered over the stage colour it was too quiet to do
    that job. Declared *after* the stage rules in `style.css` — same
    specificity, so source order is what makes it win.

    This does not re-create the old bug, because the fix was in the data
    model, not the palette: rush is a boolean beside the status now, and
    `Order.can_rush` confines it to `confirmed`. So the red replaces exactly
    one colour — the steel blue — and can never disguise whether something
    is ready or delivered. Its key sits under the timeline with the
    returning-client star (`.timeline__legend-sep` separates them), not in
    the filter row: rush isn't a stage you can filter to, so the "rush
    first" sort options are how you single it out.
- `--status-pending` (purple) — not an order status. The app's "something
  happened that you should know about" colour: the calendar's synced-event
  chip (`.chip--event`), the unopened-thread markers in the lead inbox
  (`.pill--new` and the row's left edge), and `.nav-badge--new`, the count of
  clients a sender rule created by itself
- `--badge-neutral` (medium grey) — the plain `.nav-badge`, i.e. the lead
  count. Deliberately non-committal: a lead is by definition undecided —
  client, supplier or spam, nobody has looked yet — and a louder colour would
  claim more than the app knows
- `--day-today` (red) — the current-day marker, and red for "broken/needs
  attention now": `.nav-badge--alert` ("an integration has stopped syncing")
  and `.nav-badge--stock-alert` (inventory items at zero/negative stock).
  Red means broken.
- `--status-ready` (gold/amber) — besides the "ready" order status, the
  "needs attention *soon*" badge weight: `.nav-badge--low-stock`, the count of
  inventory items running low (above zero but at or below their own warning
  point), plus the amber-bordered `.warning-note` it mirrors on the order
  Materials tab. One tier softer than red. **The four badge weights are the
  whole scheme: grey = undecided, purple = worth knowing, amber = needs
  attention soon, red = broken.** Amber was added deliberately with the
  inventory low-stock feature — a genuinely milder severity than the red
  out-of-stock signal it sits beside, not a count promoted because it felt
  important. Don't add a fifth, and don't promote a count from one weight to
  another because it feels important
- Fonts: Inter (body + effectively headings too) — a Google Font, loaded in
  `base.html`'s single `<link>`. **There is no mono font anywhere in the app
  any more.** It started as IBM Plex Mono, was swapped for Roboto Mono, then
  removed everywhere at the user's request (orders/clients/invoices lists,
  settings, the order and invoice detail pages, the calendar, the
  communications module, `.invoice-ref`, `.nav-badge` — plain body text
  throughout instead), including the now-unused Roboto Mono request itself
  dropped from `base.html`'s Google Fonts `<link>`. Don't reintroduce mono
  anywhere without checking first.

- **A legend row takes three kinds of thing, and they read differently.**
  `.legend__item` buttons filter what's already on the page (statuses,
  with/without orders); `.legend__new-order` is the one primary action,
  pushed right by `margin-left: auto`; and `.legend__aside` is a quiet
  `--ink-soft` link to a *different* list — currently only the hidden-clients
  archive. The distinction is worth keeping: a filter that reloads the page
  and a link that doesn't are the same gesture with different consequences,
  so they shouldn't look alike. `.legend__aside` matches `.back-link`'s weight
  and hover, and exists separately only because that one carries a bottom
  margin for the top of a detail page.

- **`.detail-lifecycle` is "things you can do to this record that aren't a
  field edit"** — the order page's rush/cancel/delete, the client page's
  Hide. Always the same shape: an `<h2>` naming the record ("This order",
  "This client"), a `.lifecycle-actions` row of buttons, and a
  `.detail-note` underneath that says what the buttons will and won't touch —
  *and why a button isn't there when it isn't*. That note is the load-bearing
  part; a control that vanishes without explanation is the failure mode the
  whole block was built to avoid.

- **`.detail-form` caps at 360px**, which is right for a name, a date or an
  amount and wrong for prose. Two places override it rather than widening the
  base rule: `.compose-form` (the email reply box) and `.ai-form` (Settings →
  AI's prompt boxes). Both use the identical arrangement — the form runs full
  width so the textarea does, and a `__field` modifier caps the short inputs
  beside it back to 360px. **If a third form needs a wide textarea, copy that
  pattern**; don't raise `.detail-form`'s cap, which would stretch every
  single-line field in the app.

**When extending the UI:** match the current restrained, high-contrast, no-flourish
look. Don't add serif/display fonts, decorative stitching, or brownish/muted accent
colors without checking first — all three have been explicitly removed once already.

## User design preferences (persistent, apply to future styling work)

- Dislikes brown/earthy/muted tones for anything meant to be legible or distinct
  (hairlines, status colors) — prefers clear, high-contrast, distinct hues instead.
- Prefers sans-serif over serif or novelty display fonts; has rejected two display
  font attempts (Cormorant Garamond, Space Grotesk) in favor of no separate display
  font at all.
- Wants modern, minimal iconography (inline SVG) over text glyphs/HTML entities for
  UI controls like nav arrows.
- Cares about layout stability — noticed and asked to fix the footer jumping between
  months with different row counts.
