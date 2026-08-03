# 17 — Caregiver Commands (specification, not yet built)

Execution spec for the caregiver command surface: pairing a caregiver to a patient over
WhatsApp, and the `set …` commands that let the caregiver write the patient's trusted data.

**Nothing in this document is implemented.** Every section below states what exists today
(with file references) before stating what must be added. Read §2 and §3 before writing code —
three decisions block implementation and two of them contradict a currently-documented
invariant.

---

## 1. Audit — what exists today

### 1.1 Answering the two direct questions

| Asked | Status | Evidence |
|---|---|---|
| Can the patient ask for her **blood type / bloodwork**? | **No. Nothing exists.** The string `blood` does not appear anywhere in `app/`, `alembic/`, or `docs/`. No `doc_kind='blood_work'`, no storage path, no query intent. | repo-wide grep |
| Can the patient ask for her **home address**? | **No. Nothing exists.** `safe_zones` stores `center_lat`/`center_lon`/`radius_m` but has **no address column**. `location_pings.address` exists as a column but is never written — only read, always falling back to `f"{lat}, {lon}"`. There is no geocoder and no "where is my home" intent. | `app/db/models/location.py:46`, `app/safety/sos.py:69`, `app/pipelines/location.py:95` |

### 1.2 A photographed lab report today becomes a medication candidate

This is the trap for `set bloodwork`. `app/ai/gemini_client.py:302` defines `"prescription"` as
*"a prescription slip, **lab report**, appointment letter, or discharge note"*, and
`app/pipelines/image.py:148` routes `kind == "prescription"` to
`candidates_service.create_candidate()` — a **`medication_candidates`** row — and replies
`PRESCRIPTION_SAVED`. It never writes to `documents`.

So `set bloodwork` cannot reuse the normal image path. It must force the document branch.

### 1.3 What can be reused

| Need | Already exists |
|---|---|
| Multi-step "waiting for the caregiver's next message" state | `messages.meta` + a time window. `app/pipelines/contact.py:64` writes `meta={"pending_emergency_contact_id": …}`; `app/pipelines/text.py:220` (`_confirm_emergency_contact`) reads it back with a 15-minute expiry. **No new state table needed — copy this pattern.** |
| Commands working by voice as well as text | Free. `app/pipelines/voice.py:118` transcribes then calls `text_pipeline.handle()` with the transcript, so any intent added to `_generate_reply` is voice-capable automatically. |
| Phone number from a shared WhatsApp contact card | `app/services/contacts.py:21` `parse_vcard()` + `upsert_from_vcard()`, routed by `inbound.kind == "contact"` (`text/vcard`, `app/channels/inbound.py:18`). |
| Caregiver role | `users.role` with `CHECK (role IN ('patient','caregiver'))` — `app/db/models/user.py:15`. Seed already creates one (`app/services/seed.py:63`). |
| Reminders from a new medication | Free. `sync_medication_reminders()` (`app/services/medications.py:69`) reconciles `reminders` from the `medications` table on a schedule — insert a verified medication row and its reminder appears. |
| Telling the patient about a new appointment outside the 24h window | `outbound.send_reminder()` (`app/channels/outbound.py:89`) already falls back to the approved appointment template (`{{1}}`=when, `{{2}}`=what). |
| Day + time phrasing for the notice | `calendar_service.render_when()` / `render_time_of_day()` (`app/services/calendar.py:49`). |

### 1.4 What is missing entirely

- **No caregiver→patient link.** No `caregiver_links` table. Its absence is called out
  deliberately in three places: `app/db/models/contact.py:13`, `app/db/models/medication.py:50`,
  `app/jobs/ack_watchdog.py:17`.
- **No role branch in routing.** `app/api/webhooks.py:46` creates every inbound sender via
  `get_or_create_user()`, which takes the `role` server default of `'patient'`.
  `app/pipelines/router.py` and `text.py::_generate_reply` never inspect `role`.
