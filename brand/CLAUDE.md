# brand/ — the studio's logo

The *why* behind this package. The checkable rules are in
[REQUIREMENTS.md](REQUIREMENTS.md) (BL1–BL15).

## Why it exists

The logo was an invoice setting, owned by billing (`billing/logos.py`).
When Showcase's catalog mode needed it too, it stopped being an invoice
detail and became the studio's, so on 2026-10-06 it moved: the upload to
Settings → General → Brand, and the file and its rules here. Billing now
only **embeds** a logo: the host registers `brand.logo_png` as its source
(`invoicing.set_logo_source`), so billing still imports nothing outside
itself. Showcase gets it through `showcase_adapter.py`.

Like `features/`, it imports only `db` from the host, so a module could
depend on it without depending on the app. The settings routes are app.py's
(`upload_logo`, `delete_logo`, `brand_logo`), next to the other Settings
routes.

## The question only this package can answer

**What is this logo legible on?** Invoices print it on white paper
(Classic) or on the accent colour (Banded); catalog mode shows it on
near-black. A white mark on transparency is invisible on the first and
right on the last. `appearance()` reads the stored PNG's visible pixels:

- **opaque** — it fills its own rectangle, so it brings a background and
  reads anywhere;
- **light** / **dark** — the alpha-weighted lightness of the visible pixels,
  split at `LIGHT_LOGO_LUMINANCE`.

Derived from the file on every read, cached per file (names are unique per
upload), never stored — a replaced logo can't leave a stale answer, the
same "derived, never stored" rule as everywhere else (hard rule 10).

Two consumers: catalog mode puts only a dark logo on a light plate
(showcase SC26), and Settings → Invoicing warns when the saved layout and
colour would hide it (BL14). If auto-detection ever isn't enough (a
two-tone logo), the standard fix is a second upload, "logo for dark
backgrounds" — not built until a studio needs it.

A test-writing trap: a solid rectangle on transparency is **opaque** once
uploaded, because its transparent margins are trimmed (BL3a). Test marks
need transparency inside them, the way lettering has — `tests/test_brand.py`
draws frames.

## The move out of billing (BL15)

`migrations.adopt_billing_logos()` runs on every boot from app.py, after
billing's own migrations. It copies, writes the brand row, and only then
clears billing's column and removes the old file, so a crash part-way
leaves billing's copy to retry from. It reads `billing_profiles` with raw
SQL rather than importing billing's models. `billing_profiles.logo_filename`
stays as a column (SQLite can't drop one cheaply) and is always NULL after
the move; `BILLING_LOGO_DIR` stays in billing's config only so this
migration can find old files.

## Layers

| File | What it's for |
|---|---|
| `__init__.py` | The API: `set_logo`, `remove_logo`, `logo_png`, `logo_path`, `logo_filename`, `has_logo`, `appearance`, `readable_on`, `tone_of_png`. |
| `config.py` | `BRAND_DIR`, the upload cap, the two contrast thresholds. |
| `logos.py` | Checking an upload and re-encoding it to PNG; bytes on disk. Moved from billing, history intact. |
| `models.py` | `CompanyBrand` (`company_brands`): one row per company with a logo. |
| `migrations.py` | Column migrations (none yet), and the one-time adoption of billing's logos. |
