# AI module (`ai/`)

> Part of the `leather_orders` app — see the root [CLAUDE.md](../CLAUDE.md) for
> stack, conventions and design language.

**This file is structure: what lives where, and why the module is shaped this
way. [REQUIREMENTS.md](REQUIREMENTS.md) is behaviour** — every rule under
stable ids (`T-1`, `S-4`, `C-2`, `B-1`, …), including the ones for the two
features not built yet. Rules are cited here rather than restated; when the
two disagree, REQUIREMENTS wins. Changing behaviour means changing
REQUIREMENTS and the code in the same commit.

Two features that share one thing worth centralising: a company-held vendor
API key, encrypted at rest, and a company-held base prompt.

- **Inquiry replies** — draft a reply to a new enquiry asking for what a quote
  actually needs. It fills the compose box; a human edits and sends. **Nothing
  here ever sends mail** (`R-1`).
- **Renderings** — turn a mockup, pattern or sketch already attached to an
  order into a product rendering. The result is a *draft* until someone saves
  it into the order's documents (`G-1`).

Both are built.

## The boundary, and why it's the strictest one here

`ai/` imports **`db` from `models.py` and the host's `crypto.py`, and nothing
else of the app** (`B-1`). Not an `Order`, not a `Document`, not an
`EmailThread` — each of those reaches it as a plain dict through a hook
registered in `app.py`, the same pattern as
`documents.routes.register(resolve_order=...)` taken one step further.

That's stricter than `documents/` and `inventory/`, which both hold a real
`order_id` foreign key, and the strictness is the point: those two are wired to
*one* host concept each, so a foreign key costs them nothing. This module is
wired to **three**. Reaching into any one directly would tie it to all three at
once, and a vendor key plus a prompt is the most portable thing in this
codebase — there's no reason for it to know what an order is.

`tests/test_ai_boundary.py` enforces this by reading the source, the same way
`test_billing_boundary.py` does for `billing/`.

## Layers

| File | What it's for |
|---|---|
| `config.py` | Model ids, timeouts, size caps, where render drafts live. All env-overridable, same convention as `documents/config.py`. |
| `crypto.py` | *Which* key this module's API keys are encrypted under — three lines over the host's `SecretBox`. |
| `errors.py` | `AIError`, the one exception raised outward. Its message is shown verbatim in the browser. |
| `conversation.py` | A thread dict → the transcript a model is shown. Pure, no database, no vendor, no Flask. |
| `openai_client.py` | The only file that knows OpenAI's API exists. Lazy import, and where vendor exceptions become sentences. |
| `google_image_client.py` | The same, for Gemini's image model. Its sibling in every respect. |
| `storage.py` | Render bytes on disk. Same shape as `documents/storage.py`. |
| `models.py` | `AISettings` (`ai_settings`) and `RenderDraft` (`ai_render_drafts`). |
| `migrations.py` | This module's own column migrations. Empty today; see below. |
| `services.py` | The public API. Every function takes `company_id` first (`T-1`). |
| `routes.py` | The blueprint, plus the `ai_reply_available()` context processor. Registered with a `resolve_thread_context` hook. |
| `templates/ai/settings.html` | Settings → AI, sharing `templates/_settings_nav.html`. |

## Inquiry replies — how the pieces connect

```
_compose_form.html  ──fetch──▶  POST /ai/suggest-reply
       (communications')                    │
                                            ├─▶ resolve_thread_context(company_id, thread_id)
                                            │       = app.py's _thread_conversation
                                            │         (the only place an EmailThread
                                            │          becomes a plain dict)
                                            │
                                            └─▶ services.suggest_reply
                                                    ├─ conversation.render  (what to send)
                                                    └─ openai_client        (how to send it)
```

Four things worth knowing before changing any of it:

- **`resolve_thread_context` is why this module never imports
  `communications`.** The translation from `EmailThread` to `{subject,
  messages: [...]}` happens in `app.py`, which already knows both sides.
  Same hook shape as `documents.routes.register(resolve_order=...)`.
- **The button lives in communications' template but the availability check
  doesn't.** `ai_reply_available()` is injected as a *callable* by this
  blueprint's `app_context_processor` — the same arrangement as
  communications' badge counts, and for the same two reasons: the query only
  runs where it's asked for, and the template never imports the module. It's
  guarded with `is defined` so the partial still renders on a deployment
  where this blueprint isn't registered at all.
- **The button only appears on a reply** (`R-6`). The client page's "New
  message" box has no conversation to draft from, and a suggestion built from
  nothing is a form letter.
- **`conversation.py` is pure on purpose.** Every judgement call about what a
  model gets to see lives there — whole thread, oldest first, oldest dropped
  when over budget, an oversized single message truncated rather than dropped
  — and each is testable without a network call or a fixture.

