# 15 — Diagrams

Covers brief section 30. The ER diagram lives in §08 and the architecture overview in §01;
both are cross-referenced rather than duplicated.

## 15.1 Overall architecture

See `01-summary-and-architecture.md` §2.1.

## 15.2 Deployment architecture

```mermaid
graph TB
    subgraph Internet
        PHONE[Patient's phone<br/>WhatsApp]
        BROWSER[Browser<br/>OAuth consent only]
        TW[Twilio<br/>WhatsApp Sandbox]
        GOOG[Google APIs]
        GEM[Gemini API]
        EDGE[edge-tts endpoint]
    end

    subgraph "Railway project"
        subgraph "api service — 1 replica, Docker"
            UV[Uvicorn · 1 worker]
            FAPI[FastAPI routes]
            WRK[asyncio worker loop]
            SCH[APScheduler]
            FF[ffmpeg]
        end
        PGS[(Postgres<br/>private network)]
        VOL[/Volume /data<br/>media + tts cache/]
    end

    subgraph GitHub
        REPO[main branch]
    end

    PHONE <--> TW
    TW -->|webhook POST| FAPI
    FAPI -->|REST send| TW
    BROWSER -->|OAuth callback| FAPI
    UV --- FAPI
    FAPI --> WRK
    WRK --> FF
    SCH --> WRK
    FAPI --> PGS
    WRK --> PGS
    SCH --> PGS
    WRK --> VOL
    FF --> VOL
    WRK --> GEM
    WRK --> GOOG
    WRK --> EDGE
    SCH --> GOOG
    REPO -->|auto-deploy| UV
```

## 15.3 User authentication (OAuth)

```mermaid
sequenceDiagram
    autonumber
    actor P as Patient
    participant WA as WhatsApp/Twilio
    participant API as Railway API
    participant DB as Postgres
    participant G as Google

    P->>WA: "connect google"
    WA->>API: webhook
    API->>DB: INSERT oauth_states (state, exp 10m)
    API->>WA: link with state
    WA->>P: tap to connect
    P->>G: opens consent URL
    G->>P: sign in
    G->>P: consent — Gmail RO, Calendar RO
    P->>G: allow
    G->>API: GET /oauth/google/callback?code&state
    API->>DB: validate state, mark used
    API->>G: POST /token (code, access_type=offline)
    G-->>API: access + refresh token
    API->>API: Fernet encrypt both
    API->>DB: INSERT oauth_tokens (bytea)
    API->>P: success page
    API->>WA: "Your email is connected."
    WA->>P: confirmation

    Note over API,G: every 10 min: refresh tokens<br/>expiring within 5 min
```

## 15.4 WhatsApp message flow

```mermaid
sequenceDiagram
    autonumber
    actor P as Patient
    participant TW as Twilio
    participant API as Webhook
    participant DB as Postgres
    participant W as Worker
    participant AI as Gemini
    participant OG as Outbound gateway

    P->>TW: message
    TW->>API: POST form-encoded + X-Twilio-Signature
    API->>API: validate signature
    alt invalid
        API-->>TW: 403
    end
    API->>DB: upsert user · INSERT messages · set last_inbound_at
    API->>W: enqueue(message_id)
    API-->>TW: 200 <Response/>  (< 500ms)

    W->>DB: load message + context
    W->>W: detect language · classify intent
    W->>AI: generate (one call)
    AI-->>W: response
    W->>W: medication_guard · simplifier
    W->>DB: INSERT outbound message
    W->>OG: send(user, text)
    OG->>DB: window open?
    alt open
        OG->>TW: free-form
    else closed
        OG->>TW: template
    end
    TW->>P: reply
    TW->>API: status callback
    API->>DB: status = delivered
```

## 15.5 Voice processing

