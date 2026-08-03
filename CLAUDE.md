# CLAUDE.md — WhatsApp Dementia Assistant

Read this before every task. If anything you are about to write conflicts with this file,
stop and say so instead of writing it.

## What this is

A hackathon-demo WhatsApp assistant for a dementia patient. Bilingual English / Simplified
Chinese. Python + FastAPI + PostgreSQL on Railway. A dedicated Twilio number
(+1 315 812 6378), connected to a Meta Business Account, as the channel — not the shared
Twilio sandbox. Gemini as the single multimodal AI provider. Google OAuth for read-only
Gmail + Calendar.

Full design lives in `docs/`. Section map in `README.md`.

---

## Non-negotiable invariants

### SAFETY-1 — The model never invents medical facts

Medication names, doses, and schedules come from the `medications` table and nowhere else.

- The LLM receives medications as a **read-only structured context block**. It may rephrase
  them into simple language. It may not add, correct, infer, or substitute.
- `app/safety/medication_guard.py` holds **two different guards**. They check opposite things
  and are not interchangeable — using the wrong one is an outage, not a style choice:
  - `enforce(text, medications, fallback)` — *completeness*: every name in `medications` must
    appear in `text`. Only correct where the text is **supposed to enumerate every row**
    (`_medication_query`, `_caregiver_medication_query`, `jobs/reminders.py`). Those are all
    template-rendered from the rows, so this is a defensive assertion against a template bug,
    **not a hallucination detector**. Never call it on ordinary conversation: a reply that
    legitimately mentions no medication fails it, and gets replaced with "It is time for your
    medicine" — wrong and dangerous for the patient.
  - `screen_for_unlisted_medication(text, medications, fallback)` — *contamination*: no drug
    name outside the patient's own rows may appear. For **model-generated free text**
    (`_general_qa`, `_caregiver_qa`). Catches the real failure mode — the model substituting a
    real, plausible drug for the patient's actual one ("Aricept" for "Donepezil").
- That second guard is a **bounded heuristic, not a guarantee.** Known limits, all deliberate:
  it matches against a curated ~50-name list in `medication_guard.py` (extend it before
  trusting it for an unrepresented drug class); a fully invented name passes; it scans
  Latin-script tokens, so it depends on the persona rule keeping drug names untranslated in
  Chinese replies; and it cannot tell an instruction from a description, so an informational
  mention ("her lab report mentioned insulin") can false-positive.
- **Three outbound paths mention medications and are guarded by neither** — the pill-bottle
  vision reply (`pipelines/image.py`), document summaries, and email summaries. They rely on
  prompt instructions alone. Known gap; do not assume blanket coverage.
- OCR and vision **never write to `medications`**. They write to `medication_candidates`
  with `status='pending'`. Only a caregiver action promotes a candidate.
- Never generate dosage advice, drug interactions, or "you can skip this one" reasoning.
  If asked, return the fallback and suggest contacting the caregiver or doctor.
  **Enforced by persona wording only — no code checks this.** Both guards above match on drug
  *names*; neither inspects what is said *about* a drug, so "take your Donepezil, though you
  could skip it today" passes both.

### SAFETY-2 — SOS is deterministic

The SOS path contains no LLM call. Trigger phrase match → look up emergency contacts →
send templated alert → log. An LLM outage must not break SOS.

### CHANNEL-1 — Never call the Twilio send API directly

All outbound goes through `app/channels/outbound.py`. That module owns the 24-hour window
check, the approved-template fallback, and the 1-message-per-3-seconds throttle. Direct
`client.messages.create()` calls anywhere else are a bug.

### CHANNEL-2 — Media and text are separate messages

Twilio silently drops the `Body` on a free-form media message. A voice reply is always two
sends: the text message, then the audio message. Never one.

### CHANNEL-3 — Voice notes are OGG/Opus only

MP3 arrives as a downloadable file attachment, not a playable voice note. TTS output must be
transcoded with ffmpeg to `audio/ogg` + libopus before sending. Media filenames must be
≤20 ASCII characters.

### WEBHOOK-1 — Acknowledge fast, process later

Twilio's webhook times out. The handler validates the signature, persists the raw message,
enqueues work, and returns `200` with an empty TwiML body — target under 500ms. All AI calls
happen in the background worker. Never `await` a Gemini call inside the webhook handler.

### DATA-1 — Tokens are encrypted at rest

Google access and refresh tokens are encrypted with Fernet before they touch the database.
Key comes from `TOKEN_ENCRYPTION_KEY`. Plaintext tokens must never appear in logs,
error messages, or `__repr__`.

### DATA-2 — Never log message content or PII

Log `message_sid`, `user_id`, type, language, latency, outcome. Never body text, transcripts,
email content, or coordinates. There is a redaction filter in `app/core/logging.py`; use it.

### LANG-1 — Language is per-message, not per-user

Detect the language of each inbound message and reply in that language. A user's stored
`preferred_language` is only the fallback when detection is ambiguous. Mid-conversation
switching must work, including within one sentence.

---

## Conventions

- Python 3.12, FastAPI, SQLAlchemy 2.0 (async), Alembic for migrations, Pydantic v2 settings.
- `ruff` + `black`. Type hints on every public function.
- No secrets in code. Everything through `app/core/config.py` (`pydantic-settings`).
- All timestamps stored as `TIMESTAMPTZ` in UTC. Display in the user's timezone (`Asia/Singapore`).
- Every AI call goes through `app/ai/gemini_client.py` — it owns retries, 429 backoff,
  timeouts, and token accounting. No direct SDK calls elsewhere.
- Every external call has a timeout and a fallback. A dead dependency degrades the reply;
  it does not 500 the webhook.

## What not to build

Do not build these unless explicitly asked. They are out of scope and will cost the demo:

Celery / Redis / RabbitMQ · Kubernetes · a caregiver web dashboard · a companion mobile app ·
user-facing auth beyond the OAuth link · multi-tenant isolation · read replicas · a vector
database · self-hosted Whisper / PaddleOCR / Piper · WebSockets · i18n framework for anything
beyond the two supported languages.

## Demo data rule

Seed data only. Synthetic patient, synthetic Gmail account, synthetic medications. Free-tier
Gemini may retain prompts for training — no real person's health information goes through this.
