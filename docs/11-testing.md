# 11 — Testing Strategy

Covers brief section 24. Scoped for a hackathon: enough tests to stop the demo breaking, not
a coverage target.

## 11.1 Priorities

Test in this order. If you run out of time, stop at the line.

1. **Medication guard** — a wrong drug name reaching a patient is the worst failure mode.
2. **Outbound gateway window logic** — decides whether reminders arrive at all.
3. **Webhook parsing for all six input types** — the front door.
4. **Language detection and reply-language selection** — the headline feature.
5. **Twilio signature validation** — easy to get subtly wrong behind Railway's proxy.
6. **OAuth callback** — state validation, token encryption round-trip.
   — cut line —
7. Pipeline happy paths.
8. Scheduler RRULE advancement.
9. Error and degraded-mode paths.

## 11.2 Unit tests

`pytest` + `pytest-asyncio`. No network, no DB.

| Target | Cases |
|---|---|
| `detect_language` | pure English · pure Simplified · mixed both directions · Traditional input · emoji-only · numbers-only · empty |
| `medication_guard` | drug in context (pass) · drug not in context (block) · similar-but-different name (block) · no drug mentioned (pass) · Chinese drug name · guard rejection returns the fallback string |
| `simplifier` | over-long sentence split · list truncated to 3 · passive→active |
| `window_is_open` | inbound 1h ago (open) · 23h59m (open) · 24h01m (closed) · never (closed) · null (closed) |
| `haversine` | known distances · antimeridian · identical points |
| `parse_rrule_next` | daily · twice-daily · weekly · DST-free Singapore case · past `next_fire_at` catch-up |
| `validate_twilio_signature` | valid · tampered param · wrong URL scheme (the Railway proxy case) · missing header |
| Fernet round-trip | encrypt→decrypt equality · wrong key raises · ciphertext differs across calls |
| Filename generation | ≤20 chars · ASCII only · extension preserved |

## 11.3 Integration tests

Real Postgres via `testcontainers` (or a GitHub Actions service container). All external HTTP
mocked with `respx`.

- Full inbound text → outbound reply, asserting rows in `messages` and the correct reply language.
- Media message → `media_files` row, file on disk, correct SHA-256.
- Duplicate `MessageSid` → exactly one outbound message (idempotency).
- Reminder due → `outbound_queue` row → gateway picks the right path for open and closed windows.
- OAuth callback → encrypted token stored, plaintext absent from the row.
- Token refresh → `expires_at` advanced, ciphertext changed.
- Boot recovery pass → orphaned `received` message gets processed.
- Cleanup job → respects each retention window, doesn't over-delete.

## 11.4 Mocking external APIs

| Service | Approach |
|---|---|
| **Twilio inbound** | `scripts/fake_twilio_post.py` builds realistic form payloads with valid signatures for all six types. Also usable manually against a running dev server. |
| **Twilio outbound** | `respx` intercepting the Messages endpoint. Assert the request body — especially that media sends carry **no** `Body` param. |
| **Gemini** | `respx` with recorded fixtures per pipeline. Keep one fixture per language per pipeline. Also test a 429 and a malformed-JSON response — both are routine in production and both are commonly untested. |
| **Gmail / Calendar** | Fixture JSON from a real sync, captured once and sanitised. Include an all-day event and a recurring event — they break naive code. |
| **edge-tts** | Mocked to return a fixture MP3. One real call in the smoke test only. |
| **ffmpeg** | Real. It's local, fast, and mocking it hides the format bugs that actually bite. |

## 11.5 Webhook testing

Locally: `ngrok http 8000`, point the sandbox webhook at the forwarding URL, message from your
own WhatsApp. Remember to re-point when ngrok restarts.

Faster loop, no phone: `POST /internal/simulate/inbound`. Use this for 90% of development —
each of the six input types has a canned payload. It exercises the identical code path from
the normalizer onward.

Signature testing deserves its own case. Build the signature exactly as Twilio does (full URL
including scheme and query, plus sorted POST params, HMAC-SHA1, base64) and assert both accept
and reject. Then assert it still works when `X-Forwarded-Proto: https` is present and
`request.url.scheme` is `http` — that is the Railway configuration, and getting it wrong means
every real message 403s while every local test passes.

## 11.6 OAuth testing

- Unit: state generation, expiry, single-use enforcement, token encryption.
- Integration: mock Google's token endpoint with `respx`, drive the callback, assert the row.
- Manual, once per environment: the real flow end to end, in an incognito window, with the
  actual demo Google account. **Do this on the deployed Railway URL, not localhost** — the
  `redirect_uri` differs and is exact-matched.
- Failure paths worth exercising manually: deny consent, use an expired state link, revoke
  access at myaccount.google.com and confirm the `invalid_grant` handling gives the user a
  clear reconnect message.

## 11.7 End-to-end demo rehearsal

Not automated. A written script, run start to finish on the deployed environment, at least
twice, at least a day before the demo.

Each of these on a real phone, in both languages:

1. Send `join <code>` → onboarding reply.
2. "Connect google" → OAuth → confirmation over WhatsApp.
3. "What's my next appointment?" and "我下一个预约是什么时候?"
4. Voice note in English → text + voice reply.
5. Voice note in Mandarin → text + voice reply.
6. Code-switched voice note → correct handling of both languages.
7. Photo of a pill bottle → candidate saved, no dose confirmed.
8. PDF of a medical letter → summary.
9. "Any important emails?"
10. Share a location pin → contextual reply.
11. Send a contact card → emergency-contact prompt.
12. "Help" → SOS path fires, caregiver phone receives the alert.
13. Wait for a scheduled reminder to fire on its own.
14. Ask the same question three times → three warm, identical-quality answers.

## 11.8 Pre-demo smoke test

Run `GET /health/deps` and this checklist within an hour of demoing:

- [ ] Every participant has re-sent `join <code>` **today** (3-day expiry)
- [ ] `/health/deps` green for Twilio, Gemini, Google, TTS
- [ ] A real TTS synthesis returns playable OGG, not a file attachment
- [ ] Google token not expired; refresh token still valid (7-day expiry on unverified apps)
- [ ] Railway credit balance sufficient
- [ ] Gemini daily quota not already spent by testing
- [ ] Seed data loaded; at least one calendar event and one email in the next 24h
- [ ] Scheduler last-run timestamps are recent on `/metrics`
- [ ] Phone volume up, notifications on, screen mirroring working

Points 1 and 6 have each killed a hackathon demo before. The 3-day join expiry is silent, and
free-tier Gemini quota is genuinely easy to exhaust during a morning of rehearsal.
