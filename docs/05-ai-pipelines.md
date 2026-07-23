# 05 — AI Processing Pipelines

Covers brief section 9. Six pipelines, one shape:
`Input → Preprocess → AI → Database → Output`.

All AI calls go through `app/ai/gemini_client.py`. Nothing else imports the SDK.

## 5.0 The Gemini client

Single choke point. Responsibilities:

- Model selection: `flash-lite` for classification and routing, `flash` for generation and
  media understanding. Never hardcode a model string outside this module.
- Timeouts: 15s text, 45s media. Hard cap.
- Retry: 3 attempts, exponential backoff with jitter, on 429 and 5xx only. Never retry a 400.
- Rate limiting: a local token bucket sized under the free-tier RPM so you fail slow rather
  than getting throttled mid-demo.
- Usage accounting: log tokens per call to `ai_usage` so you can see what's eating quota.
- Structured output: a `generate_json(schema)` helper that instructs the model to return raw
  JSON only, strips ``` fences defensively, and validates against a Pydantic model. Retry once
  on parse failure with the error fed back in; then fall back.
- **Degraded mode:** if Gemini is unavailable, every pipeline has a non-AI fallback (listed
  per pipeline below). The system never returns nothing.

## 5.1 Text pipeline

```
Text → language detect → intent classify → context assembly → Gemini → guards → DB → reply
```

**Preprocess.** Normalise whitespace and full-width punctuation. Detect language from the
script: any CJK codepoints → `zh-Hans`; otherwise `en`. Mixed → whichever script carries more
characters, recorded as `mixed` so the reply can code-switch too. This is a 20-line function
and beats a language-detection library on short messages, which is all you get on WhatsApp.

**Intent classify.** One `flash-lite` call returning an enum, plus a fast regex pre-pass for
the handful of intents that must never depend on a model:

| Intent | Route |
|---|---|
| `sos` | **Regex only.** Deterministic handler, no LLM. |
| `medication_query` | DB lookup → LLM phrases the row |
| `next_appointment`, `today_agenda`, `who_visiting` | DB lookup → LLM phrases |
| `email_summary` | `email_cache` read → LLM phrases |
| `connect_google` | OAuth link generator |
| `repeat_last` | Re-send the last outbound message verbatim |
| `set_language`, `set_voice_mode` | Preference write |
| `general_qa` | Full conversational path |

**Context assembly.** Build a structured block, not prose:

```
<patient>name, preferred language, timezone</patient>
<today>date, day of week (local)</today>
<medications>caregiver-verified rows only — name, time, dose, simple instruction</medications>
<schedule>today's + tomorrow's calendar events</schedule>
<contacts>name → relationship</contacts>
<recent_emails>pre-computed summaries, priority >= 4</recent_emails>
<conversation>last 6 turns, both languages as sent</conversation>
```

Cap at ~2000 tokens. Truncate the conversation window first, never the medications block.

**Generate.** System prompt enforces the dementia-friendly register (§07 §7.6). Reply language
= detected language of *this* message.

**Guards.** `medication_guard` → `simplifier` → length cap. See `CLAUDE.md`.

**Persist.** Outbound `messages` row with detected language, model, latency, token count.

**Degraded mode:** if Gemini is down, intent regex still handles SOS, medication, appointment,
and agenda queries from the DB with templated phrasing. Only `general_qa` fails, and it fails
with "I'm having trouble thinking right now — I can still tell you about your medicines and
appointments."

## 5.2 Voice pipeline

```
Voice note → download → ffmpeg normalise → Gemini audio (transcribe + detect) → text pipeline → reply (+ TTS)
```

**Preprocess.** Twilio delivers OGG/Opus. Normalise before sending to the model:

```
ffmpeg -i in.ogg -ar 16000 -ac 1 -af "highpass=f=80,lowpass=f=8000,afftdn=nf=-25,loudnorm" out.wav
```

Mono, 16 kHz, band-passed to the speech range, light denoise, loudness-normalised. This matters
more than usual here — elderly speech is quieter, slower, and often recorded in a noisy room.

Reject >60s with a friendly "that was a bit long, could you say it again more briefly?" —
long audio burns quota and dementia users ramble.

**AI.** One call: audio part + a prompt asking for the transcript *and* the detected language
as JSON. Gemini transcribes and identifies language in the same pass — no separate detection
step, and it keeps English words embedded in Mandarin speech intact rather than forcing one
language. Store the transcript in `transcripts` linked to the media row.

**Then:** the transcript enters the text pipeline unchanged. Voice is an input format, not a
separate feature — one code path for both.

**Output.** Reply as text, and as a voice note if the user's `reply_mode` is `audio` or `both`.
Two separate Twilio sends, 3s apart (`CLAUDE.md` CHANNEL-2).

**Degraded mode:** "I couldn't hear that clearly — could you type it instead?"

## 5.3 Image pipeline

```
Image → download → validate → resize → Gemini vision → sub-route → DB → reply
```

**Preprocess.** Verify it is really an image (magic bytes, not the declared MIME). Strip EXIF —
it carries GPS. Auto-rotate from the orientation tag before stripping. Downscale so the long
edge is ≤1568px; larger costs tokens and adds nothing.

**AI.** One call classifies and extracts together:

```json
{"kind": "pill_bottle|prescription|document|scene|person|other",
 "text_found": "verbatim OCR, original script, no translation",
 "description_en": "...", "description_zh": "...",
 "confidence": 0.0-1.0}
