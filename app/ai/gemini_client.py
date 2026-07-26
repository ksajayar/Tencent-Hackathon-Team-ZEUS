import asyncio
import json
import random
import time
import uuid

from google import genai
from google.genai import types

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.ai_usage import AIUsage
from app.db.session import async_session

logger = get_logger(__name__)

TEXT_TIMEOUT_SECONDS = 15
MEDIA_TIMEOUT_SECONDS = 45
BATCH_TIMEOUT_SECONDS = 30  # text-only, but up to 25 items in and a JSON array out
MAX_ATTEMPTS = 3
RATE_LIMIT_RPM = 8  # comfortably under the ~10-15 RPM free-tier ceiling

_client = genai.Client(api_key=settings.gemini_api_key)


class _TokenBucket:
    """Local rate limiter so we fail slow (queue here) rather than get 429'd mid-demo."""

    def __init__(self, rate_per_minute: int) -> None:
        self._capacity = float(rate_per_minute)
        self._tokens = float(rate_per_minute)
        self._rate_per_second = rate_per_minute / 60.0
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            self._tokens = min(
                self._capacity, self._tokens + (now - self._last_refill) * self._rate_per_second
            )
            self._last_refill = now
            if self._tokens < 1:
                wait = (1 - self._tokens) / self._rate_per_second
                await asyncio.sleep(wait)
                self._tokens = 0
                self._last_refill = time.monotonic()
            else:
                self._tokens -= 1


_rate_limiter = _TokenBucket(RATE_LIMIT_RPM)


def _is_retryable(exc: Exception) -> bool:
    status = getattr(exc, "code", None)
    if isinstance(status, int):
        return status == 429 or 500 <= status < 600
    return exc.__class__.__name__ == "ServerError"


async def _log_usage(
    *,
    user_id: uuid.UUID | None,
    pipeline: str,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    latency_ms: int,
    outcome: str,
) -> None:
    try:
        async with async_session() as session:
            session.add(
                AIUsage(
                    user_id=user_id,
                    pipeline=pipeline,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    outcome=outcome,
                )
            )
            await session.commit()
    except Exception:
        logger.exception("ai_usage_log_failed")


async def _call_model(
    *,
    system_prompt: str,
    contents,
    pipeline: str,
    model: str,
    user_id: uuid.UUID | None,
    timeout_seconds: int,
):
    """Shared retry/timeout/rate-limit/usage-logging core - the only place
    that actually calls the Gemini SDK (§12: THE ONLY MODULE THAT CALLS
    GEMINI). Returns the raw response on success, None on any failure;
    callers extract .text themselves since text vs audio-transcription
    callers parse it differently."""
    await _rate_limiter.acquire()

    for attempt in range(1, MAX_ATTEMPTS + 1):
        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                _client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=types.GenerateContentConfig(system_instruction=system_prompt),
                ),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.warning("gemini_timeout", pipeline=pipeline, attempt=attempt)
            await _log_usage(
                user_id=user_id,
                pipeline=pipeline,
                model=model,
                input_tokens=None,
                output_tokens=None,
                latency_ms=latency_ms,
                outcome="timeout",
            )
            return None
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            if _is_retryable(exc) and attempt < MAX_ATTEMPTS:
                backoff = (2 ** (attempt - 1)) + random.uniform(0, 1)
                logger.warning(
                    "gemini_retry",
                    pipeline=pipeline,
                    attempt=attempt,
                    backoff_s=round(backoff, 2),
                    error_class=exc.__class__.__name__,
                )
                await asyncio.sleep(backoff)
                continue
            logger.error(
                "gemini_call_failed",
                pipeline=pipeline,
                attempt=attempt,
                error_class=exc.__class__.__name__,
            )
            await _log_usage(
                user_id=user_id,
                pipeline=pipeline,
                model=model,
                input_tokens=None,
                output_tokens=None,
                latency_ms=latency_ms,
                outcome="failed",
            )
            return None
        else:
            latency_ms = int((time.monotonic() - start) * 1000)
            usage = getattr(response, "usage_metadata", None)
            await _log_usage(
                user_id=user_id,
                pipeline=pipeline,
                model=model,
                input_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
                output_tokens=getattr(usage, "candidates_token_count", None) if usage else None,
                latency_ms=latency_ms,
                outcome="success",
            )
            return response

    return None


async def generate_text(
    *,
    system_prompt: str,
    user_content: str,
    pipeline: str,
    model: str | None = None,
    user_id: uuid.UUID | None = None,
) -> str | None:
    """The only function allowed to call Gemini for plain text generation.

    Returns None on any failure after retries/timeout — callers must have a
    degraded-mode fallback (every external call has a timeout and a fallback).
    """
    response = await _call_model(
        system_prompt=system_prompt,
        contents=user_content,
        pipeline=pipeline,
        model=model or settings.gemini_model_main,
        user_id=user_id,
        timeout_seconds=TEXT_TIMEOUT_SECONDS,
    )
    return response.text if response is not None else None


