# 04 — Google OAuth, Gmail & Calendar

Covers brief sections 6, 7 and 8.

## 4.1 OAuth flow

```
Patient (WhatsApp): "connect google"
   ↓
Backend generates state token, stores it, returns a link
   ↓
Patient taps → Google sign-in
   ↓
Consent screen: Gmail (read-only) · Calendar (read-only)
   ↓
Google redirects → GET /oauth/google/callback?code=...&state=...
   ↓
Backend validates state → exchanges code for access + refresh token
   ↓
Fernet-encrypt both → INSERT oauth_tokens
   ↓
Success page shown in browser + confirmation sent over WhatsApp
```

### Step detail

**1. Initiation.** The patient sends "connect google" / "连接谷歌". The backend creates a
`oauth_states` row: `state` (32 bytes URL-safe random), `user_id`, `expires_at` (10 minutes),
`used_at` (null). It replies with a link:

```
https://accounts.google.com/o/oauth2/v2/auth
  ?client_id=...
  &redirect_uri=https://<app>.up.railway.app/oauth/google/callback
  &response_type=code
  &scope=openid email profile
         https://www.googleapis.com/auth/gmail.readonly
         https://www.googleapis.com/auth/calendar.readonly
  &access_type=offline          ← required, or you get no refresh token
  &prompt=consent               ← required, or Google skips the refresh token on re-auth
  &state=<state>
  &include_granted_scopes=true
```

`access_type=offline` **and** `prompt=consent` are both mandatory. Without them Google returns
a refresh token only on the very first consent ever granted, and your second test run will
silently have no refresh token.

**2. Callback.** Validate `state` exists, is unexpired, and is unused; mark it used
immediately (single-use). Reject mismatches with a generic error — do not echo the state back.
Exchange the code at `https://oauth2.googleapis.com/token`. You receive `access_token`
(~1 hour), `refresh_token`, `expires_in`, `scope`, `id_token`.

**3. Storage.** Encrypt `access_token` and `refresh_token` with Fernet before the INSERT.
Store `expires_at` as an absolute UTC timestamp, the granted `scope` string, and the Google
`sub` from the id_token. Unique constraint on `(user_id, provider)`.

**4. Confirmation.** Show a plain success page in the browser, and send a WhatsApp message —
because the patient may not understand the browser page. From here they never leave WhatsApp.

### Token refresh

A scheduled job runs every 10 minutes:

```sql
SELECT * FROM oauth_tokens WHERE expires_at < now() + interval '5 minutes';
```

For each: POST to the token endpoint with `grant_type=refresh_token`, re-encrypt, update
`expires_at`. Refresh is also attempted lazily on any 401 from a Google call, once, before
giving up.

Failure modes:

| Failure | Cause | Response |
|---|---|---|
| `invalid_grant` | User revoked access, or the refresh token expired | Delete the token row, message the user asking them to reconnect. Do not retry. |
| Refresh token absent | `access_type=offline` / `prompt=consent` omitted | Bug. Force full re-consent. |
| Testing-mode expiry | An **unverified app in Testing status expires refresh tokens after 7 days** | Real risk if you build early and demo late. Either publish the consent screen or re-auth the morning of. Put it on the checklist. |
| Network error | Transient | Exponential backoff, 3 attempts, then alert. |

### Security

- Tokens encrypted at rest with Fernet (`TOKEN_ENCRYPTION_KEY`, 32-byte base64, from Railway
  secrets — never committed).
- Read-only scopes only. There is no code path in this system that can send an email, delete
  a message, or modify a calendar. That is a deliberate design property, not an oversight.
- `redirect_uri` is exact-matched by Google and registered in the Cloud Console. No wildcards.
- Tokens are never logged, never in `__repr__`, never in an error response. The decrypt helper
  returns a value that is only passed to the Google client.
- Decrypt at point of use; do not hold plaintext tokens in module-level state.
- OAuth consent screen: add the demo Google account under **Test users**. An unverified app
  with sensitive scopes is capped at 100 users and shows a warning interstitial — acceptable
  for a demo, and a real barrier to production (call it out in §14 risks).

---

## 4.2 Gmail integration

**Scope:** `gmail.readonly`. Polled, not live-queried.

### Sync job — every 15 minutes