**Failures are sentences, not tracebacks** (`R-5`, `R-7`). `openai_client`
translates every vendor exception into an `AIError` whose message is written
for the person looking at the screen, matched on HTTP status rather than on
imported exception classes — importing those would mean importing `openai` at
module level, which is the thing the lazy import exists to avoid. The vendor's
own text is never passed through: an API error can echo request details,
including the key, and that message is rendered in the browser.

## Crypto — the one host helper

The Fernet mechanics moved out of `communications/crypto.py` into a root
**`crypto.py`** when this module needed the same thing. It's a `SecretBox`
class: one env var, one salt, `encrypt` / `decrypt` / `using_derived_key`.
`communications/crypto.py` and `ai/crypto.py` are both now three-line
declarations naming *their* variable and salt, keeping their own public names
(`TokenDecryptionError`, `KeyDecryptionError`) because the recovery differs —
reconnect the account, versus re-enter the key.

Two things to know before touching it:

- **Each purpose gets its own salt, and that's load-bearing** (`S-2`). Separate
  env vars let one key rotate without invalidating the other; separate *salts*
  are what make the derived-key fallback produce a different key per purpose
  from the same `SECRET_KEY`. Without that, ciphertext from one purpose would
  decrypt under the other's box.
- **Never change an existing box's `env_var` or `salt`.** Either one silently
  makes every value already encrypted under it unreadable. The `-v1` suffix on
  the salt strings exists so a deliberate rotation reads as `-v2` rather than
  as a rename.

The allowance for importing it is narrow and tested: `crypto.py` depends on the
standard library and `cryptography`, nothing else (`B-3`). If it ever grows a
Flask or models import, the boundary is broken and this goes with it.

## Settings — the shape, and the two traps

One row per company, created empty on first read — the same "created rather
than returned-as-None" contract as `billing.profile_for()` (`T-3`).

**There is no `enabled` flag** (`A-1`). Availability is derived from the key
being present, because a stored flag reading "on" beside an absent key is a
copy that can disagree with reality (hard rule 10), and "off" is already
spelled by deleting the key.

The two traps, both of which are real bugs the tests were checked against:

1. **A blank key field means "keep the saved key"** (`C-2`). The input is
   always rendered empty, because a saved key is never sent back to the
   browser (`S-4`) — so treating blank as "clear it" would wipe the key every
   time someone edited only the prompt. Deleting is an explicit button, reading
   **"Delete"** inside a `.settings-source-list` (hard rule 6).
2. **`has_*_key` reads the column, `*_key_hint` reads the value** (`S-5`).
   After an encryption-key rotation the value can't be decrypted, and that's
   precisely the state the page most needs to describe — so the hint swallows
   the failure and the page renders "a key is saved, and it can't be read"
   instead of 500ing.

**Prompts are instructions, not templates** (`C-6`). Context is appended at
call time rather than substituted into slots, so a company editing a prompt
can't break a placeholder. Saving one blank restores the shipped default
(`C-3`) — an empty prompt would produce garbage rather than an error.

**Model ids are settings fields with defaults, never constants** (`C-5`). Both
vendors rename and retire models on their own schedule; a hardcoded id turns
that into a deploy.

## Settings → AI, and the rename that came with it

The mail integration's nav link now reads **Email/Calendar** rather than
**Integrations** — accurate while it was the only one, misleading the moment a
second category appeared. Both halves are named because that page configures
Gmail *and* Google Calendar, and "Email" alone hid the second. Only the label
changed: the endpoint and URL are still `communications.integrations` /
`/settings/integrations`, because renaming a route to match a label churns
every `url_for` and every bookmark for no behavioural gain.

**AI** is the seventh `.settings-nav` category, after Email/Calendar. The nav lives in
`templates/_settings_nav.html` so this module shares it rather than keeping a
copy that drifts — the same arrangement communications introduced.

## Renderings — how the pieces connect

```
documents/_explorer.html  ── wand button (data-render-document) ──┐
                                                                  │ retargets
templates/order_page.html ── includes ai/_render_modal.html ──────┘  one dialog
                                     │
                                     ├─▶ POST /orders/<o>/documents/<d>/render
                                     │       ├─ load_document hook  (app.py)
                                     │       ├─ services.render_from_document
                                     │       └─ google_image_client
                                     │
                                     ├─▶ GET  /ai/renders/<id>/image
                                     ├─▶ POST /ai/renders/<id>/save
                                     │       └─ save_render hook → documents.services.upload
                                     └─▶ POST /ai/renders/<id>/discard
```

- **Neither module's template knows about the other's.** The explorer asks
  `ai_can_render(doc.content_type)` — a callable from this blueprint's context
  processor, guarded with `is defined` — and `order_page.html` includes the
  dialog. `app.py` is the composition root for templates too, not just imports.
- **One dialog per page, not per document** (`G-8`). The wand buttons carry a
  `data-render-document` id and the dialog retargets itself on open. Twenty
  photos on an order would otherwise mean twenty copies of this script.