_TRANSCRIBE_PROMPT = (
    "Transcribe this voice message verbatim, keeping any mixed English/Mandarin "
    "words exactly as spoken - do not force it into one language. Respond with "
    "raw JSON only, no markdown fences, in exactly this shape: "
    '{"transcript": "...", "language": "en|zh-Hans|mixed", "confidence": 0.0-1.0}'
)


def _parse_transcription_json(raw: str) -> dict | None:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(cleaned)
        return {
            "transcript": str(data.get("transcript", "")).strip(),
            "language": data.get("language"),
            "confidence": float(data.get("confidence", 0.0)),
        }
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        logger.warning("gemini_transcription_parse_failed")
        return None


async def transcribe_audio(
    *,
    audio_bytes: bytes,
    mime_type: str,
    pipeline: str,
    model: str | None = None,
    user_id: uuid.UUID | None = None,
) -> dict | None:
    """§06 §6.1: one call transcribes AND detects language (en|zh-Hans|mixed)
    in the same pass - Gemini reads mixed-script speech in context instead of
    forcing single-language identification the way Whisper would.

    Returns {"transcript", "language", "confidence"} or None on failure/
    unparseable output - same degrade-with-fallback contract as generate_text.
    """
    audio_part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
    response = await _call_model(
        system_prompt="You are a precise speech transcription assistant.",
        contents=[_TRANSCRIBE_PROMPT, audio_part],
        pipeline=pipeline,
        model=model or settings.gemini_model_main,
        user_id=user_id,
        timeout_seconds=MEDIA_TIMEOUT_SECONDS,
    )
    if response is None or not response.text:
        return None
    return _parse_transcription_json(response.text)


_CLASSIFY_PROMPT = """Classify each email below for an elderly dementia patient's inbox. \
For each one decide:
- category: one of medical, family, appointment, admin, other. Use "medical" for hospitals, \
clinics, doctors, lab results, pharmacies.
- priority: 1-5. Use 5 only for something time-sensitive in the next 48 hours.
- needs_action: true if the patient or their caregiver needs to do something about it.
- summary_en: one plain-language sentence, no jargon.
- summary_zh: the same sentence in Simplified Chinese.

Never state a medication name or dose in a summary, even if the email mentions one - medication \
information only ever comes from a caregiver-verified record, never from an email.

Respond with raw JSON only, no markdown fences, as a JSON array in exactly this shape:
[{"id": "...", "category": "...", "priority": 1-5, "needs_action": true, \
"summary_en": "...", "summary_zh": "..."}]

Emails:
"""


def _parse_classification_json(raw: str) -> list[dict] | None:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(cleaned)
        if not isinstance(data, list):
            return None
        return [
            {
                "id": str(item.get("id", "")),
                "category": item.get("category") or "other",
                "priority": max(1, min(5, int(item.get("priority", 1)))),
                "needs_action": bool(item.get("needs_action", False)),
                "summary_en": str(item.get("summary_en", "")).strip(),
                "summary_zh": str(item.get("summary_zh", "")).strip(),
            }
            for item in data
        ]
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        logger.warning("gemini_classification_parse_failed")
        return None


_VISION_PROMPT = """Look at this photo for an elderly dementia patient's WhatsApp assistant. \
Classify it and extract what's useful, in one pass.

First decide "kind":
- "pill_bottle": a medicine bottle, blister pack, or pill box label.
- "prescription": a prescription slip, lab report, appointment letter, or discharge note.
- "document": any other printed or handwritten document, form, or letter.
- "scene": a place, street, room, or object - not a document, not a person.
- "person": a photo of one or more people.
- "other": anything that doesn't fit above.

If it is a pill_bottle or prescription, also extract the printed text VERBATIM in its original \
script - never translate or paraphrase it in that field, and never add anything that isn't \
printed on the label or page. Never state a dose, frequency, or instruction as advice to take -\
 only report what is printed as text.

For "person" images, describe generically ("two people smiling in a garden") - never attempt to \
identify anyone, even if they might be a known contact. For "scene" images, describe simply and \
concretely, e.g. what kind of place this looks like.

Respond with raw JSON only, no markdown fences, in exactly this shape:
{"kind": "pill_bottle|prescription|document|scene|person|other",
 "text_verbatim": "verbatim printed text, original script, empty string if none",
 "script": "han|latin|mixed|none",
 "structured": {"drug_name": null, "dose": null, "frequency": null},
 "description_en": "two or three simple, short sentences in English",
 "description_zh": "the same meaning, in Simplified Chinese",
 "confidence": 0.0-1.0}
"""


def _parse_vision_json(raw: str) -> dict | None:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(cleaned)
        structured = data.get("structured") or {}
        return {
            "kind": data.get("kind") or "other",
            "text_verbatim": str(data.get("text_verbatim", "")).strip(),
            "script": data.get("script") or "none",
            "structured": {
                "drug_name": structured.get("drug_name"),
                "dose": structured.get("dose"),
                "frequency": structured.get("frequency"),
            },
            "description_en": str(data.get("description_en", "")).strip(),
            "description_zh": str(data.get("description_zh", "")).strip(),
            "confidence": float(data.get("confidence", 0.0)),
        }
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        logger.warning("gemini_vision_parse_failed")
        return None


