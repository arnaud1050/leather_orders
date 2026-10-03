# Communications module (`communications/`)

> Part of the `leather_orders` app — see the root [CLAUDE.md](../CLAUDE.md) for
> stack, conventions and design language.

**This file is structure: what lives where, and why the module is shaped this
way. [REQUIREMENTS.md](REQUIREMENTS.md) is behaviour** — every rule this module
obeys, why it exists and which test defends it, under stable ids (`N-1`, `F-4`,
`SY-17`, …). Rules are cited here rather than restated; when the two disagree,
REQUIREMENTS wins. Changing behaviour means changing REQUIREMENTS and the code
in the same commit.

Gmail + Google Calendar integration, built as a **self-contained module with its
own models, services, templates and blueprint** rather than more routes in
`app.py`. A deliberate exception to the root's "new routes go in `app.py`" rule:
this is the one part of the app meant to be liftable into another project whole,
and the requirements it was built from (`Communications Integration Module`,
Phase 1) call for it explicitly.

**The one rule that keeps it modular:** the rest of the app talks to
`communications.services` and nothing deeper.

```python
from communications.services import email_service
email_service.send_email(company_id, to=..., subject=..., body_text=...)
```

`app.py` calls exactly two things from it — `communications.routes.register(app)`
and `calendar_service.events_by_day()` in `month_view()` — and never imports a
provider. Reaching into `communications.providers` from a route would work today
and break the first time a second provider exists.

## Where the rules are

| Looking for | REQUIREMENTS.md § |
|---|---|
| Tenant isolation | §1 (`T-*`) |
| Encryption, OAuth, CSRF, XSS, scopes, audit | §2 (`S-*`) |
| Provider abstraction, timestamps, Google quirks | §3 (`P-*`) |
| Sync windows, idempotence, scheduling, settings forms | §4 (`SY-*`) |
| Sending and replying | §5 (`SEND-*`) |
| The lead inbox, hide/trash, conversion | §6 (`L-*`) |
| Badges, pills, alerts — three counters, three clearing rules | §7 (`N-*`) |
| Sender rules | §8 (`R-*`) |
| Contact-form field mapping | §9 (`F-*`) |
| Reading a conversation | §10 (`M-*`) |
| Calendar and time zones | §11 (`CAL-*`, `TZ-*`) |
| Deliberately not built, and what to settle first | §12 |
| Known coverage gaps | §13 |

## Layered structure, and what each layer is for

- **`providers/`** — the only code that knows a vendor API exists. `base.py`
  defines the `EmailProvider` / `CalendarProvider` interfaces plus the neutral
  dataclasses everything else speaks (`FetchedThread`, `FetchedMessage`,
  `FetchedEvent`). `gmail_provider.py` is the only file containing label ids,
  `payload.parts` MIME trees, base64url bodies or Gmail's `q:` syntax.
  `registry.py` maps `EmailAccount.provider` (a string in the database) to a
  class — **the single place** an `if provider == "gmail"` is allowed to live
  (`P-1`, `P-2`). Adding Microsoft Graph is a new module here plus two dict
  entries there; `services/`, `sync/`, `routes.py` and every template are
  untouched.
- **`oauth/`** — how a company *grants* access once. Separate from `providers/`
  (how we *use* it afterwards) because a second provider would need both, and
  they're independent.
- **`services/`** — the public API. Every function takes `company_id` first and
  filters on it (`T-1`).
- **`sync/`** — polling. Both syncs upsert on the provider's own ids (`SY-1`).
- **`storage/`** — attachment bytes. The model holds an opaque
  `stored_filename`; only this package knows it means a file on disk, so
  pointing it at S3 later is four functions, not a migration. Files live under
  `data/attachments/<company_id>/` — the same bind-mounted volume as
  `atelier.db`, so they survive a rebuild for the same reason.

**Column naming:** the source requirements spell these `gmail_thread_id` /
`gmail_message_id`; they're `provider_thread_id` / `provider_message_id` here,
because the same document also requires that business logic not depend on Gmail
(`P-3`).

## Data model

`Company → EmailAccount → EmailThread → EmailMessage → EmailAttachment`, plus
`CalendarEvent`, `EmailSyncSettings` (one row per company), `SenderRule` →
`SenderRuleField` / `AutoCreatedClient`, and `AuditLog`. `company_id` is
denormalised onto `EmailThread` and `CalendarEvent` (`T-5`).

