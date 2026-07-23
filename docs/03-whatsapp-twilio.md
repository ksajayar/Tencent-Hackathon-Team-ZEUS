# 03 — WhatsApp Channel (Twilio Sandbox)

Covers brief section 5. This is the highest-risk document in the set. Read it fully before
writing any channel code.

## 3.1 The flow

```
Patient (WhatsApp)
   ↓  message to the shared sandbox number +1 415 523 8886
Twilio WhatsApp Sandbox
   ↓  HTTP POST, application/x-www-form-urlencoded, X-Twilio-Signature header
Railway  →  POST /webhooks/twilio
   ↓  validate signature · persist · enqueue · return 200 <Response/>   (< 500ms)
Background worker
   ↓  normalize → fetch media → route → pipeline
Gemini  (transcribe / OCR / see / read / generate)
   ↓
Outbound gateway  (window check → throttle → send)
   ↓
Twilio  →  Patient
```

## 3.2 One-time setup

1. Twilio Console → Messaging → Try it out → Send a WhatsApp message.
2. Note the sandbox number and your join code (`join <two-words>`).
3. Set **When a message comes in** → `https://<app>.up.railway.app/webhooks/twilio`, method POST.
4. Set the **status callback** → `https://<app>.up.railway.app/webhooks/twilio/status`.
5. Each participant sends `join <two-words>` to the sandbox number from their own WhatsApp.

**The join lapses three days after it is made.** Nothing warns you; sends just start failing
with error 63016 / 63007. Put "everyone re-sends the join code" at the top of the demo-morning
checklist in §13.

## 3.3 Inbound: the wire format

Twilio POSTs form-encoded key–value pairs. Not JSON. In FastAPI:

```
form = await request.form()      # NOT await request.json()
```

Fields you will actually use:

| Field | Present when | Notes |
|---|---|---|
| `MessageSid` | always | Idempotency key. Unique-constrain it; Twilio retries on non-2xx. |
| `AccountSid` | always | Verify it matches yours. |
| `From` | always | `whatsapp:+6591234567` — strip the `whatsapp:` prefix before storing. |
| `To` | always | The sandbox number. |
| `Body` | text messages | Empty string on pure-media messages. |
| `NumMedia` | always | String `"0"`, not an int. Cast it. |
| `MediaUrl0..N` | `NumMedia > 0` | **Requires HTTP Basic auth** (AccountSid : AuthToken) to fetch. |
| `MediaContentType0..N` | `NumMedia > 0` | Trust but verify — sniff the actual bytes too. |
| `ProfileName` | usually | WhatsApp display name. Good default for `users.display_name`. |
| `WaId` | always | The WhatsApp ID, digits only. Use this as the stable user key, not `From`. |
| `Latitude`, `Longitude` | location messages | Decimal degrees, as strings. |
| `Address`, `Label` | location, sometimes | Human-readable place; only present if the sender's client supplied it. |

### Signature validation

Every request carries `X-Twilio-Signature`: an HMAC-SHA1 over the full request URL plus the
sorted POST parameters, keyed with your Auth Token. Use `twilio.request_validator.RequestValidator`.
Reject with `403` on mismatch. Two gotchas:

- Railway terminates TLS at the edge, so `request.url` may report `http`. Build the URL from
  `X-Forwarded-Proto` or hardcode the public base URL from config. Getting this wrong makes
  every signature fail.
- Validate **before** parsing anything into your models.

### Handling the six input types

| Type | Detection | Handling |
|---|---|---|
| **Text** | `NumMedia == 0` and `Body` non-empty | Straight to the text pipeline. |
| **Voice note** | `MediaContentType0` starts `audio/` (usually `audio/ogg`) | Download → ffmpeg normalise → Gemini audio pipeline (§06). |
| **Image** | `image/jpeg`, `image/png` | Download → Gemini vision/OCR pipeline. Route by intent: pill bottle vs prescription vs scene. |
| **Document** | `application/pdf` | Download → Gemini document pipeline. Reject >16 MB and >30 pages with a friendly message. |
| **Location** | `Latitude` + `Longitude` present | Write `location_pings` → evaluate safe zones and shop triggers (§07). No AI call. |
| **Contact card** | `MediaContentType0` is `text/vcard` | Download, parse with `vobject`, write to `contacts`, ask "should I add them as an emergency contact?" |

All six normalise into one internal shape before the router sees them:

```
InboundMessage:
  user_id, message_sid, kind: text|audio|image|document|location|contact
  text: str | None
  media: MediaRef | None        # local path, mime, sha256, bytes
  coords: (lat, lon) | None
  received_at: datetime
```

### Webhook response

Return `200` with `Content-Type: text/xml` and an empty `<Response/>`. Do not use TwiML to
reply — TwiML replies must be composed synchronously, which forces you to wait on Gemini
inside the webhook. All replies go out through the REST API from the worker instead.

Non-2xx makes Twilio retry, which duplicates messages. Catch everything, log it, still return
200. The only legitimate non-2xx is `403` on a bad signature.

## 3.4 Outbound: the 24-hour window

This is the constraint that shapes the reminder system.