1. `users.messages.list(userId='me', q='newer_than:3d -category:promotions -category:social', maxResults=25)`
2. For each id not already in `email_cache`, `users.messages.get(format='metadata')` for
   headers plus `snippet`. Only fetch `format='full'` for messages that pass the priority
   filter — it saves quota and avoids storing more content than you need.
3. Upsert into `email_cache` keyed on `(user_id, gmail_message_id)`.

Use `historyId` for incremental sync once the first full pass is done. Full re-list on
`404 historyId too old`.

### Classification and prioritisation

One Gemini call per batch, not per email. Send subject + sender + snippet for up to 25
messages and ask for structured JSON back:

```json
[{"id": "...", "category": "medical|family|appointment|admin|other",
  "priority": 1-5, "needs_action": true,
  "summary_en": "...", "summary_zh": "..."}]
```

Prompt rules that matter:
- Category `medical` for hospitals, clinics, doctors, lab results, pharmacies.
- Category `family` for known contacts in the `contacts` table — pass those names in as context.
- Priority 5 only for time-sensitive items in the next 48 hours.
- Summaries must be one sentence, plain language, no jargon.
- **Never extract or restate medication names or doses from an email into a reminder.**
  Emails feed information; only a caregiver-verified `medications` row drives a reminder.
  (`CLAUDE.md` SAFETY-1.)

Store both language summaries at sync time. The patient may ask in either language, and
pre-computing both means the answer is instant and consistent.

### Serving it

"Any important emails?" → query `email_cache` where `priority >= 4` and
`received_at > now() - 3 days`, order by priority then recency, take top 3, render in the
detected language. No AI call at read time — the summaries already exist. This is what makes
repeat-asking (the core dementia use case) feel instant.

### Detecting important medical email

Beyond the LLM category, a deterministic pre-filter promotes anything from a sender domain in
a small allowlist (`*.hospital.sg`, known clinic domains, the patient's GP) to priority 4
minimum, regardless of what the model says. Rules-then-model, so a model failure cannot bury
a hospital email.

### Family messages

Cross-reference the sender against `contacts`. A match raises priority and lets the reply say
"your daughter emailed" rather than "someone emailed", which is materially more useful to a
person with memory impairment.

---

## 4.3 Google Calendar integration

**Scope:** `calendar.readonly`.

### Sync job — every 15 minutes

`events.list(calendarId='primary', timeMin=now-1d, timeMax=now+14d, singleEvents=True,
orderBy='startTime')`. `singleEvents=True` expands recurring events into instances — without
it you get RRULEs you'd have to expand yourself.

Upsert into `calendar_events` on `(user_id, google_event_id)`. Store a content hash so you can
detect a changed time and notify: *"Your appointment with Dr Tan moved to 11am."* That
detail demos extremely well and costs almost nothing.

Use `syncToken` for incremental sync; on `410 Gone`, discard the token and do a full re-list.

### Serving it

| Question | Query | Notes |
|---|---|---|
| "What's my next appointment?" | earliest event with `start_at > now()` | Answer with day-relative language: "tomorrow at 10 in the morning", not "2026-07-24T10:00". |
| "What am I doing today?" | events where `start_at::date = today` in `Asia/Singapore` | Merge with today's meds and reminders into one agenda. |
| "Who is visiting today?" | today's events whose attendees or title match a `contacts` row | Falls back to attendee display names. |

### Daily agenda

A scheduled job at 08:00 local composes one message combining calendar events, medication
times, and any active shopping or routine reminders — a single ordered list, not three
separate notifications. This is the "What should I do today?" flow from your architecture deck
and it is the strongest single demo moment.

### Conflict detection

Pure Python, no AI: sort today's events by start, flag any pair where
`a.end_at > b.start_at`. Also flag a medication time that falls inside an appointment window,
which is the conflict that actually matters for this user. Surface it gently:
*"You have two things at 10 o'clock. Would you like me to remind your daughter?"*

### Reminder scheduling from calendar

For each event, `reminders` rows are created at T-24h and T-2h (configurable per user). The
scheduler owns firing; see §09. Deduplicate on `(source='calendar', google_event_id, offset)`
so a re-sync doesn't create a second reminder for the same event.

### Time zones

Google returns RFC3339 with offsets, and all-day events as bare dates with no time. Store
everything as `TIMESTAMPTZ` in UTC; convert to `Asia/Singapore` only at render time. All-day
events need an explicit branch or they render as "midnight", which is confusing and wrong.