- **`documents` has no owner.** `app/db/models/document.py` links only `media_id` →
  `media_files.message_id` → `messages`. A document uploaded by the *caregiver* is attached to
  the *caregiver's* message chain, so a patient-side "what's my bloodwork" cannot find it.

---

## 2. Blocking decisions

### D1 — Google Calendar is read-only. `set appointment` requires changing that.

`app/google/oauth.py:13` states, as a design property:

> Read-only, minimum viable (§02, §04). **No code path in this system can send, delete, or
> modify anything in a Google account.**

`SCOPES` contains `calendar.readonly`. Writing an event needs `calendar.events`, and **every
already-connected account must re-run the OAuth consent flow** — existing refresh tokens do not
gain scopes retroactively. Docs §02 and §04 both assert read-only and would need amending.

Two options:

- **D1-a — Widen the scope.** Real Google write. Costs: scope change, re-consent for every
  connected user mid-demo, an invariant rewritten in three docs, and a new failure mode
  (a write that 403s).
- **D1-b — Local-only event (recommended for the demo).** Insert directly into
  `calendar_events` with `google_event_id = f"local:{uuid4()}"`. The patient-facing result is
  identical: `get_schedule_window()` reads the table, not Google. Safe because
  `sync_user_calendar()` (`app/google/calendar.py:163`) only **adds and updates** by
  `google_event_id` — it never deletes rows absent from Google — so a locally-created event
  survives every subsequent sync. Keeps the read-only invariant intact.

**Pick one before writing `set appointment`.** The rest of the command is identical either way.

### D2 — `documents` needs an owner column

Required for `set bloodwork` + "what's my bloodwork". Add `patient_id UUID NULL REFERENCES
users(id) ON DELETE CASCADE` to `documents`, set on every write. Nullable so the existing
patient-upload paths (`pipelines/document.py:116`, `pipelines/image.py:155`) can be backfilled
to pass it without breaking old rows.

Rejected alternative: joining `documents → media_files → messages → conversations → user_id`.
It works for patient uploads but returns the **caregiver** for caregiver uploads, which is
exactly the case being built.

### D3 — `set medication` and SAFETY-1

SAFETY-1 says medication data comes from the `medications` table and *"only a caregiver action
promotes a candidate."* A verified caregiver typing `set medication` **is** that caregiver
action, so this is permitted — but only if:

- `verified_by` = the caregiver's `users.id` and `verified_at` = now. A row with
  `verified_by IS NULL` is invisible to `get_active_medications()` and the reminder scheduler
  (`app/services/medications.py:16`, `:85`).
- The write happens **only** on the caregiver's explicit confirmation of parsed values —
  never straight from a model's parse.
- OCR still never writes here. A photo sent during `set medication` goes to
  `medication_candidates`, unchanged.

`medications` has four NOT NULL fields that the caregiver's free text must be turned into:
`dose_text`, `schedule_rrule` (an RRULE — "twice a day after meals" must become
`FREQ=DAILY;BYHOUR=8,20` or similar), `instruction_en` **and** `instruction_zh` (both required,
so both languages must be generated).

---

## 3. Schema changes

One migration, `m10_caregiver`:

```
caregiver_links          NEW
  id, caregiver_id FK users, patient_id FK users,
  status text CHECK (status IN ('pending','active','revoked')) default 'pending',
  created_at, confirmed_at
  UNIQUE (caregiver_id, patient_id)

documents                ALTER
  + patient_id UUID NULL REFERENCES users(id) ON DELETE CASCADE
  + INDEX (patient_id, doc_kind)

safe_zones               ALTER
  + address text NULL          -- the human-readable home address
```

`doc_kind` is plain `Text` with **no CHECK constraint** (`app/db/models/document.py:27`,
migration `m8` line 37), so `'blood_work'` needs no schema change.

---

## 4. Pairing — `connect caregiver`

Today `connect caregiver` matches no intent (`_CONNECT_GOOGLE_RE` is the literal
`connect google`) and falls through to a free-text Gemini call, which will improvise a
confirmation for something that did not happen. See §1.4.

