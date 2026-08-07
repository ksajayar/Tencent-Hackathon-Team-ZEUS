# 12 — Railway Deployment & Folder Structure

Covers brief sections 16 and 18.

## 12.1 Railway services

One project, one environment (`production`), three pieces:

| Service | What | Config |
|---|---|---|
| `api` | Docker web service. FastAPI + worker + scheduler, one container. | **1 replica, hard requirement.** Health check `/health`. Public domain enabled. |
| `postgres` | Railway managed Postgres plugin | Injects `DATABASE_URL`. Use the **private** networking URL — it doesn't count toward egress. |
| volume | Attached to `api` at `/data` | 1 GB. Media and TTS cache. |

Do not add a second service for the worker or scheduler. It doubles the cost and, for the
scheduler, would double-fire every reminder.

## 12.2 Dockerfile

The reason for Docker rather than Nixpacks is one line: **ffmpeg**. Without it, no voice notes.

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg libmagic1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /data/media /data/tts

ENV PYTHONUNBUFFERED=1 PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD curl -fsS http://localhost:${PORT}/health || exit 1

CMD alembic upgrade head && \
    uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1
```

`--workers 1` matters as much as the single replica: multiple Uvicorn workers each start their
own APScheduler.

Running `alembic upgrade head` in `CMD` is fine at this scale and saves you a release step.
For production you'd split it into a release phase.

## 12.3 Environment variables

Set in Railway → Variables. Never in the repo.

```
# Channel
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_WHATSAPP_FROM=whatsapp:+13158126378

# AI
GEMINI_API_KEY=
GEMINI_MODEL_FAST=gemini-flash-lite
GEMINI_MODEL_MAIN=gemini-flash

# Google
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=https://<app>.up.railway.app/oauth/google/callback

# Data
DATABASE_URL=${{Postgres.DATABASE_URL}}
MEDIA_ROOT=/data/media
TTS_CACHE_ROOT=/data/tts

# Crypto
TOKEN_ENCRYPTION_KEY=        # Fernet.generate_key()
MEDIA_SIGNING_KEY=
ADMIN_TOKEN=

