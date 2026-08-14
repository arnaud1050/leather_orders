# Communications module — business requirements & rules

Every rule this module is meant to obey, in one place, with **why** it exists
and **where it's tested**. Two audiences:

- **Changing the code.** If a change contradicts a rule here, that's the
  conversation to have before writing it. Several of these look like details
  and are load-bearing; the *why* is the part that matters.
- **Testing.** Each rule names the file that defends it. A rule with no test
  is marked ⚠ — those are the gaps, and they're listed together in §13.

Scope: `communications/` only (Gmail + Google Calendar, the lead inbox,
sender rules). Tax, invoicing and inventory rules live elsewhere. Design
and structural notes are in `CLAUDE.md`; this file is behaviour.

**This module accounts for the majority of the test suite.**

Rule ids are stable — cite them in commit messages and in review. Sections
are grouped by what a rule protects, not by which file implements it.

---

## 1. Tenancy

The isolation story, and the one no other rule can compensate for.

| # | Rule | Why | Tested in |
|---|---|---|---|
| **T-1** | Every service function takes `company_id` **first** and filters on it. | These are the functions routes hand URL parameters to. A tenant check at the bottom of the stack can't be forgotten at the top. | `test_account_service.py`, `test_email_service.py` |
| **T-2** | A tenant derives from `current_user.company_id`, **never** from the URL or a form field. | Otherwise any id in a path is an access-control bypass. | `test_routes.py` |
| **T-3** | A cross-tenant read is a 404, not an empty page or a 403. | A 403 confirms the row exists. | `test_routes.py`, `test_lead_triage.py` |
| **T-4** | Client matching only ever considers clients of the account's own company. | Two studios that email the same person get their own threads matched to their own records. This filter *is* the association security model. | `test_email_sync.py` |
| **T-5** | `company_id` is denormalised onto `EmailThread` and `CalendarEvent` even though it's reachable through the account. | Every list query filters by company first; a tenant filter that depends on remembering to join is one that eventually gets skipped. | `test_models.py` |
| **T-6** | Sender rules, field mappings and auto-created-client records are all tenant-scoped on read *and* write. | Same as T-1; these ones create clients, so a leak here writes into another studio's roster. | `test_sender_rules.py`, `test_form_mapping.py` |

---

## 2. Security

| # | Rule | Why | Tested in |
|---|---|---|---|
| **S-1** | OAuth tokens are **encrypted at rest** (Fernet). | A stolen copy of `atelier.db` must not be enough to read anyone's mail. | `test_crypto.py` |
| **S-2** | Ciphertext is non-deterministic, fails *typed* on a rotated key, and rejects tampering. | A silent decrypt failure would look like "no account connected". | `test_crypto.py` |
| **S-3** | Rotating `SECRET_KEY` without a `COMMS_ENCRYPTION_KEY` makes stored tokens unreadable, and the integrations page **says so**. | The derived-key fallback is a dev convenience with a real consequence; hiding it turns a re-connect into a mystery. | `test_crypto.py`, `test_routes.py` |
| **S-4** | The OAuth `state` token is random, stored in the session, and compared on callback. Missing, mismatched and replayed callbacks are all refused. | Without it a crafted callback URL attaches an attacker's mailbox to a victim's company. | `test_oauth.py` |
| **S-5** | The flow carries the `company_id` it started for in the session, and the callback re-checks it against `current_user`. | Covers the session changing identity mid-flow. | `test_oauth.py` |
| **S-6** | The PKCE `code_verifier` is carried in the session between the two `Flow` objects. | The callback builds a different `Flow`; without it the exchange fails with `invalid_grant: Missing code verifier`. | `test_oauth.py` |
| **S-7** | **Every** unsafe route in the blueprint requires a valid CSRF token — enforced by a `before_request` hook, not a per-route decorator. | A POST route added later is protected by default rather than if someone remembers. These routes send mail and disconnect mailboxes. | `test_routes.py`, `test_lead_triage.py`, `test_sender_rules.py`, `test_form_mapping.py` |
| **S-8** | Message bodies render from `body_text`, **never** `body_html`. | The HTML comes from an arbitrary sender; `\|safe` onto our own origin is stored XSS. | `test_routes.py` |
| **S-9** | A `<script>` in a subject or body renders escaped. | Same, via the other route in. | `test_routes.py` |
| **S-10** | Attachments are served `application/octet-stream`, never inline, under a generated on-disk filename. | An attachment called `../../app.py` is a real thing people send. | `test_routes.py` |
| **S-11** | A `../../` filename cannot escape the company's attachment directory, **on write or on read**. | Both directions, because either alone is the bug. | `test_storage_and_jobs.py` |
| **S-12** | Requested scopes are the minimum: `gmail.modify`, `gmail.send`, `calendar`, identity. **Never** `https://mail.google.com/`. | That scope permits permanent deletion and drags the app into a much heavier Google verification tier for nothing used. | `test_lead_triage.py` |
| **S-13** | `EmailAccount.granted_scopes` records what Google *actually* granted, and features are gated on it. | Users can untick things at the consent screen. | `test_account_service.py` |
| **S-14** | Connect / disconnect / send / trash / auto-create / rule changes are all **audit-logged**. | These are the actions someone will later have to account for. | `test_routes.py`, `test_lead_triage.py`, `test_sender_rules.py` |
| **S-15** | `audit.record()` never raises. | Failing a send that already went out, because a log row wouldn't write, is worse than the missing row. | `test_routes.py` |

---

## 3. Provider abstraction

