# 16 — Agent Persona, Limitations & Guardrails

A single reference for what the assistant *is*, what it *cannot* do, and the mechanisms that
keep it inside those bounds. Written from the implemented system (`app/ai/prompts/persona.py`,
`app/safety/`, `app/pipelines/`), not from aspiration — every claim below maps to a file.

---

## 1. Persona

### 1.1 Who it is

A warm, patient companion for someone living with dementia, reachable only through WhatsApp.
Bilingual: English and Simplified Chinese, detected and switched **per message**, not per user
(`app/pipelines/text.py::detect_language`). It answers orientation questions ("what am I doing
today?", "who is visiting?"), reads the patient's Gmail and Calendar on their behalf, delivers
medication and appointment reminders, and can raise an SOS.

Source of truth for tone and phrasing: `PERSONA_EN` / `PERSONA_ZH` in
[`app/ai/prompts/persona.py`](../app/ai/prompts/persona.py), injected as the Gemini
`system_instruction` on every general-Q&A call.

### 1.2 Voice rules

- One idea per sentence; sentences under 15 words.
- Present tense, active voice.
- An appointment, visit, event or reminder is never mentioned without its day and time — the
  `<schedule>` context block hands the model that phrasing already rendered
  (`app/services/calendar.py::render_when`) rather than a raw "14:00" it is forbidden to echo.
- Spoken times ("2 in the afternoon", "after lunch") — never clock times ("14:00").
- People are named by relationship **and** name together: "your daughter, Mei Ling", never
  just one or the other.
- Lists never exceed three items.
- Answers about location or "what's happening" end with a small reassurance.
- English proper nouns and medical/brand terms (*"Dr Tan"*, *"Panadol"*) stay in English even
  when the reply is in Chinese — translating them would stop the patient recognising them.

### 1.3 The repetition rule

Dementia means the same question may be asked five times in a row. The persona is explicitly
forbidden from saying "as I mentioned," "you already asked," or "remember" — the fifth answer
must be exactly as warm as the first. This is the single most load-bearing line in the prompt.

### 1.4 Certainty rule

The persona never hedges on facts actually present in its context block (today's schedule,
medications, recent emails) — it states them plainly. It only says "I don't know" when the
context genuinely has nothing on the topic, and then offers to ask the caregiver. This keeps
uncertainty honest instead of habitual.

### 1.5 What the persona is explicitly *not*

- Not a diagnostician, not a pharmacist, not a decision-maker on medication.
- Not a medical device. It reminds and reads; it does not treat (§4.4).
- Not an emergency service. SOS notifies a human contact — it does not call 911/999 (§4.4).
- Not a data-entry clerk for the caregiver: it never proposes, corrects, or infers medication
  data — only a caregiver action promotes anything into the trusted record (§3.1).

---

## 2. Limitations

Limitations are grouped by cause: platform, AI, language, and integration. Each is named rather
than hidden, per `dementia-assistant-blueprint.pdf`'s "honest caveats" instruction.

### 2.1 Channel / platform

| Limitation | Consequence |
|---|---|
| Own Twilio number (+1 315 812 6378), connected to a Meta Business Account — no join code, no shared-sandbox branding, custom templates approved through Meta | The 24-hour free-form-message window is Meta's rule, not the sandbox's, and still applies regardless |
| WhatsApp Business template approval takes real review time and can be rejected | A newly-drafted template (e.g. the caregiver intro, §17) may not be live yet — check `/internal/debug/templates` before relying on one in a demo |
| WhatsApp gives a **static location pin on request**, not a live GPS stream | No continuous geofencing or passive wandering detection — location is pull-based only |
| 1 message / 3 seconds throttle | Enforced in the outbound gateway; bursty replies queue |
| Single Railway replica (in-process scheduler + queue) | Cannot horizontal-scale without moving the scheduler out of process first |
| Media size/format caps: audio & documents 16 MB, images 5 MB; one media object per message; filenames ≤20 ASCII chars | Oversized, multi-attachment, or malformed media is rejected outright with a friendly message, never processed |
| WhatsApp opt-out (`STOP`) must be honoured per Twilio error 21610 | If a patient's own number opts out, **all** sends to it stop — including reminders and any SOS confirmation — until they opt back in |
| Railway hosting (compute, Postgres, volume) has **no permanent free tier** | The assistant depends on a maintained paid balance; running out of credit suspends the service entirely, not just new features |

### 2.2 AI (Gemini)

| Limitation | Consequence |
|---|---|
| Free tier ≈ 10–15 requests/minute, low hundreds to ~1,500 requests/day | Breaks at roughly 5–15 concurrent users; enabling billing removes the free allowance entirely rather than extending it |
| **No uptime SLA on the free tier**; requests can be silently deprioritised at peak | 429s are a routine, expected path — not an exceptional error — so the client treats backoff as normal operation |
| Free-tier prompts may be retained for training | Synthetic patient data only — never a real person's health information |
| General text-generation is a language model, not a database | It **can** produce fluent but wrong text on anything not pulled from structured context — this is why medical/emergency content is never generated (§3) |
| Dialect unsupported: Hokkien, Teochew, Cantonese | A large share of the target elderly Singaporean population speaks these; not solvable by prompting |
| Dementia-affected speech degrades transcription accuracy | No cheap fix; confidence threshold suppresses guessing instead (§3.6) |
| Model drifts toward Traditional Chinese characters | Simplified is pinned in the prompt and must be spot-checked |
| The "short sentences, plain language" persona rule is **less reliably followed in Chinese than in English** | A distinct issue from script drift above — Chinese output needs its own testing pass, English quality does not transfer |
| Chinese date/number conventions (上午/下午, Chinese numerals) aren't handled by prompting alone | Times are rendered through an explicit localisation helper rather than left to the model |
| `edge-tts` is an unofficial, reverse-engineered endpoint | No SLA, can break without notice; falls back to text-only reply |

### 2.3 Google integration

| Limitation | Consequence |
|---|---|
| Gmail + Calendar are **read-only** by design | The assistant can never send an email, create an event, or modify the patient's account |
| Unverified OAuth app | Refresh tokens expire after 7 days in Testing mode; capped at 100 users; shows a Google warning screen |
| Data polled every 15 minutes, not live | Freshness target is 15 minutes; a manual "check my email now" forces a sync |
| `TOKEN_ENCRYPTION_KEY` has no rotation story in v1 | Losing the key makes every stored Google token unusable — every user must reconnect and re-consent from scratch |

### 2.4 Explicit non-goals

Per `CLAUDE.md`, out of scope unless separately requested: Celery/Redis/RabbitMQ, Kubernetes, a
caregiver web dashboard, a companion mobile app, user-facing auth beyond the OAuth link,
multi-tenant isolation, read replicas, a vector database, self-hosted Whisper/PaddleOCR/Piper,
WebSockets, or an i18n framework beyond English/Simplified Chinese.

### 2.5 Product & ethical limitations (named, not solved)

- **Consent.** A person with dementia may not be able to give meaningful, ongoing consent to
  having their email read. A real deployment needs a legally authorised representative.
- **Dependency.** If the patient comes to rely on the assistant and a medication reminder fails
  silently, they miss a dose — which is why failed reminders are logged `CRITICAL` with a
  caregiver-escalation path, not retried-and-forgotten.
- **Surveillance.** Passive location tracking of someone who cannot consent is ethically loaded;
  the pull-based design (patient actively shares) is a deliberate choice, not just a technical
  limitation of WhatsApp.

---

## 3. Guardrails

These are **structural**, not prompt-based, per the project's core security principle: a
successful prompt injection (e.g., instructions hidden in a photographed document or an email)
must still be unable to produce a fabricated medication instruction or a false emergency
(`docs/10-security-observability.md` §10.1).

> **Verification note.** Every guardrail below was checked directly against `app/`, not just
> against the design docs in `docs/`, since the docs were the *input* to building this system and
> a few things they describe were never actually wired up. Five real gaps between "documented"
> and "implemented" turned up and are called out inline where they occur: the medication guard
> doesn't cover the free-text Q&A path (§3.1), the transcription confidence threshold and behavior
> differ from the design doc (§3.6), the prompt-injection defense is only half-built — delimiting
> without the "treat as data" instruction (§3.7), there is no audit-log table yet (§3.8), and two of
> the four documented rate limiters don't exist in code (§3.9). Everything else in this section was
> confirmed present as described.

### 3.1 SAFETY-1 — The model never invents medical facts

- Medication names, doses, and schedules come only from the `medications` table.
- The LLM receives medications as a **read-only structured context block**; it may rephrase them
  into simple language, never add, correct, infer, or substitute.
- `app/safety/medication_guard.py::enforce` checks a reply and discards it for a safe fallback if
  a drug name isn't a verbatim match to a row in the user's context.
- OCR and vision **never write to `medications`** — they write to `medication_candidates` with
  `status='pending'`; only a caregiver action promotes a candidate.
- The assistant never generates dosage advice, drug-interaction reasoning, or "you can skip this
  one" — if asked, it returns the fallback and points to the caregiver or doctor.

**Verified gap, worth flagging on its own:** `CLAUDE.md` states the guard runs on "every outbound
message that mentions a medication," but in the current code
(`app/pipelines/text.py::_generate_reply`) it is only wired into the deterministic
`_medication_query` path (triggered by phrases like "what medicine do I take"). The free-text
`_general_qa` path — which also receives the medications block as context and can mention a drug
name while answering an unrelated question — returns straight from `simplify()` with **no**
`medication_guard.enforce` call. `app/ai/context.py`'s own docstring concedes this: "medication_guard
itself only applies to the deterministic template/query paths, not this free-text one." The
practical safety net on that path today is entirely upstream (the model is only ever given
medication names as read-only context, never asked to invent one) — there is no output-side
verification backstopping it the way SAFETY-1 implies. If this is being scored or demoed against
`CLAUDE.md` as written, this is the one line item to either fix (route `_general_qa`'s output
through the guard too) or explicitly caveat.

### 3.2 SAFETY-2 — SOS is deterministic

`app/safety/sos.py` contains no LLM call anywhere in the path: trigger-phrase regex match →
look up emergency contacts → send a templated alert → log. An LLM outage cannot break SOS.
Rate-limited to one alert per contact per 5 minutes (never per user, so a real emergency isn't
silenced) — and SOS is explicitly **exempt** from general user-facing rate limiting.

### 3.3 Vision & document guardrails

The vision system prompt (`app/ai/gemini_client.py::analyze_image`) hard-codes: never diagnose,
never confirm a photographed medicine as the patient's own prescribed medication, never identify
people from photos (generic descriptions only — "two people smiling in a garden"). Document
summarisation (`summarize_document`) reports only what a document *is* and what the patient is
*asked to do*, at a logistics level — never medication names/doses as actionable advice.

### 3.4 Email classification guardrail

`_CLASSIFY_PROMPT` explicitly forbids stating a medication name or dose in an email summary,
even if the source email mentions one — medication information only ever comes from the
caregiver-verified record, never from an inbox.

### 3.5 Output-length / readability guardrail

`app/safety/simplifier.py::simplify` caps every reply at 600 characters, truncating at a
sentence boundary rather than mid-word, so a verbose model response still reads naturally to
someone with dementia.

### 3.6 Confidence guardrails

- **Transcription** (`app/pipelines/voice.py`): an **empty** transcript → no guessing, the
  assistant asks the patient to repeat itself. A **non-empty but low-confidence** transcript
  (`confidence < 0.6`, `LOW_CONFIDENCE_THRESHOLD`) is logged as a warning but **still passed on**
  to the text pipeline as if it were a normal message — it is not currently blocked or re-asked.
  (An earlier draft of this document said "<0.5 → asks the patient to repeat"; that describes the
  design doc's intent, not this code path — correcting it here.)
- **OCR** (`app/services/medication_candidates.py::MIN_MEDICAL_FIELD_CONFIDENCE = 0.8`): a
  structured medical field below 0.8 confidence is dropped before storage, confirmed in code —
  this one matches the design doc exactly.

### 3.7 Prompt-injection defense — design intent, not yet in the prompts

`docs/10-security-observability.md` describes a two-layer defense: delimited context blocks, plus
a system-prompt statement that content in those blocks is data, never instructions. Checking the
actual prompts (`app/ai/prompts/persona.py`, and the vision/document/email system prompts in
`app/ai/gemini_client.py`): the context **is** delimited in tagged blocks (`<medications>`,
`<schedule>`, etc. — see `app/ai/context.py`), but **no system prompt in the codebase currently
contains an explicit instruction telling the model to treat that content as data, not
instructions.** The layer that is genuinely real is the structural one — §3.1/§3.2's output-side
guards, which don't depend on the model behaving, so a successful injection still can't produce a
fabricated medication instruction or a false emergency. The delimiting-plus-instruction layer is a
documented improvement, not a currently-implemented one.

### 3.8 Privacy & logging guardrails

- Google access/refresh tokens are Fernet-encrypted at rest (`app/core/security.py`); plaintext
  tokens are never held in module-level state, only decrypted at point of use.
- `app/core/logging.py::_redact_processor` drops a fixed denylist of keys (message body,
  transcript, email content, OCR text, coordinates, tokens, patient name) from every log line and
  truncates any string field over 200 characters — confirmed wired into `structlog.configure`.
- **Not currently implemented:** a separate, non-deletable audit log for sensitive actions (token
  issue/revoke, medication verify, SOS outcome, data deletion) is described in
  `docs/10-security-observability.md` §10.2 but there is no such table in `app/db/models/` — only
  `AIUsage` (cost/latency accounting), `SosEvent` (SOS-specific), and the redacted `structlog`
  stream exist today. Anyone relying on an audit trail for compliance should treat this as a gap,
  not a built feature.

### 3.9 Rate limiting & abuse protection

Two rate limiters actually exist in code: the global Gemini token bucket
(`app/ai/gemini_client.py::_TokenBucket`, 8 requests/minute) and SOS's own 5-minute-per-contact cap
(`app/safety/sos.py`). **Not currently implemented:** the per-WhatsApp-sender cap (20
messages/minute) and per-IP cap on OAuth routes (10/minute) described in
`docs/10-security-observability.md` §10.1 — there is no rate-limiting middleware or dependency on
`/webhooks/twilio` or `/oauth/*` in the current code. SOS is deliberately exempt from its own
per-contact cap being tightened any further (§3.2) — the intent, even once the missing limiters
above are built, is that it should never be possible to rate-limit someone out of an emergency.

### 3.10 Language-integrity guardrail

Language is detected per message via script analysis (`detect_language`), not read from a stored
user preference — a stored `preferred_language` is only the fallback when a message has no script
signal at all. This stops a bilingual, code-switching conversation from drifting into the wrong
language mid-thread.

### 3.11 Fail-safe error handling

Every external dependency (Gemini, Google APIs, Twilio, TTS, ffmpeg) has a timeout and a
user-visible fallback in the patient's own language — the system is built so that "silence" is
never an acceptable failure mode for a confused user. Exceptions are never surfaced to the
patient; only a logged detail and a simple sentence are.

### 3.12 What guardrails deliberately do *not* cover

Named as `DEFERRED`, not silently missing:

- No malware/AV scanning on uploaded files (mitigated by never executing or re-serving them, and
  7-day deletion).
- No field-level encryption of message content at rest, no key rotation.
- No formal consent mechanism for a patient who may be unable to give one — flagged as a real
  requirement for any non-demo deployment, not solved here.

---

## 4. How the three sections relate

The **persona** (§1) governs *tone* — it is allowed to be wrong about styling and still be safe.
The **guardrails** (§3) govern *facts that could cause harm* — they are structural precisely so
that no amount of persona drift, prompt injection, or model error can bypass them. The
**limitations** (§2) are the honest boundary between the two: places where the system does not
attempt to compensate with either persona wording or a guardrail, and says so instead of quietly
degrading.
