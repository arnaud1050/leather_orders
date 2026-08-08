# AI module — business requirements

Living record of the rules this module implements, kept for two reasons: so
future changes don't silently drop a rule nobody wrote down, and so there's
a checklist to test against. When a rule changes, update this file in the
same change.

**Phase 1 (this commit) is configuration only** — the settings a company
holds, and the two predicates that say whether a feature can be offered.
§6 and §7 describe features **not yet built**; they're written down now
because the phase-1 shape was chosen to fit them, and a rule invented later
to justify what got built is worth nothing.

Covered by `tests/test_ai_settings.py` and `tests/test_ai_boundary.py`
unless noted.

## 1. Tenant isolation (`T-*`)

- **T-1** Every service function takes `company_id` first and filters on it.
  There is no unscoped read anywhere in this module.
- **T-2** One `AISettings` row per company, enforced by a unique index on
  `company_id` — not by callers remembering to check.
- **T-3** A company's settings row is created empty on first read, not on
  signup. A tenant that never opens Settings → AI has no row and costs
  nothing.
- **T-4** One company's key, prompt or model is never readable from
  another's session, and never appears in another's rendered page.

## 2. Secrets (`S-*`)

- **S-1** API keys are **encrypted at rest** (Fernet, via the host's
  `crypto.SecretBox`). The column holds ciphertext; a plaintext key in the
  database is a bug, not a configuration choice.
- **S-2** This module's box uses its own env var (`AI_ENCRYPTION_KEY`) and
  its own salt. A value encrypted here is **not** decryptable by
  `communications/`'s box and vice versa, so the two purposes can't read
  each other's secrets even on one machine.
- **S-3** Without `AI_ENCRYPTION_KEY`, the key is derived from `SECRET_KEY`
  — a dev fallback with a real consequence, and **the settings page says
  so**. Same rule, and the same reasoning, as communications' `S-3`.
- **S-4** A saved key is **never sent back to the browser**. The page shows
  whether one is saved and at most its last four characters; the input is
  always rendered empty.
- **S-5** Rotating the encryption key must not break the settings page.
  `has_*_key` reads the column and `*_key_hint` returns `None` on a
  decryption failure, so the page renders "a key is saved, and it can't be
  read" rather than 500ing — which is the state a user in that situation
  most needs described.
- **S-6** A key is never written to a log, a flash message or an audit row.
- **S-7** No CSRF token layer, matching `documents/` and `app.py`'s own
  mutating routes, relying on `SESSION_COOKIE_SAMESITE=Lax`. This is a
  decision, not an omission — see `routes.py`'s docstring. Revisit it
  together with `communications/security.py` if Flask-WTF is ever added
  app-wide.

## 3. Settings and prompts (`C-*`)

- **C-1** A field a form doesn't render is left alone (hard rule 9). The
  two sections post to separate endpoints and neither can disturb the
  other's fields.
- **C-2** A **blank key field means "keep the saved key"**, never "clear
  it" — because the input is always rendered blank (`S-4`), so treating
  blank as a clear would wipe the key every time someone edited only the
  prompt. Deleting is an explicit button.
- **C-3** A **blank prompt or model restores the shipped default.** A
  company can't end up with an empty prompt, which would silently produce
  garbage output rather than an error.
- **C-4** Deleting a key keeps the model and prompt, so restoring the key
  restores the setup rather than starting it over.
- **C-5** Model ids are a settings field with a default, never a constant.
  Both vendors rename and retire models on their own schedule, and that
  must not require a deploy.
- **C-6** Prompts are stored as instructions, not as templates with
  placeholder slots. Context is appended at call time, so a company editing
  a prompt cannot break a substitution.
- **C-7** Deleting a key is spelled with a button reading **"Delete"**
  inside a `.settings-source-list` — one of the app's exactly two delete
  conventions (hard rule 6). Never "Remove".

## 4. Availability (`A-*`)

- **A-1** Whether a feature is offered is **derived from its key being
  present** (hard rule 10). There is no `enabled` flag: a stored flag
  saying "on" beside an absent key is a copy that can disagree with
  reality, and "off" is already spelled by deleting the key.