**`EmailMessage.read_at` and `EmailThread.opened_at` are two different markers
and both are needed** — see `N-27`, and `N-7` for the reply case that makes
deriving one from the other wrong. Both are written by `mark_thread_opened()`,
which writes on a **GET** — normally worth avoiding, but it's idempotent,
destroys nothing, and opening the conversation is the only place "read" could
honestly be recorded.

### Migrations

`db.create_all()` creates the module's tables, so a brand-new *table* here never
needs a migration. A **column added to a table that already shipped does** — and
those live in **`communications/migrations.py`**, not in the root `models.py`
`_ADDED_COLUMNS`. Deliberately separate: putting communications columns in the
app's list would mean the root model file has to know what this module stores,
which is the coupling the module layout exists to avoid. Same "no-op once
applied, safe on every boot" contract as the root one. It's called from `app.py`
right after `run_migrations()`, because `app.py` is the composition root and a
call in either direction between `models.py` and this package would be circular.

What it currently carries, and the decision attached to each:

| Column | Added with | Note |
|---|---|---|
| `email_threads.dismissed_at` / `dismissed_reason` | lead triage | — |
| `email_threads.opened_at` | the "New" pill | replaced `LeadReadState` (below) |
| `email_messages.read_at` | the unread client-mail badge | **backfilled** by `_backfill_read_messages()`, the module's one data migration (`N-28`) |
| `email_sync_settings.calendar_frequency` | the calendar's own interval | DDL spells the default as a literal `30` — what the job was hardcoded to before, so an existing deployment keeps the cadence it had. Same "a migration records what shipped" rule as `companies.timezone` |
| `calendar_events.attendees` | editable guests | deliberately **not** backfilled — we genuinely don't know who was on an event synced before it shipped, and the next sync fills it from the provider |

**`LeadReadState` is gone** — one "when did this user last look" timestamp that
backed both the badge and the pill, and answered neither question (`N-1`, `N-5`,
and `N-2` for the specific bug). An existing database keeps an unused
`lead_read_states` table: dropping tables isn't something a boot-time migration
should do.

**`sender_rules.note` is gone too**, on the same terms (`R-27`). It was free
text meant to let a rule explain itself six months later, and it earned its keep
nowhere — a tag nobody read on the list, and a second box beside the only field
that matters once the address became editable. Removed from the model and both
forms; an existing database keeps the column, unused. Nothing reads it, so a
fresh database simply never creates it.

## Security — implementation notes

The rules are `S-1` … `S-15`. What isn't in that table:

- **Token encryption** (`crypto.py`, Fernet). The key comes from
  `COMMS_ENCRYPTION_KEY`, falling back to one derived from `SECRET_KEY` via
  PBKDF2 for dev. The integrations page warns when it's running on the derived
  key — see `S-3` for why that warning is load-bearing.
  **The mechanics moved to the host's root `crypto.py`** when `ai/` needed the
  same thing for API keys; this module's `crypto.py` is now three lines naming
  *its* env var and *its* salt over a shared `SecretBox`, and keeps
  `TokenDecryptionError` as its own name because the recovery (reconnect the
  account) is specific to what it stores. The salt is unchanged, so existing
  ciphertext still decrypts — and it must stay unchanged. That root file is the
  one host import this module makes besides `db`; it depends on the standard
  library and `cryptography` alone, which is what keeps it from dragging the
  app in behind it (`ai/REQUIREMENTS.md` `B-3` tests that).
- **CSRF** (`security.py`) is enforced by a blueprint `before_request` hook, and
  forms write `{{ csrf_token() }}`, registered as a Jinja global.
  `SESSION_COOKIE_SAMESITE=Lax` is set in `app.py` as defence in depth.
  **If Flask-WTF is ever added app-wide, delete `security.py` and use
  `CSRFProtect`** — it exists only because there was nothing to defer to.
- **The PKCE `code_verifier`** rides in the session as `VERIFIER_SESSION_KEY`
  (`S-6`): `google-auth-oauthlib` auto-generates one inside
  `authorization_url()`, but the callback builds a *different* `Flow`.
- **`has_scope()`** is what gates the calendar and send features on what Google
  actually granted (`S-13`).

## Sync behaviour — implementation notes

Polling, not webhooks (Phase 1). Rules are `SY-*` and `L-*`; the code:

- `sync_account(account, since=...)` is the single entry point (`SY-3`).
- **Trash recovery** is `_recover_untrashed()` in `email_sync.py`, a
  reconciliation pass bounded by `config.TRASH_RECOVERY_WINDOW_DAYS` (35) —
  `L-10` explains why nothing in the normal flow could ever see an un-trash, and
  `L-11` why `is_trashed()` is tri-state.