**Flow** (state carried on `messages.meta`, per §1.3):

1. Patient sends `connect caregiver`.
2. Bot: *"Who looks after you? Send me their phone number, or share their contact card."*
   Outbound `meta={"pending_caregiver_link": true}`, 15-minute window.
3. Patient replies with any of:
   - **text** — parse an E.164-ish number out of the body;
   - **audio** — already handled: the transcript re-enters `_generate_reply` (§1.3), so the
     same text parse applies. Digits from STT are unreliable — always echo the parsed number
     back for confirmation;
   - **contact card** — `inbound.kind == "contact"` → `parse_vcard()`. `contact.py` must check
     for a pending caregiver link *before* its existing emergency-contact question.
4. Bot echoes the number and asks the patient to confirm (reuse `_YES_RE`/`_NO_RE`).
5. On yes: store the caregiver in `contacts` and insert `caregiver_links` with
   `status='pending'`. **Do not attempt any send to the caregiver here** — see below.

### 4.1 Pairing is two-phase, and phase 2 cannot be skipped

Storing the caregiver's number does **not** make them reachable, for two independent reasons.
(This project has since moved off the Twilio sandbox onto a purchased number connected to a
Meta Business Account — see `docs/03` — which removes the join-code problem this section
originally described. Reason 1 below is the platform rule that replaces it; reason 2 is
unaffected either way.)

1. **The 24-hour window still applies.** Meta's platform rule, not a sandbox one: a recipient
   who has never messaged this number cannot receive a free-form send, only an approved
   template (`docs/03` §3.4). A template *could* be submitted and used to send an unsolicited
   intro, but the simpler choice taken here is to not bother — wait for the caregiver to message
   first, same as the sandbox-era design, just for a different underlying reason now.
2. **A `contacts` row is not a `users` row.** `outbound.send_text()` takes a `User` and needs
   `user.phone_e164` plus `window_open(user)`; the caregiver commands additionally need a
   `conversations` row and `messages.meta` to carry multi-step state (§1.3). A contact has
   none of these. `send_urgent(phone, body)` does send to a bare number — SOS uses it that way
   (`app/channels/outbound.py:179`) — but it deliberately persists **no `messages` row**, so
   it cannot carry `meta` and cannot drive `set bloodwork`'s multi-turn intake.

So:

| Phase | Trigger | Effect |
|---|---|---|
| **1 — nominate** | Patient sends the number or contact card | `contacts` row + `caregiver_links` with `status='pending'`. Nothing is sent to the caregiver, so nothing can fail. Tell the patient: *"I've saved {name}. Ask them to message me and I'll set them up."* |
| **2 — activate** | Caregiver messages the bot for the first time | The webhook's `get_or_create_user` (`app/services/conversation.py:14`) already creates their `users` row. Match its `phone_e164` against pending links → set `role='caregiver'`, flip the link to `'active'`, send the onboarding message. |

Phase 2 is where the design works in your favour: because the caregiver has just sent an
inbound message, the 24-hour window is open, so the onboarding send is plain free-form text —
no template, no queue, nothing to drop.

**Matching pitfall.** The link is resolved by phone number, but `_normalize_phone()`
(`app/services/contacts.py:16`) only strips non-digits — a vCard saved as `9123 4567` will
not match Twilio's `+6591234567`. Normalize to E.164 with a default country code before
comparing. Note also that `users.phone_e164` has **no unique constraint** (only `wa_id` does,
`app/db/models/user.py:22`), so the lookup must tolerate more than one row.

**Caregiver onboarding message** (sent once, at phase 2):

> You are now set up as {patient_name}'s caregiver. You can send me:
> • *set bloodwork* — add her latest blood test
> • *set appointment* — add an appointment to her calendar
> • *set address* — save her home address
> • *set medication* — add a medicine and when to take it
> Send any of these and I will ask you for what I need.

Keep it to those four lines.

**Demo-morning checklist:** the caregiver must message the bot at least once before any
caregiver command is expected to work — no join step, no expiry, but the "someone has to speak
first" ordering is still real.