- **Two hooks, both in `app.py`.** `load_document` hands over bytes;
  `save_render` puts them back through `documents.services.upload()`, so
  validation, content sniffing and the 1GB quota apply to a vendor's image
  exactly as to one someone dragged in (`G-4`). This module never reads or
  writes another module's storage.
- **`RenderDraft.order_id` / `source_document_id` are plain integers**, not
  foreign keys — deliberately unlike `documents/` and `inventory/`, which both
  hold real FKs into `orders`. A real FK would make this module importable only
  into a project that has an `orders` table, which is the one thing a vendor key
  and a prompt should never require. The cost is that a deleted order leaves
  drafts behind; the pruner collects them, and a draft is scratch by definition.

**Refuse before spending money** (`G-6`). A source that's the wrong type or too
large is rejected *before* the vendor call. Every other check can happen after;
these two are the ones where a charge would be incurred for something already
known to be doomed.

**A response with no image is a refusal, not a breakage** (`G-9`). "Try again"
is wrong advice for a request that will be refused again, so it gets its own
message. `_first_image` walks the response defensively rather than indexing it:
a shape we don't recognise has to read as "no image", not as an AttributeError
the user sees as a crash. It also skips text parts — the model often narrates
what it did, and the narration isn't the deliverable (`G-10`).

## The signature, and where it lives

`users.signature` — a **host** column, on Settings → Account, not here. A
signature is written by a person: two people sharing one `studio@` each want
their own, so a company-level one is wrong the day a second user exists, while
per-user with one user is only momentarily redundant (`SIG-1`).

This module never sees a `User`. `routes.py` reads `current_user.signature`
(the session already knows who that is — no host hook needed) and passes it to
`services.suggest_reply` as a plain string.

**It's appended in code, never asked of the model** (`R-11`). The prompt tells
the model to stop at its last sentence; the sign-off is then exact by
construction, costs no tokens, and can't be paraphrased into someone else's
name. Both halves matter — drop the prompt instruction and a draft carries an
invented sign-off *and* the real signature underneath it.

`User.signature_block` (a blank line, then the signature, or nothing at all) is
a property rather than string-building at each call site, because there are
already two — the compose box and the AI draft — and "sometimes two newlines,
sometimes none" is exactly what drifts apart between them.

## Migrations

`ai_settings` is a brand-new table, so `db.create_all()` covers it and
`ADDED_COLUMNS` is empty. `migrations.py` is wired into `app.py` anyway, and
that's deliberate: it means this module's *first* column migration lands in its
own file rather than in the root `models.py` list, which must not have to know
what this module stores (hard rule 12).

What it does carry is one **data** migration: a stored prompt still byte-for-
byte a *superseded* default is moved to the current one (`D-1`). Same class of
thing as communications' `_backfill_read_messages()` — safe on every boot, a
no-op once nothing matches. Without it the only way off an old default is to
blank the field, and a company that never knew the default had changed would
stay on it forever.

**Changing `DEFAULT_REPLY_PROMPT` means appending the old text to
`SUPERSEDED_REPLY_PROMPTS`** in the same commit, or existing installs keep the
prompt they never chose.

### The CRLF trap

The comparison is made on **newline-normalised** text, and that's not defensive
tidiness — it's the bug this migration hit against a real database (`D-2`).

A browser submits textarea content with **CRLF** line endings, per the HTML
spec, while every default in `config.py` uses LF. So a company that opened
Settings → AI and pressed Save without changing a word holds a byte-different
copy of the prompt it never edited, and a raw `WHERE reply_prompt IN (...)`
misses exactly the rows that need moving — which is every row that has ever
been through the form.

`services.normalise_newlines` stops new rows drifting; the migration normalises
on read to catch the ones already saved. The signature field is normalised the
same way, for the same reason.

## Environment

| Variable | Effect |
|---|---|
| `AI_ENCRYPTION_KEY` | Fernet key for stored API keys. Unset, one is derived from `SECRET_KEY` and the settings page says so (`S-3`). Both compose files pass it through. |
| `AI_TEXT_MODEL` / `AI_IMAGE_MODEL` | Defaults for the settings fields, not overrides — a company's saved choice wins. |
| `AI_RENDER_DIR` | Where render drafts land. Defaults under `data/`, the bind-mounted volume, same as documents and attachments. |
| `AI_DRAFT_RETENTION_HOURS` | How long an unsaved draft survives (48). |

The rest (timeouts, caps, retention) are in `config.py` with their reasoning
attached.

Nothing here needs to be set for the app to run. With no key saved, neither
feature's button renders and the module is inert.

## What's deliberately not here

REQUIREMENTS §8 has the list with reasons. The short version: **no spend cap**
(the most likely next requirement — a held key plus a Regenerate button is real
money per click), no provider registry for one implementation each, no
streaming, no stored conversation history, and **nothing that runs on a
schedule or on page load**. Every call in this module is a button someone
pressed.
