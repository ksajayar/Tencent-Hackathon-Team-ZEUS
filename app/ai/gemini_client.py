import asyncio
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
    resolved_model = model or settings.gemini_model_main
    await _rate_limiter.acquire()

    for attempt in range(1, MAX_ATTEMPTS + 1):
        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                _client.aio.models.generate_content(
                    model=resolved_model,
                    contents=user_content,
                    config=types.GenerateContentConfig(system_instruction=system_prompt),
                ),
                timeout=TEXT_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.warning("gemini_timeout", pipeline=pipeline, attempt=attempt)
            await _log_usage(
                user_id=user_id,
                pipeline=pipeline,
                model=resolved_model,
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
                model=resolved_model,
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
                model=resolved_model,
                input_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
                output_tokens=getattr(usage, "candidates_token_count", None) if usage else None,
                latency_ms=latency_ms,
                outcome="success",
            )
            return response.text

    return None