async def analyze_image(
    *,
    image_bytes: bytes,
    mime_type: str,
    location_hint: str | None = None,
    pipeline: str,
    model: str | None = None,
    user_id: uuid.UUID | None = None,
) -> dict | None:
    """§05 §5.3 + §06 §6.3/§6.4: one call classifies AND extracts - never a
    second call for OCR, per the one-AI-call-per-message rule (§05 §5.7).
    `location_hint` folds in the most recent location_ping (if <1h old) as
    optional scene context, rather than a separate call after the fact.

    Returns the parsed dict or None on failure/unparseable output - callers
    must have a degraded-mode fallback.
    """
    prompt = _VISION_PROMPT
    if location_hint:
        prompt += (
            f'\nThe patient\'s last known location (within the past hour): "{location_hint}". '
            "Only use this if the photo looks like it could be taken there; ignore it otherwise."
        )
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    response = await _call_model(
        system_prompt=(
            "You are a careful vision assistant for an elderly dementia patient. You never "
            "diagnose, never confirm a photographed medicine as the patient's own prescribed "
            "medication, and never identify people from photos."
        ),
        contents=[prompt, image_part],
        pipeline=pipeline,
        model=model or settings.gemini_model_main,
        user_id=user_id,
        timeout_seconds=MEDIA_TIMEOUT_SECONDS,
    )
    if response is None or not response.text:
        return None
    return _parse_vision_json(response.text)


_DOCUMENT_PROMPT = """This PDF was sent by an elderly dementia patient over WhatsApp. Summarise \
it for the patient and their caregiver.

Identify what kind of document this is, who it is from, and any key dates. Write the summary at \
a logistics level only - what the document is and what the patient is being asked to do (e.g. \
"this is a prescription from Dr Tan dated 12 July - please check with your caregiver"). Never \
extract medication names, doses, or instructions as actionable advice to take; if the document \
mentions medicine, say only that it mentions medicine and that a caregiver should check it.

Respond with raw JSON only, no markdown fences, in exactly this shape:
{"doc_kind": "prescription|lab_report|appointment_letter|discharge_note|other",
 "extracted_text": "the document's text content, verbatim, truncated if very long",
 "summary_en": "three to five short, simple sentences in English",
 "summary_zh": "the same meaning, in Simplified Chinese"}
"""


def _parse_document_json(raw: str) -> dict | None:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(cleaned)
        return {
            "doc_kind": data.get("doc_kind") or "other",
            "extracted_text": str(data.get("extracted_text", "")).strip(),
            "summary_en": str(data.get("summary_en", "")).strip(),
            "summary_zh": str(data.get("summary_zh", "")).strip(),
        }
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        logger.warning("gemini_document_parse_failed")
        return None


async def summarize_document(
    *,
    pdf_bytes: bytes,
    pipeline: str,
    model: str | None = None,
    user_id: uuid.UUID | None = None,
) -> dict | None:
    """§05 §5.4: sends the PDF bytes directly as a document part - Gemini
    reads scanned pages as images in the same call, so there's no separate
    OCR step. Returns None on failure; callers fall back to a pypdf text
    extract (app/vision/pdf.py) per the degraded-mode contract.
    """
    pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
    response = await _call_model(
        system_prompt=(
            "You are a careful document-summarising assistant for an elderly dementia "
            "patient's caregiver-monitored WhatsApp assistant."
        ),
        contents=[_DOCUMENT_PROMPT, pdf_part],
        pipeline=pipeline,
        model=model or settings.gemini_model_main,
        user_id=user_id,
        timeout_seconds=MEDIA_TIMEOUT_SECONDS,
    )
    if response is None or not response.text:
        return None
    return _parse_document_json(response.text)


async def classify_emails(
    *,
    emails: list[dict],
    pipeline: str,
    model: str | None = None,
    user_id: uuid.UUID | None = None,
) -> list[dict] | None:
    """§04 §4.2: one call classifies up to 25 emails at once, not one call
    per email - this is what keeps email summaries pre-computed at sync
    time so 'any important emails?' costs zero AI calls at read time.

    `emails` items: {"id", "from", "subject", "snippet"}. Returns a list of
    {"id","category","priority","needs_action","summary_en","summary_zh"}
    or None on failure - callers must have a degraded-mode fallback.
    """
    if not emails:
        return []

    prompt = _CLASSIFY_PROMPT + json.dumps(emails, ensure_ascii=False)
    response = await _call_model(
        system_prompt=(
            "You are a careful email triage assistant for an elderly dementia "
            "patient's caregiver-monitored inbox."
        ),
        contents=prompt,
        pipeline=pipeline,
        model=model or settings.gemini_model_main,
        user_id=user_id,
        timeout_seconds=BATCH_TIMEOUT_SECONDS,
    )
    if response is None or not response.text:
        return None
    return _parse_classification_json(response.text)
