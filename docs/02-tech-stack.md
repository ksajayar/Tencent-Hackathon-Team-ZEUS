# 02 — Technology Stack

Covers brief section 3. Every entry: purpose, why chosen, cost status, alternatives, limits.

Cost key: **FREE** = free and unmetered · **FREE TIER** = free within a quota · **PAID** = costs money.

---

## Runtime & framework

### Python 3.12
- **Purpose:** application language.
- **Why:** your stated preference; also where the Google, Twilio, and Gemini SDKs are best supported.
- **Cost:** FREE (PSF licence).
- **Alternatives:** Node.js (better native Twilio examples), Go (faster cold start).
- **Limits:** GIL-bound. Irrelevant here — every workload is I/O-bound waiting on APIs.

### FastAPI + Uvicorn
- **Purpose:** webhook endpoint, OAuth callback routes, internal REST.
- **Why:** async by default, which matters because a single message can fan out to Gemini,
  Google, and Twilio concurrently. Pydantic validation on the OAuth callback for free.
- **Cost:** FREE (MIT).
- **Alternatives:** Flask (sync, would need gevent), Django (far too much for one webhook).
- **Limits:** Twilio POSTs `application/x-www-form-urlencoded`, **not JSON**. You must read it
  with `await request.form()` or `Form(...)` dependencies. A Pydantic JSON body model will
  silently 422 every message. This trips up most first implementations.

### SQLAlchemy 2.0 (async) + asyncpg
- **Purpose:** ORM and connection pooling.
- **Why:** stated preference; 2.0's typed `select()` API plays well with Pydantic v2.
- **Cost:** FREE (MIT).
- **Alternatives:** SQLModel (thinner but less mature), raw asyncpg (faster, more code).
- **Limits:** async sessions are not thread-safe — one session per task, never shared with
  the scheduler's jobs.

### Alembic
- **Purpose:** schema migrations.
- **Why:** you will change the schema four times during a hackathon. Hand-written DDL will
  rot by Milestone 4.
- **Cost:** FREE (MIT).
- **Limits:** autogenerate misses index and constraint changes; review every migration.

### APScheduler (AsyncIOScheduler)
- **Purpose:** reminder firing, Gmail/Calendar polling, token refresh, cleanup.
- **Why:** runs inside the FastAPI process. No broker, no second container, no extra Railway cost.
- **Cost:** FREE (MPL 2.0).
- **Alternatives:** Celery + Redis (correct at scale, ~$5–10/mo more on Railway and half a day
  of setup), Railway Cron (separate container per job, cold start per run).
- **Limits:** **Single replica only.** Two instances = duplicate reminders. Use
  `SQLAlchemyJobStore` on your Postgres so jobs survive redeploys, and set
  `misfire_grace_time` so a deploy during a reminder window doesn't drop it.

---

## Data

### PostgreSQL 16 (Railway managed)
- **Purpose:** everything — users, messages, tokens, reminders, caches, the outbound queue.
- **Why:** stated preference. Also lets you skip Redis entirely: `SELECT ... FOR UPDATE SKIP
  LOCKED` gives you a perfectly adequate job queue at this scale.
