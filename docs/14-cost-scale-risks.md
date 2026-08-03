# 14 — Cost, Scalability, Risks & Future Work

Covers brief sections 26, 27, 28 and 29.

> **Figures checked July 2026.** Free tiers on both Gemini and Railway have been cut more than
> once in the past year. Re-verify against the live pricing pages before relying on any number here.

## 14.1 Cost analysis

### Per-component status

| Component | Status | Notes |
|---|---|---|
| Python, FastAPI, SQLAlchemy, Alembic, APScheduler, ffmpeg | **Completely free** | Open source, no metering |
| `edge-tts` | **Completely free** | Unofficial. No quota, no key, no SLA. |
| Google OAuth | **Completely free** | |
| Gmail API | **Free tier** | Generous daily quota. One mailbox polled every 15 min is negligible. |
| Google Calendar API | **Free tier** | Same. |
| Gemini API | **Free tier** | Flash / Flash-Lite only. ~10–15 RPM, a few hundred to ~1,500 requests/day. Pro removed from free tier April 2026. |
| Twilio WhatsApp | **Free tier → paid** | Trial credit covers a demo. Beyond that, per-message Meta rates plus Twilio's markup. |
| Railway | **Paid** | $5 one-time trial credit (30 days, no card). Then Hobby at **$5/month minimum**, which includes $5 of usage. No permanent free tier. |
| Railway Postgres | **Paid** | Metered inside the same credit. A demo-sized instance is roughly $3–8/month. |
| Railway volume | **Paid** | Metered by GB. 1 GB is pennies. |

### Estimated monthly cost

Assumptions: ~20 messages per user per day, 30% voice, 15% media, Gmail + Calendar polled
every 15 minutes per user.

| | Hackathon demo | 10 users | 100 users | 1,000 users |
|---|---|---|---|---|
| Railway compute | $0 (trial) or $5 | $5 | $10–20 | $40–80 |
| Railway Postgres | included | $3–8 | $10–20 | $40–80 |
| Railway volume + egress | ~$0 | $1 | $3 | $10–25 |
| WhatsApp messages | $0 (trial credit) | $2–5 | $20–50 | **$200–500** |
| Gemini | $0 (free tier) | $0–5 | **$30–60** | **$300–600** |
| Gmail + Calendar | $0 | $0 | $0 | $0–20 |
| TTS | $0 | $0 | $0 | $0 (or $50+ on Google Cloud TTS if edge-tts is dropped) |
| **Total** | **$0–5** | **$11–24** | **$73–153** | **$590–1,285** |

### Where it breaks

- **Gemini free tier dies at roughly 5–15 users.** 1,500 requests/day across users, with each
  message costing 1–2 calls, is 40–70 messages/day total. Beyond that you enable billing —
  and note that **enabling billing removes the free allowance entirely rather than adding to
  it.** Budget for the full paid rate, not the overage.
- **WhatsApp messaging becomes the largest line item at scale**, not AI. It is also getting
  worse: from **1 October 2026 Meta charges per business message including service replies and
  utility messages sent inside the 24-hour window**, which are free today. At 1,000 users this
  is a step change, and any business model built on today's free service window needs revisiting.
- **Railway is not the cheapest at scale.** It is the fastest to ship on. At 1,000 users a VPS
  with managed Postgres would be half the price and twice the work.

### Cheapest viable production configuration

Not needed for the demo; useful if a judge asks "what would this cost to run for real?"

Fly.io or a $6 VPS · Supabase free-tier Postgres · Cloudflare R2 for media (10 GB free) ·
Gemini Flash-Lite paid · Meta Cloud API direct instead of Twilio (removes the BSP markup) ·
edge-tts. Roughly **$15–30/month for 100 users**, at the cost of several days of setup.

---

## 14.2 Scalability

Everything below is `DEFERRED` for the demo. It exists so you can answer the question.

**The order in which this architecture breaks, and what fixes each:**

1. **Gemini free-tier RPM** (~5–15 users) → enable billing, add response caching for repeated
   questions. Caching helps unusually well here: a dementia user asks the same question
   repeatedly, so a 5-minute cache keyed on `(user_id, normalised_question)` has a genuinely
   high hit rate. That is a real optimisation, not a theoretical one.
2. **The single replica** (~200–500 users) → move the scheduler out of the process, then
   horizontal-scale the API. This is the first real architectural change and everything else
   depends on it.
3. **In-process queue** (same point) → the `outbound_queue` table with `SKIP LOCKED` already
   works multi-consumer. Replace the in-memory inbound queue with the same pattern and you can
   run N workers. Redis and Celery become worth it around here, not before.
4. **Postgres write volume** (~5,000 users) → connection pooling with PgBouncer, then read
   replicas for the cache tables, then partition `messages` by month.
5. **Media on a volume** (any multi-replica point) → S3-compatible object storage. R2 is free
   to 10 GB and has no egress fees, which matters because Twilio fetches every media file.

**Caching layers, in order of value:**

| Cache | Key | TTL | Why |
|---|---|---|---|
| TTS audio | `sha256(text + voice + rate)` | permanent | Reminders repeat verbatim. Highest hit rate in the system. |
| Q&A responses | `(user_id, normalised_question)` | 5 min | Repeat-asking is the core user behaviour. |
| Email/calendar summaries | pre-computed at sync | until re-sync | Already in the design. This is why answers are instant. |
| Google API responses | `(user_id, endpoint)` | 15 min | Stays inside quota. |