# App
PUBLIC_BASE_URL=https://<app>.up.railway.app
DEFAULT_TIMEZONE=Asia/Singapore
LOG_LEVEL=INFO
ENVIRONMENT=production
DEMO_MODE=false
```

`PUBLIC_BASE_URL` is load-bearing twice: Twilio signature validation and the media URLs Twilio
fetches. Get it wrong and every message 403s.

Reference the Postgres variable with Railway's `${{Postgres.DATABASE_URL}}` syntax rather than
pasting the value, so it survives a database re-provision.

### 12.3a DEMO_MODE — the judge-facing demo path

Set `DEMO_MODE=true` in Railway → Variables and redeploy (or restart the service) to turn it on;
set it back to `false` (or remove it) and redeploy to restore the normal caregiver-onboarding
flow untouched. It's the only flag this feature adds — every demo-only code path is gated on it.

What it does, end to end:

1. On startup with the flag on, the app seeds one reserved "template patient" (`app/services/demo.py`,
   idempotent — safe across every redeploy) with a synthetic bilingual profile: medications, a
   home address + safe zone, a bloodwork document (with blood type), an upcoming appointment, and
   an emergency contact. A reserved "demo caregiver" account is created alongside it.
2. Any WhatsApp number that has never messaged the bot before is auto-provisioned as a patient and
   gets its own **clone** of the template's rows — not a shared copy, so multiple judges texting
   at once each get an independent profile. No caregiver contact, no consent chain: the patient is
   immediately fully populated and can ask "what medicine do I take", "what's my blood type",
   "when's my next appointment", etc.
3. Google is the one step that stays real. Right after provisioning, the patient is sent the same
   OAuth consent link the normal `connect google` command sends — nothing about the Gmail/Calendar
   connection is seeded or faked.
4. An SOS trigger (the same `help`/`SOS`/`emergency`/救命 regex as always, SAFETY-2) never contacts
   a real phone number in demo mode. It's rerouted to the reserved demo caregiver account only,
   sent through the normal window-aware `outbound.send_text` (never the window-bypass
   `send_urgent`), and logged to `sos_events` exactly as a real trigger would be.

With `DEMO_MODE` unset or `false`, none of the above runs — every request takes the exact same
path it did before this feature existed.

## 12.4 Deployment pipeline

1. Push to `main` on GitHub.
2. Railway auto-deploys — builds the Dockerfile, runs migrations, health-checks, cuts over.
3. Rollback is one click in the Railway UI if the health check fails.

Optional GitHub Actions on PR: `ruff`, `black --check`, `pytest`. Worth the ten minutes.

**Do not deploy during the demo window.** Railway's cutover drops in-flight requests and
restarts the scheduler. Freeze deploys an hour before.

## 12.5 Volumes

Mounted at `/data`, so `MEDIA_ROOT=/data/media` and `TTS_CACHE_ROOT=/data/tts`. Two consequences:

- The volume pins `api` to one replica and one region. Already required by the scheduler.
- The nightly cleanup job must actually delete files, not just DB rows. A leaked media
  directory will fill 1 GB faster than you expect once you're testing PDFs.

## 12.6 Folder structure

```
dementia-assistant/
├── CLAUDE.md
├── README.md
├── Dockerfile
├── docker-compose.yml            # local dev: app + postgres
├── requirements.txt
├── .env.example
├── .gitignore
├── alembic.ini
├── pyproject.toml                # ruff, black, pytest config
│
├── docs/                         # this blueprint
│
├── alembic/
│   ├── env.py
│   └── versions/
│
├── scripts/
│   ├── seed_demo.py              # idempotent demo data
│   ├── fake_twilio_post.py       # replay webhook payloads locally
│   ├── check_deps.py             # pre-demo smoke test
│   └── gen_keys.py               # Fernet + signing keys
│
├── app/
│   ├── main.py                   # FastAPI app, lifespan, scheduler start, boot recovery
│   │
│   ├── core/
│   │   ├── config.py             # pydantic-settings
│   │   ├── logging.py            # structlog + PII redaction processor
│   │   ├── security.py           # Fernet, signed media tokens, admin auth
│   │   ├── errors.py             # exception types + handlers
│   │   └── deps.py               # FastAPI dependencies
│   │
│   ├── db/
│   │   ├── session.py            # async engine, session factory
│   │   ├── base.py
│   │   └── models/               # one module per table group
│   │       ├── user.py
│   │       ├── message.py
│   │       ├── medication.py
│   │       ├── reminder.py
│   │       ├── google.py         # oauth_tokens, calendar_events, email_cache
│   │       ├── location.py
│   │       └── audit.py
│   │
│   ├── api/
│   │   ├── webhooks.py           # /webhooks/twilio, /status
│   │   ├── oauth.py              # /oauth/google/*
│   │   ├── media.py              # /media/{token}
│   │   ├── health.py             # /health, /ready, /deps, /metrics
│   │   └── internal.py           # /internal/* (admin-token guarded)
│   │
│   ├── channels/
│   │   ├── base.py               # ChannelProvider protocol
│   │   ├── twilio_whatsapp.py    # the implementation
│   │   ├── inbound.py            # normalize six wire formats → InboundMessage
│   │   ├── outbound.py           # THE ONLY MODULE THAT SENDS. window + throttle + split
│   │   └── media.py              # download, hash, validate, store
│   │
│   ├── pipelines/
│   │   ├── router.py
│   │   ├── text.py
│   │   ├── voice.py
│   │   ├── image.py
│   │   ├── document.py
│   │   ├── email.py
│   │   └── calendar.py
│   │
│   ├── ai/
│   │   ├── gemini_client.py      # THE ONLY MODULE THAT CALLS GEMINI
│   │   ├── prompts/
│   │   │   ├── persona.py        # dementia-friendly system prompts, en + zh
│   │   │   ├── intent.py
│   │   │   ├── vision.py
│   │   │   ├── document.py
│   │   │   └── email.py
│   │   ├── context.py            # builds the structured context block
│   │   └── schemas.py            # Pydantic models for structured outputs
│   │
│   ├── safety/
│   │   ├── medication_guard.py   # SAFETY-1
│   │   ├── sos.py                # SAFETY-2, no LLM
│   │   └── simplifier.py         # reading-level enforcement
│   │
│   ├── google/
│   │   ├── oauth.py              # flow + token storage
│   │   ├── tokens.py             # encrypt, decrypt, refresh
│   │   ├── gmail.py
│   │   └── calendar.py
│   │
│   ├── speech/
│   │   ├── stt.py                # preprocessing + Gemini transcription
│   │   ├── tts.py                # TTSProvider protocol
│   │   ├── edge_tts_provider.py
│   │   ├── gcloud_tts_provider.py  # stub fallback
│   │   └── audio.py              # ffmpeg wrappers
│   │
│   ├── jobs/
│   │   ├── scheduler.py          # APScheduler setup
│   │   ├── reminders.py
│   │   ├── sync_gmail.py
│   │   ├── sync_calendar.py
│   │   ├── token_refresh.py
│   │   ├── outbound_flush.py
│   │   ├── location_checkin.py
│   │   └── cleanup.py
│   │
│   ├── services/                 # business logic, no HTTP, no SDK
│   │   ├── conversation.py
│   │   ├── medication.py
│   │   ├── agenda.py
│   │   ├── contacts.py
│   │   └── geofence.py
│   │
│   └── i18n/
│       ├── strings.py            # every fixed string, en + zh
│       └── formats.py            # date/time localisation
│
└── tests/
    ├── conftest.py
    ├── unit/
    ├── integration/
    └── fixtures/
        ├── twilio/               # one payload per input type
        ├── gemini/               # recorded responses per pipeline per language
        └── google/               # gmail + calendar sync fixtures
```

### Structural rules

- `app/services/` contains business logic with no HTTP client and no SDK import. That's what
  makes it unit-testable without mocks.
- `app/channels/outbound.py` and `app/ai/gemini_client.py` are the two choke points. If any
  other module imports the Twilio or Gemini SDK, something has gone wrong.
- `app/i18n/strings.py` holds every user-facing fixed string in both languages. No hardcoded
  English anywhere else — otherwise the Chinese experience quietly degrades to half-English,
  which is exactly the failure a judge will notice.

## 12.7 Local development

```yaml
# docker-compose.yml
services:
  db:
    image: postgres:16
    environment: [POSTGRES_PASSWORD=dev, POSTGRES_DB=dementia]
    ports: ["5432:5432"]
  app:
    build: .
    env_file: .env
    volumes: ["./:/app", "./data:/data"]
    ports: ["8000:8000"]
    depends_on: [db]
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Then `ngrok http 8000` and point the Twilio webhook at the forwarding URL. For most
development, skip the phone entirely and use `POST /internal/simulate/inbound`.