- **A-2** The two features are independent. Configuring replies does not
  enable rendering.
- **A-3** A document is renderable only when the key is present **and** the
  file is a raster image the module is willing to send (`image/jpeg`,
  `image/png`). SVG is an allowed upload and is never sent — the app
  already refuses to render one inline, and no image model wants it.
- **A-4** Every view route is `@login_required`, like every other view in
  the app.

## 5. The module boundary (`B-*`)

- **B-1** `ai/` imports **`db` from `models.py` and the host's `crypto.py`,
  and nothing else of the app**. It never imports a host model, `app.py`,
  or a sibling module.
- **B-2** Everything it needs about an order, a document or a mail thread
  arrives as a plain dict through a hook the host registers in `app.py` —
  the same pattern as `documents.routes.register(resolve_order=...)`, one
  step further.
- **B-3** `crypto.py` is the one shared host helper, allowed because it
  depends on the standard library and `cryptography` alone. If it ever
  grows a Flask or models import, `B-1` is broken and this allowance has to
  go with it.
- **B-4** This is stricter than `documents/` and `inventory/`, which both
  hold a real `order_id` foreign key, and the strictness is the point: this
  module is wired to *three* host concepts, so reaching into any one
  directly would tie it to all three.

## 6. Inquiry replies — **not built yet** (`R-*`)

- **R-1** The button drafts into the existing compose textarea. **Nothing
  in this module ever sends mail**; a human edits and sends.
- **R-2** The **entire thread** is sent as context, oldest message first,
  each labelled with who sent it and when — not just the latest message.
  A reply drafted from the last message alone re-asks what was answered
  three messages ago, which is worse than no suggestion.
- **R-3** Context is capped (`config.THREAD_CONTEXT_MAX_CHARS`). Past the
  cap the **oldest** messages are dropped, since a long thread's recent
  turns are what a reply has to answer.
- **R-4** Quoted reply chains are trimmed before sending, reusing the
  trimming the thread page already does — otherwise a five-message thread
  ships the first message five times.
- **R-5** A vendor failure (bad key, rate limit, timeout) surfaces as a
  readable message beside the button and leaves whatever is already typed
  in the textarea untouched.
- **R-6** The button doesn't render at all when no key is saved (`A-1`).

## 7. Renderings — **not built yet** (`G-*`)

- **G-1** A render is a **draft** until someone saves it. It does not
  become a document, and does not consume the company's 1GB document
  quota, until then.
- **G-2** The per-project text typed into the render window is **added to**
  the company prompt, not a replacement for it.
- **G-3** Regenerating keeps previous drafts visible for comparison, and
  each regeneration is a fresh vendor charge — the UI says so.
- **G-4** Saving a draft goes through `documents.services.upload()` via a
  host hook, so validation, quota and the per-company storage root all
  still apply. This module never writes into another module's storage.
- **G-5** Unsaved drafts are pruned after `config.DRAFT_RETENTION_HOURS`.
- **G-6** The source image is capped at `config.MAX_SOURCE_IMAGE_BYTES` —
  tighter than documents' own per-file limit, because it's uploaded to a
  third party rather than only stored.

## 8. Deliberately not built

- **No spend cap or rate limit.** A held API key plus a Regenerate button
  is real money per click, and the only ceiling right now is the vendor
  account's own. This is the most likely next requirement, and it belongs
  here (per company, per day) rather than in a template.
- **No provider abstraction.** One text vendor and one image vendor, named
  directly. `communications/providers/` exists because a second mail
  provider was a real prospect; a second AI vendor is not, and a registry
  for one implementation is a guess about the future dressed as
  architecture. The service functions are the seam if that changes.
- **No streaming.** A suggestion arrives whole or fails whole. Streaming
  into a textarea the user may already be editing is a race with no upside
  here.
- **No conversation memory.** Each suggestion is generated from the thread
  as it stands; the module stores no history of what it was asked.
- **No AI anywhere it isn't asked for.** Nothing here runs on a schedule,
  on sync, or on page load — every call is a button someone pressed.
