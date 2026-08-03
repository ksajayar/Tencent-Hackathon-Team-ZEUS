# 13 — Development Roadmap

Covers brief section 25. Ordered by dependency and by demo value, not by the brief's ordering.

Each milestone has a **Done when** you can actually test. Give Claude Code one milestone at a
time; do not hand it the whole plan and ask for an app.

---

## M0 — Infrastructure

Repo, Dockerfile, FastAPI skeleton, config, structlog with the redaction processor, Postgres
on Railway, Alembic initialised, `/health` responding on the public Railway URL.

**Done when:** `curl https://<app>.up.railway.app/health` returns 200 from a deployed container.

## M1 — WhatsApp round trip

Twilio WhatsApp channel configured. `POST /webhooks/twilio` with signature validation. `users`,
`conversations`, `messages` tables. Inbound normalizer for text. Outbound gateway with the
window check and throttle. Echo bot.

**Done when:** you message the Twilio number from your phone and get a reply, and the message is in
Postgres. Validate the signature works **on Railway**, not just locally — the proxy scheme
issue in §10 will bite here or nowhere.

## M2 — Conversation with Gemini

`gemini_client` with retries, backoff, timeouts, usage logging. Language detection. Persona
prompts in both languages. Context assembly. Simplifier. Full text pipeline.

**Done when:** it answers a question in English, the same question in Mandarin, and a
code-switched one — each in the right language, in short sentences.

## M3 — Google OAuth

Consent screen configured with the demo account as a test user. `oauth_states`, `oauth_tokens`.
Full flow, Fernet encryption, refresh job, revoke.

**Done when:** "connect google" over WhatsApp completes the flow and the token round-trips
through encryption. Verify no plaintext token appears in any log line.

## M4 — Calendar

Sync job, `calendar_events`, `singleEvents` expansion, all-day handling, timezone rendering,
conflict scan.

**Done when:** "what's my next appointment?" returns a real event from the demo Google account,
phrased in day-relative language, in both languages.

## M5 — Reminders

`reminders`, `outbound_queue`, `medications` (seeded, verified). Scheduler. RRULE advancement.
Window-aware sending with the template fallback. `medication_guard`. Ack handling.

**Done when:** a medication reminder fires on its own schedule and arrives — and you have
tested it both inside and outside the 24-hour window.

## M6 — Voice

ffmpeg in the image. Audio preprocessing. Gemini transcription with language detection.
edge-tts. MP3→OGG/Opus transcode. Signed media serving. Text-and-audio as two sends.

**Done when:** a Mandarin voice note gets a Mandarin voice-note reply that plays as a **voice
note**, not a file attachment. This is the highest-risk milestone — budget for it.

## M7 — Gmail

Sync job, `email_cache`, deterministic pre-filter, batched classification, both-language
summaries pre-computed.

**Done when:** "any important emails?" returns three prioritised one-line summaries instantly,
with a hospital email ranked top.

---

### ▲ DEMO DAY CUT LINE ▲

**M0–M7 is a complete, compelling demo.** Everything below is upside. If you are behind
schedule, stop here and spend the remaining time on rehearsal and polish instead — a
well-rehearsed seven-feature demo beats a shaky eleven-feature one every time.

---

## M8 — Vision & documents

Image pipeline with sub-routing. `medication_candidates`. PDF pipeline. `documents`.

**Done when:** a photo of a pill bottle saves a candidate without confirming a dose, and a PDF
letter comes back as a three-sentence summary.

## M9 — Location, SOS & contacts

Location pin handling, `safe_zones`, haversine, pull-based check-in. SOS regex path.
vCard parsing.

**Done when:** "help" alerts a second phone within seconds, and a location pin near a seeded
shop zone triggers the shopping reminder.

## M10 — Hardening & rehearsal

Boot recovery pass. Degraded modes on every pipeline. Quiet hours. `/health/deps`.
Priority tests from §11. Two full rehearsals of the §11.7 script on the deployed environment.

**Done when:** you have run the whole demo script twice, end to end, without touching the code.

---

## Sequencing notes

- **M6 is the risk.** Three things must line up: ffmpeg in the container, edge-tts still
  working, and OGG/Opus rendering correctly. Spike it early — even a throwaway script that
  synthesises "hello" and sends it — so you find the format problem in week one rather than
  the night before.
- **M3 before M4 and M7.** Both depend on tokens.
- **M5 before M6.** Reminders are more demo-valuable than voice, and voice can be layered on.
- **M1 must be verified on Railway, not localhost.** The signature and public-URL issues only
  appear behind the proxy.
- **Seed data early.** From M4, keep the demo Google account populated with a calendar event
  tomorrow and a hospital email from today. An empty account makes every feature look broken.

## Demo-morning checklist

Copy this into your notes:

- [ ] `GET /health/deps` all green
- [ ] TTS smoke test returns a playable voice note
- [ ] Google token valid (unverified apps in Testing expire refresh tokens after 7 days)
- [ ] Gemini daily quota not exhausted by morning rehearsal
- [ ] Railway credit balance checked
- [ ] Seed data: a calendar event in the next 24h, a hospital email today, a medication due
      during the demo window
- [ ] Deploy freeze — no pushes for the last hour
- [ ] Judge messages the bot first, so the 24-hour window is open and every reply is free-form
- [ ] Phone charged, volume up, screen mirroring tested

The last one is not a joke. A voice-note demo with the phone on silent is a demo of nothing.