- A message **from** the patient opens a 24-hour customer service window.
- Inside the window: any free-form text or media, no template needed.
- Outside the window: **only a pre-approved template.**
- On the sandbox, you cannot create templates. You get three fixed ones.

### The three sandbox templates

| Template | Body |
|---|---|
| Appointment reminder | `Your appointment is coming up on {{1}} at {{2}}` |
| Verification code | `Your {{1}} code is {{2}}` |
| Order shipped | `Your {{1}} order of {{2}} has shipped and should be delivered on {{3}}. Details: {{4}}` |

### The outbound gateway

`app/channels/outbound.py` is the only module allowed to call Twilio. Its decision:

```mermaid
flowchart TD
    A[send request] --> B{last_inbound_at<br/>within 24h?}
    B -->|yes| C[free-form send]
    B -->|no| D{message is a<br/>scheduled reminder?}
    D -->|yes| E["appointment template<br/>{{1}} = when, {{2}} = what"]
    D -->|no| F[park in outbound_queue<br/>status = awaiting_window]
    C --> G[throttle: 1 per 3s]
    E --> G
    G --> H[Twilio REST API]
    H --> I{success?}
    I -->|yes| J[status = sent, store SID]
    I -->|no| K[retry w/ backoff, max 3<br/>then status = failed + alert]
    F --> L[flush on next inbound message]
```

The `awaiting_window` state matters: when the patient next messages, the gateway flushes
anything parked, oldest first, so nothing is silently dropped.

### Using the appointment template for medication reminders

The honest position, and the one to say out loud in the pitch: on the sandbox this is a
workaround. `{{1}}` and `{{2}}` are free text, so:

- `{{1}}` = `"today at 9:00 AM"` → `{{2}}` = `"your morning medicine — Donepezil, 1 tablet"`
- Rendered: *"Your appointment is coming up on today at 9:00 AM at your morning medicine —
  Donepezil, 1 tablet"*

Grammatically awkward. Two mitigations, in order of preference:

1. **Keep the window open.** In practice the patient talks to the bot daily — that is the
   product. Send a gentle morning check-in *inside* the window and everything after it is
   free-form and perfectly worded. Design the demo so the judge messages first.
2. Accept the template wording for the out-of-window edge case, and show the well-worded
   in-window version during the demo.

For production you would register a real WhatsApp sender and submit proper Utility templates
in `en` and `zh_CN`. Draft them now so they're ready:

```
Name: medication_reminder    Category: UTILITY    Languages: en, zh_CN
en:     Hi {{1}}, it is time for your {{2}}. Please take {{3}}. Reply OK when done.
zh_CN:  {{1}}，该吃{{2}}了。请服用{{3}}。吃完请回复"好"。
```

### Media send rules

Non-negotiable, from Twilio's own documentation:

1. **A free-form media message cannot carry a caption.** If you pass `Body` alongside
   `MediaUrl`, the body is dropped and never delivered. Send text first, then media.
2. **One media object per message.** Extra `MediaUrl` params are ignored.
3. **Voice notes must be OGG with the Opus codec.** MP3 arrives as a downloadable file with
   no play button — useless for this user. Transcode:
   `ffmpeg -i in.mp3 -c:a libopus -b:a 24k -ar 48000 out.ogg`
4. **Size caps:** audio/video/document 16 MB, images 5 MB. Twilio will not transcode for you.
5. **Filenames ≤20 characters, ASCII letters/digits/`-`/`_`/`.` only.**
6. Twilio issues a `HEAD` to your `MediaUrl` and rejects the send if the `Content-Type` header
   doesn't match the actual file. Serve `audio/ogg` explicitly.
7. **Sandbox throttle: one message every three seconds.** A text-plus-voice reply is two
   messages, so budget 3s between them and never fire a burst of reminders.

### Status callbacks

`POST /webhooks/twilio/status` receives `MessageSid`, `MessageStatus`
(`queued|sent|delivered|read|failed|undelivered`), and `ErrorCode` on failure. Update the
`messages` row. Error codes worth handling explicitly:

| Code | Meaning | Action |
|---|---|---|
| 63016 | Free-form message outside the window | Re-route through the template path. Indicates a gateway bug. |
| 63007 | Recipient has not joined the sandbox | Alert the operator — the 3-day expiry has bitten. |
| 63005 | Media rejected | Check MIME, size, filename length. |
| 21610 | Recipient opted out (`STOP`) | Stop all sends to this user. Honour it. |
| 63018 | Rate limit | Back off; your throttle is too aggressive. |

## 3.5 Provider abstraction

Define `ChannelProvider` with `send_text`, `send_media`, `send_template`, `parse_inbound`.
Implement `TwilioWhatsAppProvider` now. It is roughly 150 lines to add a `MetaCloudProvider`
later, and doing so removes the custom-template restriction and the 3-day expiry. Worth the
interface; not worth building the second implementation for the demo.

## 3.6 Local development

Twilio must reach a public URL. Use `ngrok http 8000` and point the sandbox webhook at the
forwarding address. Re-point it whenever ngrok restarts (the free tier gives you a new
subdomain each time — a paid static domain or a Cloudflare Tunnel avoids this).

Keep a `scripts/fake_twilio_post.py` that replays realistic form payloads with valid
signatures against localhost, so you can develop all six input types without a phone.