- **Resurfacing a hidden thread** happens in `_store_message`; the rule-hidden
  exemption keys on `dismissed_reason="auto_hidden"` (`L-15`, `L-16`, `R-6`).
  **A hidden *client* is un-hidden in the same place**, on the same gating
  (`L-21`, and the host's `CL21` which owns the rule) — this is the module
  writing `Client.is_hidden`, within the `Client`/`SourceOption` reach it
  already has and no wider. No `R-6` twin, because no sender rule can hide a
  client.
- **Sender rules** are matched in `_store_thread`, gated on `is_new` (`R-9`).
  Automatic conversion calls `email_service.auto_create_client()` —
  `create_client_from_thread(commit=False)` plus an `AutoCreatedClient` row and
  its own `client_auto_created` audit event (`R-11`, `R-12`). The
  already-on-file case returns `(client, was_created=False)`, writes no
  `AutoCreatedClient` row and audits as `client_mail_linked` (`R-16` … `R-20`).
  **A rule is one address and one action.** `update_rule()` edits the pattern
  in place — cleaned and uniqueness-checked exactly as `add_rule` does, and
  excluding the rule from its own duplicate check so pressing Save unchanged
  isn't an error (`R-21`, `R-22`). The **field mappings come with it**, which is
  the whole point: changing contact-form provider shouldn't cost the studio
  every label. The **action stays uneditable** (`R-23`) — hide and convert are
  two lists because they're two different statements, and a control that moved a
  rule between them would say nothing about what happened.
- **Field mapping** is `sender_rules.parse_fields`; targets live in
  `FIELD_TARGET_LABELS` (full name, first/last, email, phone, **street, city,
  province, postal code**, `inquiry_type`, `first_message`, source,
  **Ignore**), the message-vs-blank-line exception in `prose_labels`, and
  blank-filling in `_apply_details()` (`F-1` … `F-15`, `F-19` … `F-23`).
  Source matching falls back to the company's is_other `SourceOption` — see
  "One `SourceOption` per company can carry a free-text box" in
  [docs/data-model.md](../docs/data-model.md).
  `FIELD_TARGET_GROUPS` is the same set arranged for the picker's
  `<optgroup>`s; a target in the labels but not the groups exists in the model
  and is invisible in the UI, which `F-23` pins.
  **One label per target** (`R-24` … `R-26`): `_conflicting_field()` refuses a
  second mapping onto a field another already fills, and names the one in the
  way. Two labels on one target isn't ambiguous so much as unguessable — the
  winner is whichever appears *last in the email body*, which the settings page,
  listing mappings in the order they were added, gives no hint of. `_TARGET_FILLS`
  is why "Full name" collides with "First name" (it writes both halves), and
  `_REPEATABLE_TARGETS` is why **Ignore** is exempt: it stores nothing and exists
  to terminate the field above it, so a form with three skippable lines needs
  three. The check runs on `add_field` only — never over what's already stored,
  since a deploy that broke somebody's working mapping would be the worse bug.
- **`province` is the one mapped field that is cleaned rather than stored.**
  It goes through `normalize_province`, and **anything unrecognised is dropped**
  (`F-20`, `F-21`) — the column picks the tax rate, so a raw "Quebec" matches
  no row in `PROVINCE_TAXES` and the client is charged nothing, with nothing on
  screen looking wrong. (It's declared `VARCHAR(2)`, but SQLite doesn't enforce
  that and stores the whole string; Postgres would truncate or reject. Wrong
  either way.) Note **where the function comes
  from**: it lives in `billing/tax.py` beside the `PROVINCES` table it's built
  from, and is re-exported by the host's `models.py` so this module imports it
  from a file it already depends on. Importing `billing.tax` directly from here
  would be a module reaching into a module, which is the boundary the layout
  exists to keep one-way. `postal_code` is uppercased (`F-22`); the other two
  address lines are free text.
- **A relay is not the person.** Once the mapping has put the thread on the
  right client, everything the UI *shows or sends to* has to follow:
  `EmailThread.contact_address` (the client's address, falling back to
  `counterparty`) backs the reply box, the thread header and the Emails-tab
  list, and `EmailMessage.sender_display` attributes a relayed message to the
  client rather than to Squarespace (`F-16`, `F-17`). "Relay" means an address
  a **convert** rule covers and nothing looser — `EmailThread.is_relayed_sender`,
  which caches the company's rules per thread instance (`F-18`).
  **`counterparty` still means what the header said**, and is what the lead
  inbox and `create_client_from_thread` read; the two only diverge once
  there's a client.
- **Manual conversion** reads `EmailThread.suggested_name`; `_split_name()` in
  `email_service.py` prefers what was submitted and defers to that property
  otherwise, so the form and the server-side fallback can't drift (`L-18`,
  `L-19`, `L-20`).