| # | Rule | Why | Tested in |
|---|---|---|---|
| **P-1** | Only `providers/` knows a vendor exists. Label ids, MIME trees, base64url and Gmail's `q:` syntax appear nowhere else. | Adding a second provider is a new module plus two registry entries, with `services/`, `sync/`, routes and templates untouched. | `test_gmail_provider.py` |
| **P-2** | `registry.py` is the **only** place an `if provider == "gmail"` may live. | One seam instead of five. | `test_communications_boundary.py` |
| **P-3** | Columns are named `provider_thread_id` / `provider_message_id`, not `gmail_*`. | A column named for one vendor guarantees the next provider gets a misleading column or a migration. | `test_models.py` |
| **P-4** | Timestamps are **naive UTC everywhere**, normalised at the provider boundary. | Gmail returns epoch millis and RFC 3339 with offsets; a tz-aware value in a SQLite `DateTime` compares wrong against the naive ones around it rather than failing loudly. | `test_gmail_provider.py`, `test_timezone.py` |
| **P-5** | Google's **exclusive** all-day end date is converted in both directions inside `gmail_provider.py`. | Without it a one-day event occupies two cells of the month grid. Nothing above the provider should see the quirk. | `test_gmail_provider.py` |
| **P-6** | The interface exposes `trash_thread`, and **no** `delete_thread`. | The module holds no scope capable of irreversible deletion; the interface must not imply otherwise. | `test_lead_triage.py` |
| **P-7** | `ProviderError` vs `ReauthorizationRequired` splits "retry later" from "a human must reconnect". | The response differs; a 401 retried on a schedule never recovers. | `test_gmail_provider.py`, `test_email_sync.py`, `test_oauth.py` |
| **P-8** | **Tests never touch Google.** Fakes patch the registry *and* the names already imported into calling modules. | `from x import y` binds at import time, so patching only the registry misses every existing call site. | `tests/fakes.py` |

---

## 4. Sync

| # | Rule | Why | Tested in |
|---|---|---|---|
| **SY-1** | Every write is an **upsert** keyed on the provider's own ids. Running twice, overlapping a window, or retrying after a crash produces the same rows. | This is what makes polling safe to do often and safe to redo after failure. | `test_email_sync.py` |
| **SY-2** | Incremental windows **overlap by an hour**, and Gmail's `after:` gets an extra day. | Overlap costs a few upserts that become no-ops; a gap loses mail silently. | `test_email_sync.py` |
| **SY-3** | `sync_account(account, since=...)` takes an explicit window and is safe at any frequency on any subset of accounts. | Gmail push notifications later call *the same function* with a tighter window, not a second code path. | `test_email_sync.py` |
| **SY-4** | A sync failure is **stored on the account** (`last_sync_error`), not only logged; a successful sync clears it. | A mailbox that quietly stopped syncing three weeks ago is the worst available outcome. | `test_email_sync.py`, `test_integration_alert.py` |
| **SY-5** | A provider bug or unexpected exception fails **that account's** sync, not the job. | One broken mailbox must not stop the others. | `test_email_sync.py`, `test_storage_and_jobs.py` |
| **SY-6** | Client matching is exact-address, and **only ever adds** a client to a thread, never clears one. | A thread linked by hand has a client the address index may not know about. | `test_email_sync.py` |
| **SY-7** | The mailbox's own addresses are excluded from matching. | Otherwise a studio that has itself on file as a client matches every thread to itself. | `test_email_sync.py` |
| **SY-8** | `rematch_unassigned()` re-runs matching over orphan threads when a client is created or their email changes. | Mail that arrived before the client existed should attach without waiting for them to write again. | `test_email_sync.py` |
| **SY-9** | Unmatched mail is kept by default (`keep_unmatched`) and collects in the lead inbox. | Turning it off makes the app store only what it can attribute. | `test_email_sync.py` |
| **SY-10** | `keep_unmatched` is checked for **new threads only**. Flipping it off never deletes history. | Retroactively destroying conversations someone is reading is not something a checkbox should do. | `test_email_sync.py` |
| **SY-11** | Attachment **metadata is always stored**; bytes only when `sync_attachments` is on. | Attachments dwarf message text and most are signature images. The row exists either way so the UI can say "3 attachments". | `test_storage_and_jobs.py` |
| **SY-12** | One unreadable attachment must not lose the message it came with — the error lands on the sync result. | Same rule as SY-5, one level down. | `test_storage_and_jobs.py` |
| **SY-13** | Outgoing mail is stored **immediately on send**, keyed on the provider's ids so the next sync recognises it. | It appears in the client's history at once and isn't duplicated later. | `test_email_service.py` |
| **SY-14** | The scheduler runs only when `RUN_SCHEDULER=1`; jobs are plain callables. | Both Docker deployments run gunicorn with 2 workers and `--preload`; an unguarded scheduler starts in every worker and races itself. Cron, Celery or a shell can call the same functions. | `test_storage_and_jobs.py` |
| **SY-15** | "Sync now" calls the **same** `sync_now()` the scheduled job calls. | The manual and automatic paths can't drift. | `test_leads_badge.py`, `test_storage_and_jobs.py` |
| **SY-16** | Per-tenant intervals are honoured by one 5-minute tick that skips accounts whose interval hasn't elapsed. | No job per tenant. | `test_storage_and_jobs.py` |
| **SY-17** | **Mail and calendar have separate intervals** (`sync_frequency` / `calendar_frequency`), each read against its own timestamp. | Appointments move on a different timescale from mail and cost against a different quota. Previously the calendar ran on a hardcoded 30 minutes while the settings page offered only a mail frequency — so a company asking for 10-minute syncing got it for mail and silently not for appointments. | `test_storage_and_jobs.py` |
| **SY-18** | Both jobs tick at `TICK_MINUTES`; the per-company interval is enforced **inside** the job. | A job that wakes every 30 minutes cannot honour a company that asked for 10. | `test_storage_and_jobs.py` |
| **SY-19** | `sync_enabled` is the **master switch for both** kinds of sync. | One "is this tenant syncing at all" answer; two would eventually disagree. | `test_storage_and_jobs.py` |
| **SY-20** | A company with no settings row falls back to the model's defaults, **not** to syncing on every tick. | A tenant that has never opened the page must not hammer the API. | `test_storage_and_jobs.py` |
| **SY-21** | Each **Sync now** button does exactly what its label says: mail-only under Email sync (and in the lead inbox), calendar-only on the calendar page and under Calendar sync, both only under the combined button beside *Connect Gmail*. | A button that quietly does more than it claims is worse than one that does less — and with the scheduler off these are the *only* things that fetch anything. | `test_storage_and_jobs.py` |
| **SY-22** | Each settings form carries a hidden `section` marker, and a POST updates only that section. | An unticked checkbox posts **nothing**, so a calendar save processed wholesale would read every mail checkbox as "off" and switch them all off. Same rule as the client modal's address field, enforced explicitly because `in request.form` can't express it for checkboxes. | `test_storage_and_jobs.py` |
| **SY-23** | Intervals are **clamped** (5–1440 min), not validated-and-rejected. | They're dials, not data; a 1-minute frequency is a good way to get rate-limited by a number input anyone can edit. | `test_storage_and_jobs.py` |