**Future microservices**, if it ever came to that: split the AI worker first (it has different
scaling and failure characteristics from the webhook), then the scheduler, then the Google sync
workers. The webhook receiver itself stays tiny and stateless and scales trivially.

---

## 14.3 Risks & limitations

### Platform

| Risk | Severity | Mitigation |
|---|---|---|
| **24-hour free-form window** (Meta's platform rule, not a sandbox one - still applies on the current number) | High | Keep the window open where possible; approved templates cover the rest. §03. |
| **Template approval takes real review time and can be rejected** | Medium | Submit well before a demo, not the morning of. Check `/internal/debug/templates`. |
| 1 message / 3 seconds | Medium | Self-imposed in the outbound gateway. |
| **Meta charges for in-window messages from Oct 2026** | Medium (future) | Affects the business case, not the demo. |
| **Railway has no permanent free tier** | Medium | $5 trial or $5/month. Check the balance before demoing. |
| Railway redeploy restarts the scheduler | Medium | `misfire_grace_time`, `coalesce`, boot recovery pass. Freeze deploys before the demo. |

### AI

| Risk | Severity | Mitigation |
|---|---|---|
| **Free-tier prompts may train Google's models** | High for real data | Synthetic data only. Non-negotiable. |
| **Enabling billing deletes the free allowance** | Medium | Don't switch it on casually. |
| Free-tier quota cut without notice (happened twice) | Medium | Verify before demo day. Have a paid key ready but not enabled. |
| 429s at peak | Medium | Backoff, local token bucket, degraded modes. |
| **Hallucination on medical content** | **Highest** | Structural, not prompt-based: deterministic medication path, output guard, candidates table, SOS with no LLM. |
| **`edge-tts` is unofficial and can break** | High | `TTSProvider` interface with a Google Cloud TTS fallback stub. Smoke test before demoing. |
| Simplified-language register is weaker in Chinese than English | Medium | Test Chinese output separately. Don't assume it transfers. |

### OAuth

| Risk | Severity | Mitigation |
|---|---|---|
| **Unverified app: refresh tokens expire after 7 days in Testing** | **High — silent** | Re-auth on demo morning, or publish the consent screen. |
| Unverified app capped at 100 users, shows a warning | Low for demo, blocking for production | Add the demo account as a test user. Verification takes weeks. |
| Sensitive-scope verification requires a security assessment | High for production | Name it as a real barrier. It is the main reason this design is read-only. |

### Language

- **Dialect is unsupported.** Hokkien, Teochew, Cantonese — spoken by a large share of the
  elderly Singaporean population this targets. The most likely hard question from a judge.
  Answer it directly rather than deflecting.
- Dementia-affected speech degrades transcription accuracy, and there is no cheap fix.
- Models drift to Traditional characters; pin Simplified in the prompt and test for it.

### Product & ethical

These are worth more in a pitch than any technical detail, and most teams skip them.

- **Consent.** A person with dementia may not be able to give meaningful, ongoing consent to
  having their email read. A real deployment needs a legally authorised representative. This is
  a hard problem, not a checkbox.
- **Dependency.** If a patient comes to rely on the assistant and it fails silently, they miss
  medication. Which is why failed medication reminders are a `CRITICAL` log event with a
  caregiver escalation path, not a retry-and-forget.
- **This is not a medical device.** No dosage advice, no interaction checking, no diagnosis.
  The system reminds and reads; it does not treat. Regulatory classification would change
  immediately if it did.
- **This cannot call emergency services.** SOS notifies contacts. Say so in onboarding.
- **Surveillance.** Location tracking of a person who cannot consent is ethically loaded. The
  pull-based design — where the patient actively chooses to share — is, as it happens, the more
  defensible one. Worth framing as a deliberate choice rather than a technical limitation.

---

## 14.4 Future improvements

Ordered by ratio of value to effort.

1. **Caregiver dashboard.** The biggest missing piece. Verify medication candidates, set safe
   zones, see acknowledgements, receive alerts. Everything in the schema already supports it.
2. **Companion app for real geofencing.** The only way to get passive wandering detection.
   A minimal Android app posting location every few minutes to `POST /location/ping` would
   deliver the original architecture diagram properly.
3. **Migrate to Meta Cloud API direct.** Removes the 3-day expiry, the template restriction,
   and Twilio's markup. About 150 lines behind the existing `ChannelProvider` interface.
4. **Real utility templates in both languages**, once on a verified sender. Properly worded
   proactive reminders.
5. **Twilio Programmable Voice for SOS.** An actual phone call to a caregiver, not just a
   WhatsApp message.
6. **Medication image verification.** "Is this the right pill?" — but only as a match against a
   caregiver-verified reference photo, never as open-ended recognition. The distinction is the
   whole safety argument.
7. **RAG over the patient's own documents.** Once there are enough medical letters to be worth
   searching. Needs a vector store; not before.
8. **Wearable integration** (fall detection, heart rate) via Google Fit or a device SDK.
9. **Multi-patient support** for care homes. The schema is already user-scoped; the work is in
   the caregiver UI and access control.
10. **Smart home integration** — lights on at a reminder, door sensors for wandering.
11. **Offline mode.** Genuinely hard on WhatsApp, since the channel itself requires connectivity.
    Realistically this becomes a companion-app feature or nothing.