```mermaid
flowchart TD
    A[Voice note<br/>OGG/Opus] --> B[Download<br/>Twilio basic auth]
    B --> C{duration 0.5–60s?}
    C -->|no| Z["Could you say that<br/>again, more briefly?"]
    C -->|yes| D["ffmpeg normalise<br/>16kHz mono · highpass 80 ·<br/>lowpass 8k · denoise · loudnorm"]
    D --> E[Gemini audio:<br/>transcript + language JSON]
    E --> F{confidence ≥ 0.5?}
    F -->|no| Y["I couldn't hear that clearly"]
    F -->|yes| G[INSERT transcripts]
    G --> H[Text pipeline]
    H --> I[Reply text]
    I --> J{reply_mode<br/>includes audio?}
    J -->|no| K[Send text only]
    J -->|yes| L[edge-tts → MP3]
    L --> M[ffmpeg → OGG/Opus 24kbps]
    M --> N[Cache by sha256]
    N --> O[Send text]
    O --> P[wait 3s — sandbox throttle]
    P --> Q[Send audio as separate message]
```

## 15.6 Image processing

```mermaid
flowchart TD
    A[Image] --> B[Download · verify magic bytes]
    B --> C{≤ 5 MB and real image?}
    C -->|no| Z[Friendly rejection]
    C -->|yes| D[Auto-rotate from EXIF<br/>then STRIP EXIF - GPS]
    D --> E[Downscale ≤1568px · JPEG q85]
    E --> F[Gemini vision:<br/>kind · verbatim text · description · confidence]
    F --> G{kind}
    G -->|pill_bottle / prescription| H[INSERT medication_candidates<br/>status = pending]
    H --> I["I've saved this for your<br/>caregiver to check"]
    G -->|document| J[Document summarise path]
    G -->|scene| K[Simple description<br/>+ recent location if under 1h]
    G -->|person| L[Generic description<br/>NEVER identify]
    G -->|other| M[Generic description]

    style H fill:#fff3cd
    style I fill:#fff3cd
```

The highlighted path is the safety-critical one: vision output never reaches `medications`.

## 15.7 PDF processing

```mermaid
flowchart TD
    A[PDF] --> B[Download]
    B --> C{≤16 MB and ≤30 pages?}
    C -->|no| Z["That document is too big<br/>for me to read"]
    C -->|yes| D[pypdf text probe]
    D --> E{extractable text<br/>> 100 chars?}
    E -->|yes| F[was_scanned = false]
    E -->|no| G[was_scanned = true]
    F --> H[Gemini document part<br/>+ medical summarisation prompt]
    G --> H
    H --> I[INSERT documents<br/>summary_en + summary_zh]
    I --> J[3–5 short sentences<br/>in detected language]
    J --> K{offer voice reading?}
    K -->|yes| L[TTS path]
```

## 15.8 Gmail flow

```mermaid
flowchart TD
    A[Scheduler · every 15 min] --> B[For each user with a token]
    B --> C[messages.list<br/>newer_than:3d, no promotions/social]
    C --> D{new ids vs email_cache?}
    D -->|none| E[Done]
    D -->|some| F[messages.get format=metadata]
    F --> G[Deterministic pre-filter:<br/>known hospital / clinic / GP domains<br/>→ priority ≥ 4]
    G --> H[Gemini batch classify — up to 25<br/>category · priority · summary_en · summary_zh]
    H --> I[UPSERT email_cache]
    I --> J{any priority 5<br/>and time-sensitive?}
    J -->|yes| K[Proactive nudge<br/>via outbound gateway]
    J -->|no| E

    L["Patient: 'any important emails?'"] --> M[Query email_cache<br/>priority ≥ 4, last 3 days]
    M --> N[Top 3 pre-computed summaries<br/>NO AI call — instant]
```

The rules-based pre-filter runs **before** the model so a model failure can never bury a
hospital email.

## 15.9 Calendar flow

```mermaid
flowchart TD
    A[Scheduler · every 15 min] --> B[events.list<br/>singleEvents=true, -1d to +14d]
    B --> C[Normalise to UTC<br/>flag all-day events]
    C --> D[Hash each event]
    D --> E{hash changed<br/>vs cache?}
    E -->|new| F[INSERT calendar_events]
    E -->|changed| G["Notify: 'Your appointment<br/>moved to 11 o'clock'"]
    E -->|same| H[skip]
    F --> I[Overlap scan:<br/>event×event and event×medication]
    G --> I
    I --> J{conflict?}
    J -->|yes| K[Gentle notice to patient]
    J -->|no| L[Generate reminders<br/>T-24h and T-2h]
    K --> L
    L --> M[UPSERT reminders<br/>unique on source_id + offset]
```