---

## 5. Sending

| # | Rule | Why | Tested in |
|---|---|---|---|
| **SEND-1** | `send_email()` **commits itself**, unlike most of the codebase. | Once Gmail has accepted the message it has really gone out; a caller rolling back would leave no record of mail the client already received. | `test_email_service.py` |
| **SEND-2** | Replying into a thread sends from **that thread's** mailbox, whatever the default is. | Otherwise the reply comes from the wrong address and breaks the conversation on Gmail's side. | `test_email_service.py` |
| **SEND-3** | A reply carries `threadId` **and** the RFC 822 `In-Reply-To`. | Gmail threads on the former; every other client threads on the latter. | `test_email_service.py` |
| **SEND-4** | Failing to read the `Message-ID` for threading must not stop the send. | Best effort — worse threading beats no email. | `test_email_service.py` |
| **SEND-5** | "No mailbox connected" and "sending is turned off on every mailbox" are **different messages**. | Telling someone to connect a mailbox they already have connected sends them to re-run OAuth to fix a checkbox. | `test_email_service.py` |
| **SEND-6** | Recipient addresses are validated loosely (reject the obvious typo, accept anything plausible). | Over-strict RFC 5322 validation rejects real addresses. | `test_email_service.py` |
| **SEND-7** | A message may carry **attachments the caller supplies as bytes** (`OutgoingAttachment`), and this module never learns where they came from. | The one thing worth attaching here is a document filed against an order, and an order is exactly what this module may not know about (root CLAUDE.md rule 4). The host resolves ids into bytes; see SEND-9. | `test_mail_attachments.py` |
| **SEND-8** | The **total** attached to one message is capped (`config.MAX_OUTGOING_ATTACHMENT_BYTES`, 20MB) and refused **before** the provider is called. | Gmail measures the base64-encoded message — a third larger than the bytes counted here — and refuses over-limit sends with an opaque 400 *after* the whole thing has been uploaded. This one names the fix. | `test_mail_attachments.py` |
| **SEND-9** | Attachment ids arriving on the send form are **re-resolved against the sending company**, and anything that doesn't belong to it (or whose file is gone) is **dropped, not raised**. | They come from a hidden field the browser can rewrite, so they're a request rather than a fact — and one stale row shouldn't cost the user the message they just typed. | `test_mail_attachments.py` |
| **SEND-10** | The audit line for a send **names what was attached**. | "We emailed them the pattern" is exactly the question that log gets read to answer. | `test_mail_attachments.py` |
| **SEND-11** | The picker's data comes from a **host hook** (`routes.set_document_attachments`), and its route 404s when none is registered. | Same late-bound-hook shape as `documents.routes.register(resolve_order=...)`; a deployment without the documents module wired up simply doesn't offer the feature (the compose form's button is gated on `document_attachments_available()`). | `test_mail_attachments.py` |

---

## 6. The lead inbox and triage

| # | Rule | Why | Tested in |
|---|---|---|---|
| **L-1** | A lead is a thread with **no client** and **at least one incoming** message. | An outgoing-only thread to an unknown address is us mailing a supplier, not a lead. | `test_leads_badge.py` |
| **L-2** | The lead list and the badge count share **one query**. | Two implementations of "what is a lead" eventually disagree, and the number beside the link stops describing the rows behind it. | `test_leads_badge.py` |
| **L-3** | Clients are **never** created from an arbitrary sender. | An inbox is newsletters, suppliers and spam; a `Client` per sender makes the roster useless. Automatic creation happens only for senders a company has explicitly named (§8). | `test_email_service.py` |
| **L-4** | **Hide** is local-only and reversible; the row is **kept**, never deleted. | Deleting it only means the next sync re-downloads the thread and it reappears — a hide button that looks broken. | `test_lead_triage.py` |
| **L-5** | **Trash** calls the provider **first**, then dismisses locally. | A refusal from Gmail must not leave local state claiming mail was trashed while it's still in the inbox. | `test_lead_triage.py` |
| **L-6** | Trash is confirmed in the UI and audit-logged; Hide is neither. | Trash changes the studio's real mailbox. | `test_lead_triage.py` |
| **L-7** | There is **no permanent delete**, anywhere, at any level. | See S-12. Adding one is a scope and Google-verification decision before it is a code one. | `test_lead_triage.py` |
| **L-8** | A trashed thread **cannot be restored from the app** — the UI says to recover it in Gmail. | Restoring locally would show it in Leads while the mail stayed in Trash: a button that appears to work and doesn't. | `test_lead_triage.py` |
| **L-9** | Recovering a thread in Gmail **brings it back on the next sync**. | The UI promises this, so it has to be true. | `test_lead_triage.py` |
| **L-10** | Trash recovery is a **reconciliation pass**, asking the provider about threads *this app* trashed. | Nothing in the normal flow can see an un-trash: every query carries `-in:trash`, un-trashing creates no message, and the window is keyed on message dates that recovering doesn't change. | `test_lead_triage.py` |
| **L-11** | `is_trashed()` is **tri-state**. Only an explicit "recovered" restores; "unknown" (purged, or the id is gone) leaves the row dismissed. | Reading "can't tell" as "recovered" drops mail the user binned a month ago back into the inbox. | `test_lead_triage.py`, `test_gmail_provider.py` |
| **L-12** | Trash checks are bounded by `TRASH_RECOVERY_WINDOW_DAYS` (35). | Past Gmail's own 30-day purge there is nothing to recover, and unbounded this costs one API call per thread ever trashed, on every sync, forever. | `test_lead_triage.py` |
| **L-13** | A failed trash check is recorded on the result, not raised. | Mail that downloaded fine must still be stored. Same rule as SY-12. | `test_lead_triage.py` |
| **L-14** | A recovered thread returns to the **lead inbox**, not to some earlier state. | Trash is only offered on threads sitting in the inbox, so that's the state it left. | `test_lead_triage.py` |
| **L-15** | A **hand-hidden** sender who writes again resurfaces the thread. | Following up is new signal; an enquiry hidden by mistake shouldn't go silent forever. | `test_lead_triage.py` |
| **L-16** | Resurfacing applies to genuinely new **incoming** mail only, and never to trashed or rule-hidden threads. | Us mailing them isn't them writing back; and see R-6. | `test_lead_triage.py`, `test_sender_rules.py` |
| **L-17** | Converting a lead by hand also re-runs matching across the company's other orphan threads. | The new client's address may appear in more than one. | `test_email_service.py` |
| **L-18** | A new client's name is derived from the sender's display name, falling back to the readable local part of the address ("marie.alarie@…" → "Marie Alarie"). Both halves are always non-empty. | `Client` requires both, and a placeholder someone can correct beats refusing to create the client. | `test_email_service.py` |
| **L-19** | The conversion form arrives with that name **already in the boxes**, and the form and the server-side fallback read **one property** (`EmailThread.suggested_name`). | The boxes were blank with a note promising the name would be worked out on submit, so the only way to see the guess was to commit to it. Two copies of the rule — one in the template, one in the service — drift the first time either changes, and the symptom is a form that creates something other than what it showed. | `test_email_service.py`, `test_routes.py` |
| **L-20** | A submitted name still beats the suggestion. | It's a starting point, not a lock. | `test_email_service.py` |
| **L-21** | A **hidden client** who writes in is put back on the host's client list — `SyncResult.clients_resurfaced`, in `_store_message` beside L-15's own resurface, gated identically (incoming only, new messages only). | Same argument one model up, and the host's rule (core `CL21`) rather than this module's: hiding a client says "I don't deal with them at the moment", and them getting back in touch is exactly what withdraws it. No `R-6` exemption to mirror, because no sender rule can hide a *client* — hiding one is always a person's judgement. This is the module writing `Client`, which it is already permitted to do (`T-4`, §9); it is emphatically **not** licence to reach further into the host. | `tests/test_client_hiding.py` |

---

## 7. Notifications — the badge, the pill, the alert

Three counters, three different clearing rules. **They must not be merged**;
each answers a different question, and the previous single-timestamp design
answered none of them correctly.

| # | Rule | Why | Tested in |
|---|---|---|---|
| **N-1** | **The lead badge counts leads awaiting triage** and clears only when one is *converted, hidden or trashed*. | Looking is not doing. A badge that cleared on the way to the page stopped reminding anyone of the enquiry they hadn't answered — which was the only thing it was for. | `test_leads_badge.py` |
| **N-2** | Opening the leads page, the dismissed view, or a thread does **not** change the badge. | Corollary of N-1, and the specific bug it replaced. | `test_leads_badge.py`, `test_lead_triage.py` |
| **N-3** | The lead badge is **not per user**. | One studio triages one shared inbox; an enquiry is outstanding for everyone until somebody deals with it. | `test_leads_badge.py` |
| **N-4** | The lead badge is **medium grey**. | A lead is undecided by definition — client, supplier or spam, nobody has looked — and a louder colour claims more than the app knows. | `test_leads_badge.py` (markup), visual |
| **N-5** | **The "New" pill means nobody has opened that conversation**, and clears when *that thread* is opened. | A per-thread fact, not a per-visit one. | `test_leads_badge.py` |
| **N-6** | Only the **first** open is stamped, and opening one thread leaves the others flagged. | Re-reading shouldn't rewrite when it stopped being new; and the old design marked every thread read on arrival at the list. | `test_leads_badge.py` |
| **N-7** | A new reply does **not** re-flag a thread that's been read — the *pill* stays gone. | "New" is about the conversation ever having been opened, not about unread messages. A reply does raise the unread-mail count (N-22); the two markers are separate on purpose, see N-27. | `test_leads_badge.py`, `test_client_mail_badge.py` |
| **N-8** | A lead can be **read but still waiting** — pill gone, badge counting. | That state is the point: it's the enquiry you've seen and haven't answered. | `test_leads_badge.py` |
| **N-9** | The dismissed view renders **no** new/seen decoration, and the word **"New" never appears on a client's Emails tab**. | Something already triaged away isn't new, however unread; and a client's whole history isn't either. The tab carries the *other* marker instead — see N-29. | `test_leads_badge.py` |
| **N-10** | **The "new clients" badge** counts clients a rule created that nobody has seen, and **clears on a page view** of `/clients`. | The opposite of N-1 and right for the opposite reason: nothing is outstanding, the client already exists. It's a notice, and looking is the whole response. | `test_sender_rules.py` |
| **N-11** | One record per auto-created client, not one "last looked" timestamp. | A visit to the roster five minutes before the sync ran must not swallow the notice. | `test_sender_rules.py` |
| **N-12** | Those records are **acknowledged, not deleted**. | "This client arrived automatically" stays true after the badge is gone. | `test_sender_rules.py` |
| **N-10a** | **The two purple badges never both count the same client.** The new-clients badge stands down for anyone the unread-mail badge is already announcing, and opening the conversation that created a client **acknowledges that client** as well as marking the mail read. | A conversion always arrives with exactly one unread message — the enquiry that caused it — so one contact-form submission lit both badges on the Clients link, each reading "1", for one event. The unread half wins because it's the one with work attached: it clears by reading the enquiry rather than by glancing at a list, and the thread page names the new client at the top, linked to their record, which is the entire content of the other notice. Acknowledging on open is what stops the suppressed badge *appearing* once the mail is read. Suppression is not clearing: a client whose enquiry gets dismissed is still announced, which is the case this badge still exists for. | `test_sender_rules.py`, `test_form_mapping.py`, `test_client_mail_badge.py` |
| **N-13** | A client converted **by hand** never raises the badge. | You already know about a client you just created. | `test_sender_rules.py` |
| **N-14** | **The alert badge** shows `!`, not a count, when an integration's last sync failed. | What matters is *that* something stopped; the fix is the same trip either way. The tooltip carries the count. | `test_integration_alert.py` |
| **N-15** | It reads `last_sync_error` — no separate health flag. | It can't disagree with what the integrations page shows, and nothing has to remember to maintain it. | `test_integration_alert.py` |
| **N-16** | A **paused** account still counts as failing. | Turning sync off doesn't repair what broke. | `test_integration_alert.py` |
| **N-17** | The alert badge is **red**; the others are never red. | Three weights, and no more: grey = undecided, purple = worth knowing, red = broken. A fourth colour makes all four mean less. | `test_integration_alert.py` (markup), visual |
| **N-18** | Every badge is **derived, never stored**. | It cannot drift out of step with what it describes. Same reasoning as `Order.total` and `Invoice.display_status`. | all of the above |
| **N-19** | A badge must **never 500 a page**: no logged-in user (the login page) or missing tables both yield zero. | A decoration taking down the only route back in is indefensible. | `test_leads_badge.py`, `test_integration_alert.py` |
| **N-20** | Badge counts are injected as **callables**, so the query only runs where the badge is drawn. | A template showing neither costs nothing. | ⚠ none — see §13 |
| **N-21** | **Unread mail from an existing client is counted and shown** — on the Clients top-nav link, the Clients sub-nav link, beside each client's name on `/clients`, and on that client's own **Emails tab**. | A lead arriving was loud (inbox, badge, pill); a *client* writing in was silent, their thread quietly updating on a page nobody had reason to open. | `test_client_mail_badge.py` |
| **N-21a** | Every badge is a plain `<span>`, never a link, and always renders white on its colour. | The client's name beside it is already the link; two targets a few pixels apart makes a worse row to click. The colour is restated per badge because table cells and nav links colour their descendants — that inheritance is what made the roster badge black. | `test_client_mail_badge.py` |
| **N-22** | Counted per **message**, not per thread: new conversations *and* replies in existing ones. | A client conversation stays alive for years. Per-thread state would go silent after the first open, which is precisely when the client is most likely to write again. | `test_client_mail_badge.py` |
| **N-23** | It clears **only** by opening the conversation. Viewing the client list or the client's page does not. | Seeing that someone wrote is not reading what they said. The opposite of N-10, and for the opposite reason: this one is work. | `test_client_mail_badge.py` |
| **N-24** | Outgoing messages never count; leads never count here; **dismissed threads never count**. | You can't have unread mail you sent. Leads have their own badge. And hiding a thread — by hand or by a sender rule on a domain that's also a client — means "don't tell me about this". | `test_client_mail_badge.py` |
| **N-25** | The nav total and the per-client numbers come from **one query**; the per-client breakdown is a single grouped query, not one per row. | They must add up; and the roster renders every client, so a count each is the N+1 that only bites at a few hundred. | `test_client_mail_badge.py` |
| **N-26** | A waiting lead and unread client mail show as **two badges at once**, grey and purple, never merged. | Triaging a stranger and replying to a client are different work with different answers. | `test_client_mail_badge.py` |
| **N-27** | `EmailMessage.read_at` and `EmailThread.opened_at` are **both** kept, written by the same `mark_thread_opened()`. | Two questions: "has anyone ever looked at this" (once, per thread, drives the pill) and "is there mail here I haven't read" (forever, per message). Deriving the first from the second would make a reply re-flag a lead as never-looked-at — see N-7. | `test_client_mail_badge.py` |
| **N-28** | Adding `read_at` **backfills** from `opened_at` for already-opened threads, and is a no-op on every boot after. | Otherwise the feature's first act is to declare months-old mail unread, and nobody trusts it again. | `test_client_mail_badge.py` |
| **N-29** | On a client's Emails tab **each conversation carries its own unread count** (`EmailThread.unread_count`), marking the row and showing "N new". | The tab badge says a client wrote; with several conversations open it doesn't say which. The lead inbox's rule can't answer it either — a client thread has been opened many times, so "never opened" is permanently false exactly when a reply lands. | `test_client_mail_badge.py` |
| **N-30** | The row counts **add up to the tab badge**, and a dismissed thread reports **zero** — same exclusions as N-24. | A row claiming unread mail while the nav says nothing is worse than no row marker at all. | `test_client_mail_badge.py` |
| **N-31** | A row marker clears by **opening that conversation**, same as N-23 — and reappears when they reply again. | It's a view of the same `read_at` state, not a second one that could disagree. | `test_client_mail_badge.py` |

---

## 8. Sender rules

| # | Rule | Why | Tested in |
|---|---|---|---|
| **R-1** | Two actions: **hide** and **create a client**. | "I'll never act on this" and "this is always a real enquiry" are the two cases where a person adds nothing by deciding again each time. | `test_sender_rules.py` |
| **R-2** | Rules match the **sender of an incoming message**, never outgoing. | A rule about a domain the studio writes to would otherwise fire on its own replies. | `test_sender_rules.py` |
| **R-3** | A pattern is a full address or a domain (`@example.com`); a bare domain is read as the domain. | A relay or newsletter provider sends from a whole domain, and listing every address it uses is a losing game. | `test_sender_rules.py` |
| **R-4** | An **exact address beats a domain rule**. | "Hide everything from this provider *except* the contact form" must be expressible; without the precedence the specific rule is unreachable and a week of enquiries goes quietly into the dismissed pile. | `test_sender_rules.py` |
| **R-5** | A pattern that can't match anything is **refused at entry**. | A rule that appears to be working and isn't is worse than an error message. | `test_sender_rules.py` |
| **R-6** | A **rule-hidden** thread does not resurface when the sender writes again (`dismissed_reason="auto_hidden"`). | A newsletter arrives every week; a rule that un-hides it every week has not worked. A person hiding one thread judges that conversation; a rule is a standing instruction about a sender. | `test_sender_rules.py` |
| **R-7** | Only **removing the rule** undoes R-6. | The instruction is the thing to withdraw. | `test_sender_rules.py` |
| **R-8** | Rule-hidden threads are **kept and restorable**, and hiding never touches the real mailbox. | A rule typed once should not be able to reach into someone's Gmail. | `test_sender_rules.py` |
| **R-9** | Rules apply to **new threads only**. | Adding a rule is an instruction about future mail, not a licence to reach back through history someone is reading (same as SY-10) — and it's what makes this idempotent under the overlapping window. | `test_sender_rules.py` |
| **R-10** | Re-syncing never converts the same thread twice. | Corollary of R-9 and SY-1. | `test_sender_rules.py` |
| **R-11** | Automatic conversion is audited under its **own event**, separate from manual conversion. | "The app did this" and "someone clicked it" are different things to have to answer for; a log that can't tell them apart answers neither. | `test_sender_rules.py` |
| **R-12** | Automatic conversion must be **announced** (N-10). | A client entered the roster without a decision. | `test_sender_rules.py` |
| **R-13** | A rule firing on mail that can't be converted is **not an error** — the thread stays in the lead inbox. | That's where it would have been anyway. | `test_sender_rules.py` |
| **R-14** | Rules are **hard-deleted**, and deleting one unwinds nothing it already did. | A rule is an instruction, not a historical answer other records reference (contrast `SourceOption`, which is hide-don't-delete). | `test_sender_rules.py` |
| **R-15** | Rule changes are audit-logged. | See S-14. | `test_sender_rules.py` |
| **R-16** | An address **already on file is reused, never duplicated**: the conversation is attached to that client and the form fills blanks only. | A returning customer filling in the contact form again is the normal case, not an error. It arrives from the same relay, so it's unmatched and the rule fires exactly as it did the first time — and what should happen is what a person would do: put the thread on the record that exists. | `test_form_mapping.py`, `test_sender_rules.py` |
| **R-17** | That case raises **no "new clients" badge** and writes **no `AutoCreatedClient` row**. | The badge means "somebody appeared on your roster while you weren't looking". Nobody did. The unread-client-mail badge (N-21) is what covers it, and already does. | `test_form_mapping.py` |
| **R-18** | It's audited as **linked**, not created, under its own event — naming the rule that did it. | "Created" would be a false claim, and "why is this conversation on this client" is a different question from "who added this client". | `test_form_mapping.py` |
| **R-19** | The sync summary counts it as a thread **matched**, not a client created. | Reporting a roster addition that didn't happen. | `test_form_mapping.py` |
| **R-20** | Matching is on the **address only** — never the name. | Two people share a name far more often than an inbox, and silently merging two client records is not something an unattended sync should be able to do. | `test_form_mapping.py` |

---

## 9. Contact-form field mapping

The labels are **configuration, not code** — entered per rule under
Settings → Email/Calendar, because every site words its form differently.

| # | Rule | Why | Tested in |
|---|---|---|---|
| **F-1** | **Only mapped labels are labels.** | A generic "anything before a colon" parser cuts a message in half at "Delivery: end of March", and nothing in the text distinguishes the two. The mapping is the parser's entire vocabulary. | `test_form_mapping.py` |
| **F-2** | Matching tolerates case, spacing, a trailing colon, and the indentation a forwarded form arrives with. | These are copied off a screen by hand. | `test_form_mapping.py` |
| **F-3** | The **longest** matching label wins. | Otherwise a `How` mapping eats `How did you hear about us?`. | `test_form_mapping.py` |
| **F-4** | A value ends at the next mapped label **or at a blank line**. | These emails separate fields with blank lines and end with a footer belonging to no field; without this the last field swallows it and a source value stops matching the option it names. | `test_form_mapping.py` |
| **F-5** | **The message is the exception** and may run across blank lines. | People write in paragraphs; truncating at the first one loses the enquiry. | `test_form_mapping.py` |
| **F-6** | An **Ignore** target exists, stores nothing, and serves only to end the field above it. | Without mapping Squarespace's `File Upload:` line, the rest of the form is stapled to the customer's message. F-5 makes this necessary. | `test_form_mapping.py` |
| **F-7** | A full name splits on the first space; one word leaves the last name empty. | Wrong for some names, and deliberately not clever: a guess correctable in one click beats a heuristic nobody can predict. | `test_form_mapping.py` |
| **F-8** | Mapped details **fill blanks only** — never overwrite. | This runs unattended. A phone number corrected by hand beats one retyped into a web form by the same customer months later. | `test_form_mapping.py` |
| **F-9** | On a **brand-new** client, the mapped message beats the raw body. | The raw body is the whole form plus its footer. | `test_form_mapping.py` |
| **F-10** | "How they heard about us" is **matched to an existing `SourceOption`, never creates one**. | An arbitrary string from a public form must not invent categories that then appear on every client record and in the analytics breakdown. | `test_form_mapping.py` |
| **F-11** | A form yielding no email still produces a client, under the **sending address**. | A client someone can correct beats a silent failure. | `test_form_mapping.py` |
| **F-12** | An **unmapped** convert rule behaves exactly as it did before mapping existed. | No silent behaviour change for rules already in place. | `test_form_mapping.py` |
| **F-13** | An unmapped rule **says so in the UI**, naming the consequence. | Otherwise a rule on a relay quietly produces clients named after the relay. | `test_form_mapping.py` |
| **F-14** | Deleting a rule deletes its mappings; deleting a mapping stops that field being read. | No orphans, no surprises. | `test_form_mapping.py` |
| **F-15** | The thread is linked to the **person**, not the relay. | So the conversation appears under Haejung Kim, not under Squarespace. | `test_form_mapping.py` |
| **F-16** | Every address the UI *shows or sends to* for a thread is `EmailThread.contact_address` — the linked client's own address, falling back to `counterparty`. | F-15 puts the conversation on the right record; without this the reply box, the thread header and the Emails-tab list all still read the relay's address, and pressing Send answers a no-reply robot. | `test_form_mapping.py`, `test_routes.py` |
| **F-17** | A message from a relay is **attributed to the client** (`sender_display` / `sender_label`), not to the relay. | The form submitted it; the customer wrote it. "Squarespace" over their own words is the same misattribution as M-2's, one step removed. | `test_form_mapping.py` |
| **F-18** | A relay is **only** an address a **convert** rule covers. Nothing else — an unmatched sender, a hide rule, or merely "not the linked client" — is relabelled. | The studio declaring "every enquiry from here is genuine and written by somebody else" is exactly the statement F-17 needs. Guessing would put the client's name over a message their architect actually wrote. | `test_form_mapping.py`, `test_models.py` |

---

## 10. Reading a conversation

The thread view prints nothing the page already says — a mail client's own
chrome, repeated per message, drowns the conversation.

| # | Rule | Why | Tested in |
|---|---|---|---|
| **M-1** | The header names the **contact address**, never the mailbox the thread synced through. | That's *our* address; printed next to the client's name it reads as theirs. (`contact_address`, not `counterparty` — see F-16 for the relay that made the difference matter.) | `test_routes.py` |
| **M-2** | Outgoing messages are labelled **"You"**; incoming show the person's **name alone**, falling back to the address. | Their address is already in the header. (Who "the person" is on a relayed message: F-17.) | `test_models.py`, `test_routes.py` |
| **M-3** | Sender labels are coloured to match the message's own left edge. | Ties each side to its border without a second label saying so. | visual |
| **M-4** | Recipients show as "Also sent to", **excluding** our mailbox, the client and the counterparty, and are omitted entirely when empty. | A two-party conversation's To: line says nothing. | `test_models.py`, `test_routes.py` |
| **M-5** | Quoted history is trimmed at the first `>` line or an "On … wrote:" attribution. | Those messages are already on the page in their own right. | `test_models.py` |
| **M-6** | Attribution matching handles Gmail's **hard wrap**, testing each line joined with the next couple. | `wrote:` routinely lands on the following line; unwrapped matching leaves the whole quote in place. | `test_models.py` |
| **M-7** | Trimming **falls back to the untrimmed body** when it would leave nothing. | A forward that is entirely quoted text must still be readable. | `test_models.py` |
| **M-8** | Previews are built from the trimmed body. | A one-line reply previews as that line, not as what it quoted. | `test_models.py` |
| **M-9** | The lead inbox carries no "Unmatched" pill. | Every thread on that page is unmatched by definition. | `test_leads_badge.py` |
| **M-10** | The unopened row's left edge is a transparent border reserved on **every** row. | Marking one new only colours the edge: nothing shifts sideways and the line never sits against the text. | visual |

---

## 11. Calendar and time

| # | Rule | Why | Tested in |
|---|---|---|---|
| **CAL-1** | The month grid renders **only** synced events; orders live on the Timeline. | Two different things on one grid read as one thing. | `test_calendar.py` |
| **CAL-2** | Events are **overwritten** on each sync; a cancelled event is kept with `status="cancelled"`, not deleted. | A meeting that moved should move here, and a sync must not make a row vanish from under someone. The month view filters cancelled out. | `test_calendar.py` |
| **CAL-3** | Form times are the **company's local wall clock**, converted to UTC on the way in. | Getting this wrong doesn't *look* wrong — it books the appointment a few hours out — so the test asserts the stored UTC value. | `test_calendar.py`, `test_timezone.py` |
| **CAL-4** | **No event UI at all** when no calendar is connected — and no calendar Sync now button either. | Better than a button whose only outcome is an error. | `test_calendar.py`, `test_storage_and_jobs.py` |
| **CAL-5** | Guests **are** editable, because `CalendarEvent.attendees` mirrors them. A form that changes guests posts the **complete** list; one that doesn't, omits the key entirely and the provider is sent no `attendees` at all. | Google's `patch` replaces the attendee array rather than merging, so a form that didn't know the current guests could only uninvite them — which is why this was refused until the mirror existed. Omitting the key is what stops an ordinary title edit from touching who is invited. | `test_calendar.py` |
| **CAL-6** | The client link is applied **locally** and never forwarded to the provider, using a sentinel so "don't touch" stays distinct from "clear it". | It's our concept, not Google's. | `test_calendar.py` |
| **CAL-7** | Event chips use `--status-pending`, deliberately not a fifth order-status colour. | An appointment isn't an order status and the four hues are spoken for. | visual |
| **CAL-8** | Attaching a guest and **emailing** them are separate acts. `notify` is read from **which submit button was pressed** (`send_invite`), never inferred from the guest list, and reaches Google as `sendUpdates=all`/`none`. | Inferring means linking a client to a private reminder quietly mails them. Spelling the flag out also fixes the original trap: `insert` defaults to notifying nobody, so the old "Invite" field attached an address and sent no mail at all. | `test_calendar.py` |
| **CAL-9** | The **linked client is invited automatically**, from `Client.email` — never retyped. The text field is for *additional* guests. Deduplicated case-insensitively, client first. | The studio picks a name, not an address. Google rejects a duplicate address, and the client's own is the obvious thing to type into the extras box. | `test_calendar.py` |
| **CAL-10** | A client with **no email on file** contributes no guest, and the form says so before anyone submits. A client from **another tenant** contributes none either. | An invite button whose only outcome is silence looks broken; and the client id arrives in a form field anyone can edit, so it must not become a way to have us mail another studio's roster. | `test_calendar.py` |
| **CAL-11** | The **organiser is preserved** across a guest edit — restored only if it was already on the list. | Google lists the studio's own mailbox among the attendees and the form hides it (`guest_list`), so without this every save would drop the organiser from their own appointment. Restoring it unconditionally would be the opposite bug: inviting ourselves to our own reminder. | `test_calendar.py` |
| **CAL-12** | The confirmation states **whether mail went out** ("No invitations sent." / "Invitation sent to …"). | A confirmation that doesn't mention the mail is one you have to open Gmail to verify. | `test_calendar.py` |
| **CAL-13** | The invite button is **hidden when there is nobody to invite**, and "Add event" comes first in the DOM. | An offer the form can't keep is worse than no offer; and Enter submits the *first* submit button, so the keyboard falls on the one that sends no mail. | visual |
| **TZ-1** | `Company.timezone` is **display only**; every stored timestamp stays naive UTC. | Changing it re-labels what's on file rather than moving it. | `test_timezone.py` |
| **TZ-2** | The zone list is **curated**, and anything outside it is **ignored rather than stored**. | `zoneinfo.available_timezones()` is 600 unordered entries, every one a way to mislabel your own mail; and an unresolvable stored zone would silently push every displayed time to UTC. | `test_timezone.py` |
| **TZ-3** | Rendering falls back to UTC with no user or an unresolvable zone, never an error. | A wrong-looking time beats a 500 on a page that merely mentions a date. | `test_timezone.py` |
| **TZ-4** | Times print **no zone name**. | There's one setting for the whole company; repeating it on every line says nothing. | `test_timezone.py` |

---

## 12. Deliberately not built

Not omissions — decisions. Each needs the stated question answered *first*.

| Thing | Why not, and what to settle first |
|---|---|
| **Permanent delete** | Needs the unrestricted `https://mail.google.com/` scope (S-12). A scope and Google-verification decision before a code one. |
| **Read state shared with Gmail** | Read state now exists *locally* (`EmailMessage.read_at`, N-22) but is never written back: marking a thread read here leaves it bold in Gmail, and reading it in Gmail leaves it counted here. `gmail.modify` permits the label change; nothing does it. Worth deciding deliberately — two-way read state is a sync problem, not a checkbox, and the app being wrong about what you've read is worse than it not knowing. |
| **Deleting a calendar event** | Nobody asked, and the same trash-vs-delete question as email needs answering first. |
| **Removing a guest tells them nothing** | Google notifies an *uninvited* attendee only on some paths, and the app doesn't distinguish "added Anna" from "dropped Luc" when it sets `sendUpdates`. Saving with notify on tells everyone still on the list; whether the person removed hears about it is Google's business. Settle whether that's worth surfacing before adding per-change notification. |
| **Merging duplicate clients** | R-20 matches on the address alone, so a returning customer who fills the form in with a different email gets a second client record. Merging is deliberately *not* something an unattended sync may do — two people share a name far more often than an inbox. A **manual** merge (pick two clients, move orders/threads/sources onto one, keep the older record) belongs to the host, not here: it would have to move `Order`s, which this module must not import. Settle where it lives — an app-side `/clients/<id>/merge` reading a host hook — before writing it. |
| **Creating an order from an enquiry** | `communications/` must not import `Order`. The honest shape is a host-registered hook (as `billing/` takes `resolve_billable`) or an app-side listener over `AutoCreatedClient` rows. Naming the order from the message is a Claude API job. **Settle the boundary before writing any of it.** |
| **Gmail push notifications** | Architecture is ready (SY-3); not built. |
| **A second provider** | A module in `providers/` plus two registry entries. Nothing above `providers/` should change — that's the test of P-1. |
| **The AI layer** | `EmailThread.summary` exists unused. Anything AI must stay independent of sync, which must keep working with AI off. |

---

## 13. Known gaps in coverage ⚠

Rules currently defended by reading the code rather than by a test:

- **M-3**, **M-10**, **CAL-7**, and the colour halves of **N-4** / **N-17** —
  visual, asserted only as markup. Nothing catches a token being changed.
- **N-20** (badge counts are lazy) — nothing asserts the COUNT doesn't run on
  a page with no badge. Cheap to add: render a template that shows neither and
  assert no query fired. Low stakes today; it becomes a real cost if the
  counts ever get expensive.
- **Nothing exercises a real OAuth round trip.** By design (P-8), but it
  means the first live connection after a change to `oauth/` is the test.

**Now closed:** `test_communications_boundary.py` defends **P-2** (the vendor
branch on `.provider` lives only in `registry.py`) and the §12 rule that the
module never imports `Order` — the boundary test previously flagged here as the
most valuable one missing, worth landing before the order-from-enquiry work.
It also pins the two sanctioned back-references into `app.py` (`back_label`,
`get_client_or_404`, imported in `routes.py`) so that coupling can't quietly
widen — an order helper pulled from `app.py` would be the §12 rule circumvented
without ever touching `from models import`.

---

## Changing a rule

If new behaviour contradicts a rule here:

1. Say which rule, and why the reason behind it no longer holds. The *why*
   column is the argument to beat — several of these were written after the
   obvious version was tried and failed (N-1 and N-5 replaced a single
   timestamp that answered neither question; F-4 and F-6 exist because the
   first parser stapled a form footer onto a customer's enquiry).
2. Change the rule here, in the same commit as the code.
3. Change or delete the test that defended it — deliberately, not by
   discovering it went red.

A rule that can be broken with the suite still green is a rule this file is
lying about. If that happens, fix the test before the code.