```

**Sub-route by `kind`:**

| Kind | Behaviour |
|---|---|
| `pill_bottle`, `prescription` | Write to **`medication_candidates`**, status `pending`. Reply: "I can see this is [name]. I've saved it for your caregiver to check." **Never** write to `medications`, never confirm a dose. |
| `document` | Same path as the PDF pipeline's summarise step. |
| `scene` | Describe in simple language. Answers "where am I?" / "what is this?". |
| `person` | Describe generically. **Do not attempt identification.** |
| `other` | Generic description. |

**Degraded mode:** "I can see you sent a picture but I can't look at it right now."

## 5.4 PDF pipeline

```
PDF → download → size/page check → text probe → Gemini document → summarise → DB → reply
```

**Preprocess.** Reject >16 MB (Twilio's limit) or >30 pages. Probe with `pypdf`: if extractable
text is under ~100 characters, the PDF is scanned — which is fine, because Gemini reads the
pages as images either way. The probe exists so you can log which path was taken and set
expectations in the reply.

**AI.** Send the PDF bytes directly as a document part with a summarisation prompt tuned for
medical documents: what it is, who it's from, key dates, what the patient is being asked to do.
Explicitly instruct: **do not extract medication instructions as actionable advice**; surface
them as "this document mentions X — please check with your caregiver."

**Persist.** `documents` row with extracted text, both-language summaries, and doc kind.

**Output.** Three to five short sentences in the detected language. Offer a voice reading —
for a long medical letter, hearing it is much better than reading it.

**Degraded mode:** extract text with `pypdf` and return the first paragraph unsummarised, with
a note that a full summary isn't available.

## 5.5 Email pipeline

```
Gmail poll → dedupe → deterministic pre-filter → Gemini batch classify → email_cache → served on demand
```

Runs on a schedule, not on a message. Full detail in §04. The pipeline shape:

- **Preprocess:** rules-based promotion of known medical and family senders, before the model.
- **AI:** one batched call for up to 25 messages, structured JSON out.
- **DB:** `email_cache` with both-language summaries pre-computed.
- **Output:** none at sync time. Served instantly when asked. A priority-5 medical email may
  optionally trigger a proactive nudge, subject to the window rules in §03.

**Degraded mode:** rules-only classification. Sender-domain matching alone still surfaces
hospital email correctly, just without summaries.

## 5.6 Calendar pipeline

```
Calendar poll → expand recurrences → diff vs cache → conflict scan → reminder rows → served on demand
```

Almost entirely non-AI, deliberately. Times and dates must be exact, and an LLM is the wrong
tool for arithmetic on timestamps.

- **Preprocess:** `singleEvents=True` expansion, UTC normalisation, all-day handling.
- **Logic:** hash-diff against cache to detect changes; overlap scan for conflicts; generate
  `reminders` at T-24h and T-2h.
- **AI:** used only to phrase the final message in simple language, and to tidy a cryptic
  event title ("Dr T f/u appt" → "a follow-up visit with Dr Tan").
- **DB:** `calendar_events`, `reminders`.

**Degraded mode:** fully functional without AI, with slightly stiffer wording.

---

## 5.7 Cross-cutting rules

**One AI call per message.** If a pipeline is making two Gemini calls for one inbound message,
it is probably wrong. The exceptions are intent classification (cheap `flash-lite`) and a
single guard-triggered regeneration.

**Free-tier budget.** At roughly 10–15 RPM you cannot afford chatty pipelines. Pre-computing
email and calendar summaries at sync time — rather than at question time — is what keeps the
per-message cost at one call and the response instant.

**Idempotency.** Every pipeline is keyed on `MessageSid`. A Twilio retry must not produce a
second reply. Check for an existing outbound row before generating.

**Everything is logged, nothing sensitive is logged.** Pipeline name, duration, model, tokens,
outcome, error class. Not content.
