# 06 — Speech, Vision, OCR & Multilingual

Covers brief sections 10, 11, 12, 13 and 15.

## 6.1 Speech-to-text (§10)

### Why not Whisper

Whisper assigns **one language per utterance**. Real Singaporean bilingual speech does this:

> "我今天要去看 Dr Tan，然后 buy some groceries."

Whisper either transcribes it as Mandarin and garbles the English, or picks English and loses
the Mandarin. Gemini's audio understanding keeps both, because it isn't doing forced language
identification — it's reading the audio in context. Given that mixed English + Chinese
conversation is an explicit requirement in your brief, this decides the choice on its own.

Secondary reasons: no second service, no model download, no CPU inference on Railway.

### Language detection

Done inside the transcription call. Ask for JSON:

```json
{"transcript": "...", "language": "en|zh-Hans|mixed", "confidence": 0.0-1.0}
```

`mixed` is a first-class value, not an error. If confidence < 0.6, fall back to the user's
`preferred_language` and log it — repeated low confidence usually means the audio preprocessing
needs tuning, not that the model is wrong.

### Audio preprocessing

WhatsApp voice notes arrive as OGG/Opus, typically 16 kHz mono already. Still normalise:

```
ffmpeg -i in.ogg -ar 16000 -ac 1 \
  -af "highpass=f=80, lowpass=f=8000, afftdn=nf=-25, loudnorm=I=-16:TP=-1.5:LRA=11" \
  -f wav out.wav
```

| Filter | Why it matters here |
|---|---|
| `highpass=80` | Removes handling rumble and fan noise. |
| `lowpass=8000` | Speech lives below this; everything above is hiss. |
| `afftdn` | Gentle spectral denoise. Don't push it — aggressive denoising destroys quiet elderly speech. |
| `loudnorm` | Consistent level. Older users hold the phone further away and speak softly. |

Guards: reject <0.5s (accidental tap) and >60s. Log duration — if most notes are near the cap,
the patient is struggling with the format and the prompt should ask for shorter messages.

### Noise handling

Do not attempt aggressive noise suppression. In testing, the failure mode that hurts is
over-filtering a soft voice into silence, not background noise confusing the model. If a
transcript comes back empty or nonsense, ask the user to repeat — do not guess.

---

## 6.2 Text-to-speech (§11)

### Provider: `edge-tts`

Free, no key, no quota, and the best Mandarin voices available at zero cost.

| Language | Voice | Character |
|---|---|---|
| Simplified Chinese | `zh-CN-XiaoxiaoNeural` | Warm, clear, well-paced. Default. |
| Simplified Chinese | `zh-CN-YunxiNeural` | Male alternative. |
| English (Singapore) | `en-SG-LunaNeural` | Local accent. Noticeably more comfortable for a Singaporean listener. Default. |
| English (Singapore) | `en-SG-WayneNeural` | Male alternative. |
| English (US) | `en-US-AriaNeural` | Fallback if `en-SG` is unavailable. |

Tune for the audience — slower than default, unhurried:

```
rate="-15%"    pitch="+0Hz"    volume="+0%"
```

Test the rate with someone over 70 before committing. −15% is a starting point, not a finding.

### Cost comparison

| Provider | Cost | Mandarin quality | Reliability | Verdict |
|---|---|---|---|---|
| **edge-tts** | FREE, unlimited | Very good | **Unofficial — can break** | Chosen |
| Google Cloud TTS | FREE TIER (~1M WaveNet chars/mo) then paid | Very good | Official, stable | **Fallback.** Build the interface for it. |
| gTTS | FREE | Mediocre, robotic | Unofficial | Last resort |
| Piper (self-hosted) | FREE | Weak Mandarin | Fully offline, yours | No |
| OpenAI TTS | ~$15/1M chars | Good | Official | Only if already on OpenAI |
| ElevenLabs | Paid | Excellent | Official | Out of budget |

Define `TTSProvider` with one method, `synthesize(text, language, voice) -> Path`. Implement
`EdgeTTSProvider` now and `GoogleCloudTTSProvider` as a stub. If edge-tts breaks the week of
the demo, it's a config flip rather than a rewrite. **This is the single most likely
third-party failure in the system.** A pre-demo smoke test on the TTS path is on the checklist.

### WhatsApp audio format

Twilio's rule, verbatim in effect: only OGG with the **Opus** codec renders as a playable voice
note. MP3 arrives as a downloadable file attachment — which a dementia user will not open.

edge-tts outputs MP3, so transcoding is mandatory:

```
ffmpeg -i tts.mp3 -c:a libopus -b:a 24k -ar 48000 -ac 1 voice.ogg
```

24 kbps Opus is transparent for speech and keeps files tiny. Then:

- Serve from `GET /media/{signed_token}` with `Content-Type: audio/ogg` (Twilio HEADs it first).
- Filename ≤20 ASCII characters.
- Cache by `sha256(text + voice + rate)` — repeated reminders reuse the same file, which
  matters when the patient asks the same question five times.

### Reply modes

`user_preferences.reply_mode` ∈ `{text, audio, both}`. Default `both` — text for a caregiver
looking over the shoulder, audio for the patient. Changeable in either language:
"speak to me" / "只发文字" / "voice off".

---

## 6.3 OCR (§12)

**Provider: Gemini vision.** No Tesseract, no PaddleOCR.

Rationale: Tesseract's `chi_sim` is weak on real-world photos — angled, shadowed, curved
labels on pill bottles. PaddleOCR is much better at Chinese but is over a gigabyte of
dependencies, needs CPU inference time on Railway, and still needs a second model to interpret
what it read. Gemini reads and interprets in one pass, handles both scripts natively, and is
already a dependency.