---

## 5. Command routing

Add a role branch at the **top** of `text.py::_generate_reply`, before the existing intents:

```
if user.role == "caregiver" and has_active_link(user):
    caregiver_reply = await caregiver.handle_command(...)
    if caregiver_reply is not None:
        return caregiver_reply
    # not a caregiver command — fall through
```

Rules:

- **SOS stays first.** `sos.is_sos_trigger()` runs in `handle()` before any of this
  (`text.py:330`) and must not move — SAFETY-2.
- Return `None`, not a fallback string, when the text is not a caregiver command, so ordinary
  conversation still works for the caregiver. Same contract as `_ack_reminder`.
- Every command targets the linked **patient's** `user_id`, never the caregiver's own.
- An unlinked `role='caregiver'` user gets the normal patient-facing behaviour.

New module: `app/pipelines/caregiver.py`. New strings go in `app/i18n/strings.py`, bilingual
(EN + `zh-Hans`) like every other entry.

---

## 6. Commands

### 6.1 `set bloodwork`

**Prompt:** *"Please send {patient_name}'s bloodwork — you can type it, send a photo, or send
a document."* Set `meta={"pending_caregiver_intake": "blood_work"}`.

**Intake** — accepts, and keeps accepting until the caregiver says `done` (multiple uploads are
expected):

| Input | Handling |
|---|---|
| text | store verbatim as `extracted_text`; no media row |
| image | `pipelines/image.py` path, but **bypass `_route_by_kind`** (§1.2) — go straight to `documents_service.create_document()`. Otherwise a lab report silently becomes a `medication_candidate`. |
| PDF | `pipelines/document.py` path, forcing the doc_kind |

**Storage:** `documents` with `doc_kind='blood_work'`, `patient_id` = the linked patient (D2),
`media_id` = the stored `media_files` row, `extracted_text` = OCR/verbatim text,
`summary_en`/`summary_zh` = plain-language summary, `was_scanned` from the PDF probe.

**Guard:** blood results routinely name medications. The summary must not restate a drug name
or dose — the same rule `_CLASSIFY_PROMPT` already applies to emails
(`app/ai/gemini_client.py:269`). Reuse that wording.

### 6.2 `set appointment`

**Prompt:** ask for date, time, location, and purpose — all four. Re-prompt for whichever is
missing rather than defaulting.

**Storage:** per **D1**. Either a real Google `events.insert` (D1-a) or a `calendar_events` row
with `google_event_id = "local:…"` (D1-b). Store UTC (`TIMESTAMPTZ`), rendered from the
patient's `timezone`.

**Notify the patient:**

> A new appointment has been added: {purpose}, {when}, at {location}.

`{when}` comes from `calendar_service.render_when()` — day-relative, spoken time, never
`14:00` (see §16 voice rules). If the patient is outside the 24-hour window, send via
`outbound.send_reminder()`'s template fallback (`{{1}}`=when, `{{2}}`=purpose) rather than
dropping it.

**Confirm to the caregiver** separately — the caregiver must know the patient was told.

### 6.3 `set address`

**Prompt:** *"What is {patient_name}'s home address?"*

**Storage:** `safe_zones` row with `kind='home'`, new `address` column (§3).

**Known limitation — state it in the reply, do not paper over it:** there is no geocoder in
this system (§1.1). A text address yields **no** `center_lat`/`center_lon`, so it cannot arm
the geofence that `geo.match_safe_zone()` needs — it only answers "where is my home". To also
set the geofence, the caregiver must share a **location pin** from the home, which gives real
coordinates. Ask for the pin as an optional second step.

If the home zone must exist before a pin arrives, `center_lat`/`center_lon`/`radius_m` are
NOT NULL — either make them nullable in the same migration or defer the row until the pin
arrives. Deferring is simpler; store the address on the `users` row in that case.

### 6.4 `set medication`

**Prompt:** ask for the medicine name, the dose, and when to take it — explicitly all three
(D3 lists the NOT NULL columns).