**Where the module reaches furthest into the host:** field mapping imports
`SourceOption` alongside `Client`. Same existing dependency, one model wider.
Anything beyond client details — an `Order` — must go through a host-registered
hook instead; see REQUIREMENTS §12.

## UI

Follows the app's existing conventions rather than inventing any. Behaviour is
`N-*` (badges), `M-*` (reading a thread) and `CAL-*`/`TZ-*` (calendar); this is
the markup map.

- **Settings → Email/Calendar** (`/settings/integrations`) is one of the
  `.settings-nav` categories, after General/Orders/Inventory/Clients/Invoicing
  and before AI. It read **Integrations** until `ai/` added a category beside
  it; the endpoint and URL are deliberately unchanged, so `url_for` and every
  bookmark still say `integrations`. That nav lives in
  `templates/_settings_nav.html` so the module's
  own template shares it instead of keeping a copy that drifts. Same for the
  client page's tabs (`_client_nav.html`), which gained an **Emails** tab.
- Connected accounts reuse `.settings-source-list` and `.pill--*` (with
  `--connected` / `--paused` / `--error` on the existing `--pill-color` scheme).
- **Leads** (`/mail/leads`) is a sibling tab of `/clients`, not a section of it,
  and carries its own mail-only **Sync now** (`SY-21`).
- **The badges**, all fed by one `app_context_processor` in `routes.py`
  injecting *callables* (`N-20`), rendered from `templates/_clients_nav.html`:

  | Badge | Class | Colour | Counts |
  |---|---|---|---|
  | Leads awaiting triage | `.nav-badge` | grey `--badge-neutral` | `N-1` … `N-4` |
  | Unread client mail | `.nav-badge--mail` | purple | `N-21` … `N-26` |
  | Auto-created clients | `.nav-badge--new` | purple | `N-10` … `N-13` |
  | Integration stopped syncing | `.nav-badge--alert` | red, shows `!` | `N-14` … `N-17` |

  **The two purple ones never both count the same client** (`N-10a`). A
  conversion always brings an unread enquiry with it, so the pair reading
  "1 1" on the Clients link was one form submission counted twice;
  `unseen_client_count` subtracts whoever `unread_counts_by_client` is
  already announcing, and `mark_thread_opened` acknowledges the
  `AutoCreatedClient` row so the suppressed badge can't reappear when the
  mail badge clears.

  Queries: `email_service._lead_thread_query` and `_unread_client_query` are
  shared with the lists they describe (`L-2`, `N-25`);
  `unread_counts_by_client()` is one grouped query;
  `account_service.failing_accounts()` reads `EmailAccount.last_sync_error`
  (`N-15`). `.nav-badge--mail` restates `color: #fff` because it renders inside
  a table cell and a nav link, both of which colour their descendants — that
  inheritance is what made the roster badge come out black (`N-21a`).
- **Per-thread unread counts** on the client's Emails tab come from
  `EmailThread.unread_count`, rendered by `_thread_list.html` as an "N new" pill
  plus a purple left edge, gated on a `flag_unread` the tab passes and the lead
  inbox doesn't (`N-29` … `N-31`). The two markers never both appear: a lead row
  says "New" (a word, about the conversation), a client row "3 new" (a number,
  about messages).
