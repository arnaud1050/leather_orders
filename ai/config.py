"""
Configuration for the AI module — which models are called by default, what
the limits are, and where render drafts live.

All env-overridable, same convention as `documents/config.py`.

**Model ids are defaults for a settings field, not constants.** Both
vendors rename and retire models on their own schedule, and a hardcoded id
turns that into a deploy. A company's saved choice wins; these only fill in
the field the first time.
"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Same data/ directory as atelier.db, communications' attachments and
# order documents — the bind-mounted volume in both Docker deployments, so
# a draft survives a container restart. Drafts are the one thing here that
# touches disk.
RENDER_DIR = os.environ.get("AI_RENDER_DIR", os.path.join(BASE_DIR, "data", "ai_renders"))

# --- Inquiry replies -------------------------------------------------------

TEXT_MODEL = os.environ.get("AI_TEXT_MODEL", "gpt-4o-mini")

# The whole thread goes to the model, oldest first, so a reply lands in
# context rather than answering the last line in isolation. This caps what
# "the whole thread" can cost: past it, the oldest messages are dropped and
# the newest kept, since a long thread's recent turns are what a reply has
# to answer. Characters, not tokens — the module doesn't carry a tokenizer,
# and roughly 4 characters per token makes this ~15k tokens of context.
THREAD_CONTEXT_MAX_CHARS = int(os.environ.get("AI_THREAD_CONTEXT_MAX_CHARS", 60_000))

# --- Renderings ------------------------------------------------------------

# "nano banana" — Gemini's image model, called with a Google AI Studio key.
IMAGE_MODEL = os.environ.get("AI_IMAGE_MODEL", "gemini-2.5-flash-image")

# A source image is uploaded to a vendor, not just stored, so it gets a
# tighter cap than documents' own 50MB per-file limit.
MAX_SOURCE_IMAGE_BYTES = int(os.environ.get("AI_MAX_SOURCE_IMAGE_BYTES", 8 * 1024 * 1024))

# What can be sent as a source image. Narrower than documents' allowed
# uploads on purpose: raster only, and no SVG (which the app already
# refuses to render inline, and which no image model wants anyway).
SOURCE_IMAGE_CONTENT_TYPES = {"image/jpeg", "image/png"}

# An unsaved draft is scratch. Kept long enough to survive a coffee break
# mid-comparison, pruned so a company that renders twenty variants and
# saves one isn't storing nineteen forever.
DRAFT_RETENTION_HOURS = int(os.environ.get("AI_DRAFT_RETENTION_HOURS", 48))

# --- Both ------------------------------------------------------------------

# A vendor call is made inside a request the user is watching, so it fails
# rather than hanging the worker. Image generation is much slower than a
# short text completion, hence two numbers.
TEXT_TIMEOUT_SECONDS = int(os.environ.get("AI_TEXT_TIMEOUT_SECONDS", 30))
IMAGE_TIMEOUT_SECONDS = int(os.environ.get("AI_IMAGE_TIMEOUT_SECONDS", 120))

# Starting text for a company's prompts — editable in Settings → AI, and
# only ever used to fill an empty field. Written as instructions to the
# model rather than as a template with slots: what's known about the thread
# or the order is appended as context at call time, so a company editing
# these can't accidentally break a placeholder.
DEFAULT_REPLY_PROMPT = """\
You are drafting a reply to a new enquiry for a one-person custom leather \
atelier. Write in the first person singular — "I" and "my", never "we" or \
"our"; there is only one person here.

Write a warm, brief reply that thanks them for getting in touch and asks \
for what's needed to quote the piece:

- what they'd like made, and what it's for
- rough dimensions, or an existing item to match
- leather colour and finish, and hardware finish
- any inspiration photos or sketches they can send
- when they need it by, and any budget they have in mind

Ask only for what the thread hasn't already told you. Keep it under 150 \
words, plain sentences, no bullet-point interrogation. Never invent prices, \
turnaround times, phone numbers or addresses.

End with the last sentence of your message. Do not write a sign-off, a \
name, or a signature — one is added automatically after your reply.\
"""

# Prompts that were once this module's default, newest last. A company's
# saved prompt is *its* text and is never touched — except when it's still
# byte-for-byte one of these, which means it was never edited, and leaving
# it would quietly pin that company to a default we've since corrected.
# See ai/migrations.py.
SUPERSEDED_REPLY_PROMPTS = ["""\
You are replying on behalf of a small custom leather goods studio to a new \
enquiry. Write a warm, brief reply that thanks them for getting in touch and \
asks for what's needed to quote the piece:

- what they'd like made, and what it's for
- rough dimensions, or an existing item to match
- leather colour and finish, and hardware finish
- any inspiration photos or sketches they can send
- when they need it by, and any budget they have in mind

Ask only for what the thread hasn't already told you. Keep it under 150 \
words, plain sentences, no bullet-point interrogation. Sign off with the \
studio name only — no invented phone numbers, prices, or turnaround times.\
"""]

DEFAULT_RENDER_PROMPT = """\
Produce a realistic product rendering of the finished leather item shown in \
this mockup, pattern or sketch. Keep the proportions, panel layout, stitch \
lines and hardware placement exactly as drawn. Render it as a finished \
piece in good studio lighting on a plain neutral background, with visible \
leather grain and stitching. Do not add features, hardware or branding that \
aren't in the source image.\
"""
