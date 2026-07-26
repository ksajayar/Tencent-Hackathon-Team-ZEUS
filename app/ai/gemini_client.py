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
