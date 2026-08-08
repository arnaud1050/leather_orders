# AI module — business requirements

Living record of the rules this module implements, kept for two reasons: so
future changes don't silently drop a rule nobody wrote down, and so there's
a checklist to test against. When a rule changes, update this file in the
same change.

**Phase 1 was configuration; phase 2 added inquiry replies (§6).** §7,
renderings, is **not built yet** — written down before it exists because the
shape below was chosen to fit it, and a rule invented afterwards to justify
what got built is worth nothing.

Covered by `tests/test_ai_settings.py`, `tests/test_ai_reply.py` and
`tests/test_ai_boundary.py` unless noted.

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

## 6. Inquiry replies (`R-*`)

Covered by `tests/test_ai_reply.py`.

- **R-1** The button drafts into the existing compose textarea. **Nothing
  in this module ever sends mail**; a human edits and sends. There is no
  code path from here to a mail provider at all.
- **R-2** The **entire thread** is sent as context, oldest message first,
  each labelled with which side sent it and when — not just the latest
  message. A reply drafted from the last message alone re-asks what was
  answered three messages ago, which is worse than no suggestion.
- **R-3** Context is capped (`config.THREAD_CONTEXT_MAX_CHARS`). Past the
  cap the **oldest** messages are dropped and a marker takes their place,
  since a long thread's recent turns are what a reply has to answer. A
  single message longer than the whole budget is **truncated, not
  dropped** — otherwise the transcript would be empty and the model would
  answer a question it was never shown.
- **R-4** Quoted reply chains are trimmed before sending: the host hook
  passes `body_display`, not `body_text`, reusing the trimming the thread
  page already does. Otherwise a five-message thread ships the first
  message five times.
- **R-5** A vendor failure (missing library, bad key, unknown model, rate
  limit, timeout, empty completion) surfaces as **a sentence written for a
  person**, and leaves whatever is already typed in the textarea
  untouched. Each of the common statuses gets its own advice, because each
  has a different fix.
- **R-6** The button doesn't render at all when no key is saved (`A-1`),
  nor where there's no conversation to draft from — the client page's "New
  message" box, whose suggestion could only be a form letter.
- **R-7** The vendor's own error text is **never** shown. An API error can
  echo request details, including the key, back in its message, and that
  message would be rendered in the browser.
- **R-8** Speakers are labelled from `direction`, not from the app's
  `sender_label`, which renders our own mail as "You" — right on a page a
  human reads, ambiguous in a prompt, where "you" is what the model calls
  itself.
- **R-9** The company prompt is the **system** message and the transcript
  is the **user** message, so a thread containing text that looks like
  instructions can't displace the company's own.
- **R-10** A thread that isn't this company's answers **identically** to
  one that doesn't exist. "Not yours" and "not there" must not be
  distinguishable from outside.
- **R-11** The sign-off is **appended in code, never asked of the model**.
  The prompt tells it to end at its last sentence; the signature is added
  after. Exact by construction, costs no tokens, and can't be paraphrased
  into someone else's name — a wrong name on outgoing mail is worse than
  no name. The signature crosses the boundary as a plain string; this
  module never sees a `User`.
- **R-12** The shipped default prompt is written for a **one-person
  atelier** — first person singular, "atelier" not "studio". Changing it
  is changing `config.DEFAULT_REPLY_PROMPT` *and* appending the old text to
  `SUPERSEDED_REPLY_PROMPTS`, so §14 can move unedited copies forward.

## 6a. The signature (`SIG-*`)

Covered by `tests/test_signature.py`. The column is a host one
(`users.signature`) — this module only ever receives its value.

- **SIG-1** A signature belongs to a **user**, not a company or a mailbox.
  Two people sharing one `studio@` each want their own; a company-level one
  is wrong the day a second user exists, and per-user with one user is only
  momentarily redundant.
- **SIG-2** Blank is a **real value** meaning "no signature", unlike a key
  or a prompt. Nothing is destroyed by clearing it and there's no other way
  to say it. Stored as `NULL`, so there's one empty case downstream.
- **SIG-3** No signature leaves a message **exactly as it was before this
  feature existed** — `signature_block` is `""`, not a blank line.
- **SIG-4** The manual compose box is prefilled with it too, so a
  hand-typed reply signs off the same way a drafted one does.

## 14. Prompt defaults over time (`D-*`)

Covered by `tests/test_ai_migrations.py`.

- **D-1** A stored prompt is a company's own text and is **never
  overwritten** — except when it is byte-for-byte a *superseded* default,
  which can only be true if nobody ever edited it.
- **D-2** That comparison is made on **newline-normalised** text. A browser
  submits textarea content with CRLF while the shipped defaults use LF, so
  every row that has been through the settings form differs from the code
  by line endings alone. A raw equality check misses precisely the rows
  that need moving — which is what it did against a real database before
  this was fixed.
- **D-3** Prompts are **normalised to LF on save**, so new rows don't drift
  the same way. `services.normalise_newlines` is the one place that
  happens.
- **D-4** The refresh is idempotent and runs across every tenant — a
  boot-time migration has no signed-in user, the same exemption
  communications' scheduled sync has.

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