- **Cost:** PAID, inside Railway's usage credit. Realistically $3–8/mo for a demo-sized instance.
- **Alternatives:** Supabase (has a real free tier, but then your DB is off-platform and you
  lose Railway's private networking), Neon (free tier, autosuspends — bad for a live demo).
- **Limits:** Railway has no permanent free tier; see §14 for the actual numbers.

### Railway Volume
- **Purpose:** downloaded media, generated TTS audio cache.
- **Why:** media must persist between the fetch and the outbound send.
- **Cost:** PAID, metered by GB. 1 GB is plenty.
- **Alternatives:** Cloudflare R2 (10 GB free, S3-compatible — the right answer for production),
  storing bytes in Postgres (works at demo scale, bloats the DB).
- **Limits:** a volume pins you to one replica and one region. Already true because of the scheduler.

---

## Messaging channel

### Twilio WhatsApp (Meta Business Account)
- **Purpose:** the entire user interface.
- **Why:** started on the Twilio sandbox for speed (no Meta Business verification, no display
  name review, working in about ten minutes), then moved to a purchased Twilio number connected
  to a Meta Business Account once that verification was done - keeping Twilio's API rather than
  building directly against Meta's Cloud API.
- **Cost:** own-number billing (no more free sandbox trial credit); messages are billed at
  standard WhatsApp rates.
- **Limits — read all of these:**

  | Limit | Impact |
  |---|---|
  | **Free-form only inside the 24h window** opened by the user's last inbound message. | This is Meta's platform rule, not a sandbox one - it applies here exactly as it did before. The outbound gateway must check the window before every send. |
  | Custom templates require Meta's review before they're usable. | Not instant, and can be rejected on wording/variable-placement grounds. Submit well before a demo. |
  | **1 message / 3 seconds.** | Self-imposed in the outbound gateway, not enforced by Twilio here - kept anyway. |
  | Media messages **cannot carry a text caption** — `Body` is silently dropped. | Text and audio are always two separate sends. |
  | One media object per free-form message. | No batching. |
  | Audio: 16 MB. Images: 5 MB. Filenames ≤20 ASCII chars. | Validate before send or get an opaque 400. |

  No more join code, no 3-day join expiry, no shared-number branding, and custom templates are
  submittable - all four were fixed limitations of the sandbox this project started on. §03
  covers the templates now actually in use.

---

## AI

### Google Gemini API (Flash tier) — via `google-genai` SDK
- **Purpose:** transcription, OCR, vision, PDF reading, summarisation, prioritisation,
  conversation, intent classification. One model, six jobs.
- **Why:** it is the only provider where a free tier covers *all* of those modalities in one
  API. It handles Simplified Chinese and English–Chinese code-switching well, which is the
  single hardest requirement in your brief. Whisper picks one language per utterance and
  mangles mixed speech; Gemini does not.
- **Cost:** FREE TIER. As of July 2026: Flash and Flash-Lite only, roughly 10–15 requests per
  minute and a few hundred to ~1,500 per day depending on model. **Pro was removed from the
  free tier in April 2026.** Verify live before demo day — these have been cut twice.
- **Alternatives:**
  - **OpenAI** (`gpt-4o-mini` + `whisper-1` + `tts-1`): better Mandarin TTS, mature, but paid
    from the first token and needs three separate services.
  - **Groq** (free tier, Whisper large-v3, very fast): excellent STT latency, no vision, no
    PDF, and Whisper's code-switching weakness remains.
  - **Self-hosted** (faster-whisper + PaddleOCR + a local VLM): genuinely free, but PaddleOCR
    alone is >1 GB of dependencies, CPU inference on Railway is 10–30s per request, and
    Mandarin quality on a `small` Whisper model is poor. Not viable inside a hackathon.
- **Limits:**
  - **Free-tier prompts may be used to train Google's models.** Synthetic data only.
  - **Enabling billing removes the free allowance entirely** — it does not add to it. Do not
    switch on billing "just in case" the week of the demo.
  - 429s are routine. The client must implement exponential backoff with jitter.
  - No SLA; free-tier requests are deprioritised at peak.

### `edge-tts`
- **Purpose:** text-to-speech in both languages.
- **Why:** genuinely free, no key, no quota, and the neural voices are far better than
  anything else at $0. `zh-CN-XiaoxiaoNeural` is the best free Mandarin voice available.
  `en-SG-LunaNeural` exists, which is a nice touch for a Singapore demo.
- **Cost:** FREE.
- **Alternatives:** Google Cloud TTS (free tier ~1M WaveNet chars/month, official and stable,
  needs a billing account), `gTTS` (free, noticeably robotic), Piper (fully offline OSS,
  good English, weak Mandarin), ElevenLabs (best quality, paid).
- **Limits:** **unofficial.** It talks to an undocumented Microsoft Edge endpoint and has
  broken before without warning. Outputs MP3, so ffmpeg transcoding is mandatory. Build the
  TTS layer behind an interface with Google Cloud TTS as a drop-in fallback (§06).

### ffmpeg
- **Purpose:** MP3 → OGG/Opus for WhatsApp voice notes; audio normalisation before STT.
- **Why:** only OGG/Opus renders as a playable voice note. MP3 arrives as a file attachment,
  which defeats the entire point for a dementia user.
- **Cost:** FREE (LGPL/GPL).
- **Limits:** must be installed in the Docker image (`apt-get install ffmpeg`) — it is not in
  the Python base image. Adds ~80 MB.

---

## Google integration

### `google-auth-oauthlib` + `google-api-python-client`
- **Purpose:** OAuth 2.0 authorization-code flow, Gmail and Calendar reads.
- **Why:** official, handles refresh correctly.
- **Cost:** FREE (Apache 2.0). The APIs themselves are FREE TIER — Gmail allows a large daily
  quota measured in units; polling one mailbox every 15 minutes is negligible.
- **Alternatives:** raw HTTP with `httpx` (fewer dependencies, more code to get refresh right).
- **Limits:** `google-api-python-client` is **synchronous**. Wrap every call in
  `asyncio.to_thread()` or it blocks the event loop. This is the single most common mistake
  when adding Google APIs to FastAPI.
- **Scopes (read-only, minimum viable):**
  - `https://www.googleapis.com/auth/gmail.readonly`
  - `https://www.googleapis.com/auth/calendar.readonly`
  - `openid`, `email`, `profile`
- **Verification:** an unverified app with sensitive scopes is capped at **100 users** and
  shows an "unverified app" interstitial. Fine for a demo; add your judge's Google account as
  a **Test User** in the OAuth consent screen and the warning is manageable.

---

## Security & ops

### `cryptography` (Fernet)
- **Purpose:** encrypt Google access and refresh tokens at rest.
- **Why:** symmetric, authenticated, one line to use, hard to misuse.
- **Cost:** FREE (Apache 2.0 / BSD).
- **Alternatives:** `pgcrypto` in Postgres (keeps keys in the DB — worse), cloud KMS (overkill).
- **Limits:** key lives in `TOKEN_ENCRYPTION_KEY`. Lose it and every user must re-consent.
  No rotation story in v1; noted as deferred.

### `pydantic-settings`
- **Purpose:** typed environment configuration.
- **Why:** fails loudly at boot on a missing secret rather than at 2am mid-demo.
- **Cost:** FREE (MIT).

### `structlog`
- **Purpose:** structured JSON logging with a PII redaction processor.
- **Why:** Railway's log viewer greps JSON well, and you need a hard filter that keeps message
  bodies out of logs.
- **Cost:** FREE (MIT/Apache).

### Docker
- **Purpose:** reproducible build; needed anyway to get ffmpeg in.
- **Cost:** FREE.
- **Limits:** Railway's Nixpacks builder is faster but you need an explicit apt package, so
  use a Dockerfile.

### `pytest` + `pytest-asyncio` + `respx` + `testcontainers`
- **Purpose:** unit, integration, and webhook tests with mocked HTTP.
- **Cost:** FREE.
- **Limits:** `testcontainers` needs Docker in CI; if unavailable, fall back to a Postgres
  service container in GitHub Actions.

---

## Deliberately not used

| Not used | Why |
|---|---|
| Redis | Postgres `SKIP LOCKED` is enough at this scale, and Redis is another Railway service to pay for. |
| Celery | APScheduler covers it; Celery is a half-day of setup you don't have. |
| LangChain / LlamaIndex | Adds an abstraction layer over one model with six prompts. Direct SDK calls are shorter and debuggable. |
| A vector DB | No corpus. Context fits in a prompt. Revisit if the RAG knowledge base in §14 happens. |
| Whisper (self-hosted) | Gemini already transcribes, and handles code-switching better. |
| Tesseract / PaddleOCR | Gemini's Chinese OCR is better and is already a dependency. |
| A frontend framework | The only UI is WhatsApp plus one static OAuth success page. |
