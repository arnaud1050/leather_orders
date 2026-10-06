# Brand — business requirements & rules

The living spec for `brand/`: the studio's logo, shared by invoices and
Showcase's catalog mode. When behaviour changes on purpose, update the rule
here in the same commit — if a rule and the code disagree, one of them is a
bug. [CLAUDE.md](CLAUDE.md) explains the *why*.

BL1–BL11 were billing's logo rules (`L1`–`L11`, billing/REQUIREMENTS.md
§14) until 2026-10-06, when the logo became the studio's; they carry over
with their wording, only the owner and the URLs changed.

## 1. What a logo is

- **BL1 — PNG or JPEG, decided by the bytes.** An upload is a logo only if
  it decodes as a PNG or JPEG image — **including a JPEG that Pillow reports
  as MPO**, which is what phones and cameras save. The filename and the
  browser's content type are ignored. GIF, WebP, BMP, TIFF and **SVG** are
  refused.
- **BL2 — What is stored is never the upload.** The image is decoded and
  written back out as a PNG of this package's making, so metadata and
  anything appended to the file are left behind. This is why SVG is out:
  it can't be made inert without rasterising it.
- **BL3 — Capped three ways.** The upload may be at most
  `config.LOGO_MAX_BYTES` (10 MB by default, `BRAND_LOGO_MAX_BYTES` — the
  same as nginx's `client_max_body_size`); an image declaring more than 40
  megapixels is refused before it is decoded; the stored copy is scaled
  down so its longest edge is at most 1200px, keeping its proportions and
  transparency, and never scaled up.
- **BL3a — Transparent margins are trimmed.** Fully transparent space around
  the mark is cropped away before storing. An entirely transparent image is
  left as it is.
- **BL3b — Camera orientation is applied** before storing.

## 2. Keeping it

- **BL4 — A refusal changes nothing.** `set_logo` raises `LogoError`, whose
  message is written for the person uploading, and the existing logo — row
  and file — is left exactly as it was.
- **BL5 — One logo per company, in a per-company directory**
  (`<BRAND_DIR>/<company_id>/`), named by the package (a fresh random name
  per upload), never from the upload. A stored name is re-checked for path
  containment before any file is opened.
- **BL6 — Replacing or removing deletes the old file.** A real delete:
  invoices render the live logo (billing PD12), so nothing references an
  old one.
- **BL7 — Billing receives it, doesn't own it.** The host registers
  `brand.logo_png` as billing's logo source
  (`invoicing.set_logo_source`); billing embeds what it's given (billing
  L7). Showcase reads it through its adapter. This package imports only
  `db` from the host.

## 3. Settings → General → Brand

- **BL9 — Served only to its own company.** `GET /settings/brand/logo.png`
  takes no id: it serves the signed-in company's logo or a 404, requires a
  login, and is never cached.
- **BL10 — The form** (`POST /settings/brand/logo` and `…/logo/delete`,
  app.py) reads at most one byte past the cap, shows its result in the
  Brand section (MOD8), returns to `/settings/general`, and offers removal
  as a button reading **"Delete"** in a `.settings-source-list` — never
  "Remove". The old `/settings/invoicing/logo` and `/invoices/logo.png`
  routes are gone.
- **BL11 — Upload like an order document.** With no logo, an **"Add logo"**
  tile opens the file picker and takes a dropped image; with a logo, the row
  shows it with **Replace** and **Delete** and takes a dropped replacement.
  Choosing or dropping uploads at once after the same type and size checks
  in the browser, whose refusals show in the box at the top of the section
  in the page's own words. Without JavaScript, a plain Upload button does
  the same. All of it is the shared `upload-tile.js` (UT1–UT12).
- **BL13 — Shown where it's used.** With a logo, the section shows it twice
  — on white (a Classic invoice) and on near-black (catalog mode) — and
  says in a sentence how it will be placed (BL12).

## 4. What the logo reads on

- **BL12 — The tone comes from the pixels.** `appearance(company_id)` reads
  the stored file: a logo that fills its own rectangle (no meaningful
  transparency) is **opaque** and brings its background with it; otherwise
  the alpha-weighted lightness of its visible pixels decides **light**
  (≥ `LIGHT_LOGO_LUMINANCE`, 0.6) or **dark**. Derived on every read and
  cached per file, never stored. Catalog mode puts only a **dark** logo on a
  light plate (showcase SC26); light and opaque logos go straight on.
- **BL14 — A warning where it would vanish.** Settings → Invoicing shows an
  amber note when the saved look would hide the logo: a light logo on
  Classic (white paper), or on Banded an accent colour too close in
  lightness (difference under `MIN_CONTRAST`, 0.35). An opaque logo is
  never warned about. Checked against the *saved* layout and colour.

## 5. Moving out of billing

- **BL15 — Old invoice logos are adopted once.** On boot, every company
  with a logo on `billing_profiles.logo_filename` and none of its own gets
  that file copied from `BILLING_LOGO_DIR/<company_id>/` into its brand,
  then billing's column is cleared and the old file removed. Idempotent; a
  failure before the copy leaves billing's copy for the next boot. A name
  billing never generated (not 32 hex + `.png`) or a missing file builds no
  path and is just cleared. A company that already has a brand logo keeps
  it.

## Test coverage map

| Rules | Where |
| --- | --- |
| BL1–BL6, BL9–BL13, BL15, the boundary | `tests/test_brand.py` |
| BL7 | `tests/test_invoice_logo.py` (billing's side), `tests/test_showcase.py` (catalog mode) |
| BL10 (message slot) | `tests/test_save_notices.py` — `test_brand_saves_report_in_the_brand_section` |
| BL11 (in the browser) | `e2e/tests/brand-logo.spec.ts` |
| BL14 | `tests/test_invoice_logo.py` — the warning parametrised over layouts and colours |
