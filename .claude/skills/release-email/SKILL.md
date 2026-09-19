---
name: release-email
description: Write the announcement email for a new version of the order book, sent to the studio, with screenshots, in the established layout and voice. Use when asked for a release email, promo email, or "what's new" email for a version.
---

# Release announcement email

The audience is the studio owner: someone who uses the app every day and
doesn't care how it's built. The email tells them what they can now do, shows
it, and stops. Version 1.1.0's email (weekly calendar view) set the style;
everything below is what that style consists of, so a new email reads as the
same series.

Output goes in `docs/releases/<version>/`, which is gitignored. Nothing here is
committed.

## Workflow

1. **Get the version number** from the user if they haven't said. Don't guess.
2. **Work out what shipped**, from `git log` and `git diff` since the last
   release, `docs/views.md`, and the running app. Use the app's behaviour, not
   the code's vocabulary: if the diff says "cell_event_limit", the user-facing
   fact is "the month shows up to three appointments per day".
3. **Pick the features worth an email.** Things the studio will notice or use.
   Skip refactors, tests, internal tooling. Usually two to four. If something
   is a fix rather than a feature, one sentence in a feature's paragraph is
   plenty.
4. **Take screenshots** (below), one per feature.
5. **Write `email.src.html`** from `template.html` in this folder.
6. **Build `email.html`**: `python .claude/skills/release-email/scripts/embed_images.py docs/releases/<version>`
7. **Render it to check** (Playwright, or the browser pane) at 700px and 375px
   wide: images load, text and images are the same width at both sizes.
8. Tell the user where the files are and how to send (last section).

## Voice

- **No greeting, no sign-off.** No "Hi [Name]", no "Best". Nothing to fill in
  before sending.
- The h1 is `Version X.Y.Z is out! ` followed by a short headline naming what
  changed, e.g. "A weekly calendar, and a tidier month".
- One short intro paragraph: the problem they had, then "This update fixes
  that. Here is what is new in version X.Y.Z."
- Each feature is a numbered h2 ("1. A weekly view") in plain words, then two
  or three sentences on what they see and can do now, then the screenshot.
  Describe hovering, clicking and where things appear. Never mention routes,
  components, columns or code.
- Plain sentences, "you", warm but not salesy. No exclamation marks except
  the two fixed ones (the title, and the closing line). No em dashes.
- Reassure only if it's true: "Nothing changes in how you create or edit
  appointments, and there is nothing to set up." If a migration or setting
  changes, say that instead.
- **The last line is always** "If something feels off, or you have an idea for
  what should come next, just reply to this email!" (with the exclamation
  mark).
- Don't put real client, company or personal details anywhere: not in the
  copy, not in screenshots. Use generic stand-ins.

## Layout (all of it is in `template.html`)

- 600px wide, white page, **no outer border, no card**.
- **Text and screenshots are the same width**: no side padding on desktop, so
  both fill the 600px. On screens 600px or narrower the `.px` class adds 20px
  each side to text, dividers **and** screenshots together (a `<style>` media
  query, which Gmail and Apple Mail honour).
- Screenshots have no border of their own; their bone background reads
  against the white page.
- 1px `#d9d9d6` divider between features. Title 26px, feature h2 18px, body
  15px at 1.6 line height, ink `#1a1a1a`, system font stack.
- Layout deliberately plain: no buttons, no banner image, no footer.

## Screenshots

Taken from the real app with Playwright (`e2e/node_modules` has it and its
Chromium is installed), at `deviceScaleFactor: 2`.

- **Size the viewport so text is readable once shrunk to 600px wide.** A
  1200px-wide capture shrinks to half and the app's small text becomes
  unreadable. 800 to 1000px works: narrow enough to read, wide enough that the
  layout doesn't collapse (the app's mobile layout starts below 680px, never
  go under that). Check by eye and adjust per screenshot; the month grid
  needed 1000 to stop cutting off times, the week grid was better at 800.
- **Crop to the content**: from the top of the page header to the bottom of the
  thing being shown, no empty page below it. `capture_screenshots.js` takes
  `from`/`to` selectors for that, or a `selector` with padding for a small
  element such as a set of icons.
- **Sample data**: generic, short titles that don't truncate at that width
  ("Fitting", "Pickup", "Supplier visit"), never real ones. Show the state that
  demonstrates the feature (five appointments on one day, not an empty week).
  Native hover tooltips can't be screenshotted, so describe them in words.
- **Start the app** with `preview_start` using the `atelier-verify` config in
  `.claude/launch.json` (port 5051), never a server from Bash. Sign in as the
  seeded dev admin (`admin@example.invalid` / `changeme`).
- That server uses the real `data/atelier.db`. If you seed sample rows, **delete
  them afterwards** and stop the server. A seeding script imports `app`, so run
  it with `PYTHONPATH` set to the repo root; if you point it at a different
  database, set `DATABASE_URL` *before* the import, since setting
  `SQLALCHEMY_DATABASE_URI` afterwards silently writes to the real one.

```bash
node .claude/skills/release-email/scripts/capture_screenshots.js e2e docs/releases/<version> spec.json
```

The spec format is documented at the top of the script. Keep `spec.json` in
the scratchpad, not the repo.

## Sending

`email.html` has the screenshots embedded, so it's a single file. Open it in a
normal browser tab (not a preview pane), select all, copy, paste into a new
Gmail message. Say so to the user, along with two caveats: the mobile side
margin is lost when Gmail strips the `<style>` block on paste, and if Gmail
drops the images anyway, the PNGs in the same folder can be inserted by hand.
The subject and preheader are in the comment at the top of the file.