- **Reading a thread** (`thread_page.html`) uses `EmailMessage.sender_label`
  (`M-2` — which defers to `sender_display` for everything but the "You"
  substitution, so `app.py`'s AI transcript gets the name without it),
  `other_recipients` for the "Also sent to" line (`M-4`), and
  `body_display` for trimmed bodies — `_QUOTE_ATTRIBUTION` and
  `_starts_an_attribution` / `_QUOTE_ATTRIBUTION_MAX_LINES` in the module's
  `models.py` handle Gmail's hard-wrapped attribution (`M-5` … `M-8`). The reply
  form runs **full width**: `.compose-form` overrides `.detail-form`'s 360px cap
  and `.compose-form__field` caps the To/Cc/Subject fields back.
- **`_compose_form.html` carries a "Suggest response" button** owned by `ai/`,
  not by this module. It renders only on a **reply** (a new message from the
  client page has no conversation to draft from) and only when that module
  reports a key saved — via `ai_reply_available()`, a callable injected by
  its blueprint's context processor, so this template never imports it and
  the check is guarded with `is defined` for a deployment where that
  blueprint isn't registered. **It fills the textarea and sends nothing**;
  `send_message()` remains the only path to a provider. The thread itself
  reaches `ai/` as a plain dict built by `app.py`'s `_thread_conversation`,
  which is what keeps that module from importing this one — see
  [ai/CLAUDE.md](../ai/CLAUDE.md).
- **`_compose_form.html` also carries "Attach document"**, on the same
  terms and for the same reason: a document belongs to an *order*, and this
  module may not know what an order is. `app.py` registers both halves via
  `routes.set_document_attachments(list_for_client=..., load=...)` — one
  builds the picker's JSON (this client's orders, each with its documents,
  grouped by document type), the other turns the ids the form posts back
  into `OutgoingAttachment` bytes. Rules: `SEND-7` … `SEND-11`. The button
  renders when there's a client in context and the host wired the hooks up
  (`document_attachments_available()`); **whether there's anything to
  attach is answered inside the modal, not by hiding the button** — that
  would cost an EXISTS query on every page carrying a compose form, and the
  modal can distinguish "no orders yet" from "no documents on them", which
  a missing button can't. Picked documents render as a plain `.doc-list`
  inside the form, one hidden `document_id` per row, so removing one is the
  app's usual trash icon rather than a second convention.
- **Time rendering** goes through the **`local_datetime`** Jinja filter,
  registered by the module's `register()` alongside `csrf_token` so the module
  carries what its own templates need. **`local_time`** is the same conversion
  for a time-of-day alone (the calendar's event chips) — 12-hour, lowercase
  am/pm, no leading zero ("12:00pm") — built on `local_datetime` rather than
  duplicating the zone resolution. Rules: `TZ-1` … `TZ-4`.
- **Lead inbox rows** reserve the purple left edge as a transparent
  `border-left` on **every** `.thread-list__item` (`M-10`).
- **Calendar event chips** are `.chip--event` in `--status-pending` purple
  (`CAL-7`), `chip__title` over `chip__time`, each a `<button>` opening that
  event's edit dialog. The create/edit forms post into this blueprint — see
  [docs/views.md](../docs/views.md) for the month grid itself.
- The `return_to` convention is carried through the same way as everywhere else.

## Background jobs

`jobs.py` exposes `sync_email_accounts(app)`, `sync_calendar_events(app)` and
`refresh_oauth_tokens(app)` as **plain callables** — cron, Celery, a test or a
shell can call them directly, so swapping APScheduler later means rewriting
`start_scheduler()` and nothing above it (`SY-14`).

**The scheduler only starts when `RUN_SCHEDULER=1`** — both Docker deployments
run gunicorn with 2 workers and `--preload`, and an unguarded
`BackgroundScheduler` would start in *every* worker and race itself, the same
class of bug `--preload` was added to fix for seeding. Set it on exactly one
process. Left off — the default, and true of local dev — **the Sync now buttons
are the only thing that fetches anything at all**, and the integrations page
says so.

The three buttons and what each covers are `SY-21`; the per-tenant tick,
separate mail/calendar intervals and the `section` marker on each settings form
are `SY-16` … `SY-23`. In code: `jobs._accounts_due(calendar=...)` picks both
the interval (`EmailSyncSettings.sync_frequency` / `calendar_frequency`) and the
matching timestamp (`EmailAccount.last_sync_at` / `last_calendar_sync_at`).

## Setup

1. Google Cloud console → new project → enable the **Gmail API** and
   **Google Calendar API**.
2. OAuth consent screen: External, add the scopes from `S-12`, add yourself as
   a test user. (Sensitive scopes need Google verification before non-test
   users can connect — plan for that lead time before any real rollout.)
3. Credentials → OAuth client ID → **Web application**. Register the redirect
   URI, exactly matching `GOOGLE_REDIRECT_URI` including scheme and path.
4. Set the environment (a local `.env`, already gitignored):

```bash
GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... COMMS_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") python app.py
```

5. Sign in → Settings → Email/Calendar → **Connect Gmail**.

Without `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`, or without the libraries
installed, **the app runs exactly as before** — vendor imports are lazy and the
integrations page explains what's missing instead of 500ing.