### Preprocessing

Modest, and less than you'd need for classical OCR:

1. Auto-rotate from EXIF, then **strip EXIF** (it contains GPS).
2. Downscale long edge to ≤1568px.
3. Convert to RGB JPEG, quality 85.
4. Only if the image is visibly dark or low-contrast: CLAHE via Pillow/OpenCV. Do not
   binarise — Gemini does better on the original than on a thresholded bitmap. This is the
   opposite of Tesseract practice and catches people out.

### Extraction contract

Always ask for verbatim text **in the original script**, plus a separate translation field.
Never let the model silently translate during extraction — you lose the ability to show the
patient what the label actually says.

```json
{"text_verbatim": "原文，不翻译",
 "script": "han|latin|mixed",
 "translation_en": "...",
 "structured": {"drug_name": null, "dose": null, "frequency": null},
 "confidence": 0.0-1.0}
```

### Medical documents

Prescriptions, lab reports, appointment letters, hospital discharge notes. Two rules:

1. Extraction is fine. **Acting on the extraction is not.** Everything medical goes to
   `medication_candidates` or `documents` for caregiver review.
2. If `confidence < 0.8` on a medication field, do not show the value at all. Say "I can't
   read this clearly — please ask your caregiver." A confidently wrong drug name read aloud
   to someone who cannot check it is the worst outcome this system can produce.

---

## 6.4 Vision AI (§13)

Same model, different prompt. Four scenarios:

### Pill bottle recognition
Extract the label. Return name, strength, and the printed instruction as *text on the label* —
never as guidance. Write to `medication_candidates`. Reply confirms what was seen and that a
caregiver will check it. Do not match against the `medications` table and announce "yes, this
is your morning pill" — that is a diagnosis-shaped claim from a photo, and a similar-looking
bottle would produce a dangerous confirmation.

### Prescription reading
Full text extraction, structured fields, `documents` row, caregiver-review path. Summarise what
kind of document it is and what it asks the patient to do at a logistics level ("this is a
prescription from Dr Tan dated 12 July"), not at a clinical level.

### Scene understanding
"Where am I?" / "what is this?" — describe the scene in two or three simple sentences. Combine
with the most recent `location_ping` if one exists within the last hour: *"This looks like a
bus stop. You're near Bedok Interchange."* Genuinely useful for disorientation and it demos well.

### People
Describe generically: "two people smiling in a garden." **Never attempt identification**, even
against the `contacts` table. Face recognition on a patient's photos is a privacy line that a
hackathon demo has no business crossing, and a wrong "that's your daughter" is cruel.

---

## 6.5 Multilingual handling (§15)

### Detection

| Layer | Method |
|---|---|
| Text | Script detection on codepoints. CJK present → `zh-Hans`; both scripts in meaningful proportion → `mixed`. |
| Voice | Returned by the transcription call. |
| Image/PDF | `script` field from the extraction contract. |

Store `detected_language` on every `messages` row. It is a per-message property, never a
per-user setting (`CLAUDE.md` LANG-1).

### Reply language

Reply in the language of the message just received. If `mixed`, reply in the dominant script
but keep English proper nouns and medical terms in English — "Dr Tan", "Panadol" — because
that is how the patient will recognise them, and translating them harms comprehension.

Explicit override always wins: "speak English" / "讲华语" writes `preferred_language` and
pins replies until changed back.

### Context preservation across a switch

The conversation history is stored **as sent**, in whatever language each turn used. It is not
normalised to one language. The model sees the real bilingual history and handles the
continuity naturally:

```
turn 1  (zh)  "我明天有什么事?"
turn 2  (zh)  "您明天上午10点要看陈医生。"
turn 3  (en)  "what time again?"
turn 4  (en)  "Your appointment with Dr Tan is at 10 o'clock tomorrow morning."
```

Turn 4 must resolve "what time again?" against a Mandarin turn. Passing the history unmodified
is what makes this work. Translating history to a canonical language before prompting is the
common mistake and it loses referents.

### Translation

Not a separate service. Two places translation happens at all:

1. Pre-computed `summary_en` / `summary_zh` on cached emails and documents, so either question
   is answered instantly.
2. On explicit request: "what does this say in English?"

There is no translation layer between the user and the model. The model generates directly in
the target language, which is better than generating in English and translating.

### Simplified vs Traditional

Your requirement is **Simplified** (`zh-Hans`). Accept Traditional input gracefully — some
older Singaporeans read it — but always **reply in Simplified**. Pin it in the system prompt;
models drift to Traditional otherwise, especially on names and medical terms.

### Known limitations

- **Dialect.** Many elderly Singaporeans speak Hokkien, Teochew, or Cantonese, not Mandarin.
  None are supported by any of this. Say so plainly if a judge asks — it is the most likely
  hard question you'll get, and "we support Mandarin and English; dialect support would need a
  different STT approach" is a much better answer than improvising.
- **Slurred or halting speech.** Dementia affects speech production. Transcription accuracy
  degrades and there is no easy fix at this scale.
- **Number and date formats.** 上午/下午 vs AM/PM, and Chinese numerals in dates. Render times
  through an explicit localisation helper, not by asking the model to format them.
- **Simplified-language register is harder in Chinese.** The "short sentences, plain words"
  constraint is well-understood by models in English and less reliably applied in Chinese.
  Test the Chinese output separately; do not assume the English quality transfers.