**Parse → confirm → write.** Parse the free text into `{name, dose_text, schedule_rrule,
instruction_en, instruction_zh}`, echo the parsed values back in plain language
(*"Panadol, 1 tablet, every morning at 8 — is that right?"*), and write **only** on an explicit
yes. Never write straight from the parse.

**Storage:** `medications`, with `verified_by` = caregiver id and `verified_at` = now (D3).
The reminder appears on its own via `sync_medication_reminders()` — do not create a `reminders`
row by hand.

---

## 7. Patient-side queries

Add these as deterministic intents in `_generate_reply`, **before** `_general_qa` — same shape
as `_medication_query` (`text.py:182`), no LLM in the answer path where the data is a stored
fact.

| Trigger (EN / zh-Hans) | Answer |
|---|---|
| "what's my bloodwork", "my blood test", "血检", "验血报告" | Latest `documents` row for the patient with `doc_kind='blood_work'` → its `summary_en`/`summary_zh`. If none: *"I don't have your blood test results. I can ask your caregiver."* |
| "what's my blood type", "我的血型" | Not a separate field — it lives inside the bloodwork text. Either extract `blood_type` at `set bloodwork` time into a dedicated column, or answer from the bloodwork summary. **Extraction at write time is the safer of the two** — a per-question regex over `extracted_text` will eventually return the wrong line. |
| "where's my home", "what's my address", "我家在哪" | `safe_zones.address` where `kind='home'`, else the `users` fallback (§6.3). If none: offer to ask the caregiver. |

All three must end with a small reassurance, per the persona rules.

---

## 8. Files to touch

```
alembic/versions/…_m10_caregiver.py     NEW   caregiver_links, documents.patient_id, safe_zones.address
app/db/models/caregiver.py              NEW   CaregiverLink
app/db/models/document.py               EDIT  patient_id
app/db/models/location.py               EDIT  SafeZone.address
app/pipelines/caregiver.py              NEW   command router + the four handlers
app/services/caregiver.py               NEW   link lookup, get_linked_patient()
app/services/documents.py               EDIT  patient_id on create; latest_by_kind() query
app/pipelines/text.py                   EDIT  role branch; three patient query intents
app/pipelines/contact.py                EDIT  check pending caregiver link before the emergency question
app/pipelines/image.py                  EDIT  honour a pending intake kind, bypass _route_by_kind
app/pipelines/document.py               EDIT  same
app/i18n/strings.py                     EDIT  all new strings, EN + zh-Hans
app/google/oauth.py                     EDIT  only under D1-a
app/services/calendar.py                EDIT  create_local_event() under D1-b
docs/04, docs/16                        EDIT  only under D1-a (read-only claim)
```

---

## 9. Invariant checklist

Verify each before merging:

- **SAFETY-1** — `set medication` writes only on explicit caregiver confirmation, with
  `verified_by` set. OCR still writes to `medication_candidates` only. Bloodwork summaries
  restate no drug name or dose.
- **SAFETY-2** — the SOS check stays first in `handle()`. No caregiver command precedes it.
- **CHANNEL-1** — every send goes through `app/channels/outbound.py`, including sends to the
  caregiver.
- **CHANNEL-2** — text and media stay separate messages.
- **WEBHOOK-1** — all of this runs in the background task, never in the webhook handler.
- **DATA-2** — log `user_id`, command name, outcome. Never the address, the bloodwork text,
  the medication name, or the caregiver's phone number.
- **LANG-1** — caregiver commands are matched in both languages, and the caregiver is replied
  to in the language they wrote in, independently of the patient's.

---

## 10. Out of scope

Not part of this spec, and each contradicts CLAUDE.md's "what not to build": a caregiver web
dashboard, caregiver-side auth beyond the WhatsApp pairing, multi-patient caregivers,
an audit-log UI, and editing or deleting data the caregiver previously set (`set` is
create/replace only — deletion needs a separate decision on what happens to the reminders
already generated from a medication row).