## 15.10 Reminder flow

```mermaid
sequenceDiagram
    autonumber
    participant S as Scheduler (60s)
    participant DB as Postgres
    participant MG as medication_guard
    participant OG as Outbound gateway
    participant TW as Twilio
    actor P as Patient
    actor C as Caregiver

    S->>DB: SELECT reminders WHERE active AND next_fire_at <= now()
    DB-->>S: due rows
    S->>DB: load source row (medications / calendar_events)
    S->>S: render from TEMPLATE, not generation
    S->>MG: verify drug names ⊆ source row
    alt guard fails
        MG-->>S: discard, use safe fallback
    end
    S->>DB: INSERT outbound_queue
    S->>DB: advance next_fire_at from RRULE

    loop every 5s
        OG->>DB: claim pending (FOR UPDATE SKIP LOCKED)
        OG->>DB: read users.last_inbound_at
        alt window open
            OG->>TW: free-form
        else window closed
            OG->>TW: appointment template
        end
        TW->>P: reminder
    end

    alt patient replies OK / 好
        P->>TW: ack
        TW->>DB: INSERT reminder_acks
    else no ack in 30 min
        S->>OG: escalate
        OG->>C: "Mum hasn't confirmed<br/>her morning medicine"
    end
```

## 15.11 SOS flow

```mermaid
flowchart TD
    A[Inbound message] --> B{regex match:<br/>help · sos · 救命 · 紧急}
    B -->|no| C[Normal pipeline]
    B -->|yes| D[INSERT sos_events<br/>NO LLM CALL]
    D --> E[SELECT contacts<br/>WHERE is_emergency<br/>ORDER BY priority]
    E --> F{any contacts?}
    F -->|none| G["I don't have anyone to call.<br/>Please call 995."]
    F -->|yes| H[Last location_ping<br/>under 60 min?]
    H --> I[Fixed-string alert to each contact<br/>+ location if fresh]
    I --> J{send ok?}
    J -->|yes| K[Record outcome]
    J -->|no| L[Try next contact<br/>log failure<br/>NEVER claim success]
    K --> M["I have told your daughter.<br/>Stay where you are."]

    style D fill:#f8d7da
    style G fill:#f8d7da
    style L fill:#f8d7da
```

No LLM anywhere in this path. A Gemini outage must not break SOS.

## 15.12 Location & safe zones

```mermaid
sequenceDiagram
    autonumber
    participant S as Scheduler
    participant OG as Outbound gateway
    actor P as Patient
    participant API as Webhook
    participant GF as Geofence service
    actor C as Caregiver

    Note over S: pull-based — WhatsApp does not stream GPS
    S->>OG: check-in time
    OG->>P: "Where are you now?<br/>Tap to share your location"
    alt patient shares
        P->>API: location pin (Latitude, Longitude)
        API->>GF: haversine vs safe_zones
        alt outside all zones
            GF->>C: alert + coordinates
        else near a shop zone
            GF->>P: "You wanted to buy milk"
        else inside home
            GF->>GF: log only
        end
    else no reply in 20 min
        S->>C: "No response to check-in"
    end
```

## 15.13 Database ER diagram

See `08-database.md` §8.1.

## 15.14 Message lifecycle state machine

```mermaid
stateDiagram-v2
    [*] --> received: webhook persists
    received --> processing: worker dequeues
    processing --> generated: pipeline completes
    processing --> degraded: AI unavailable
    degraded --> generated: fallback response
    generated --> queued: outbound row created
    queued --> awaiting_window: 24h window closed
    awaiting_window --> queued: patient messages again
    queued --> sent: Twilio accepted
    sent --> delivered: status callback
    delivered --> read: status callback
    sent --> failed: error code
    failed --> queued: retry (max 3)
    failed --> [*]: exhausted — CRITICAL if medication
    read --> [*]
    processing --> received: crash — boot recovery re-enqueues
```

The `processing → received` edge is the boot recovery pass. It is why a crash mid-pipeline
costs a delay rather than a lost message.
