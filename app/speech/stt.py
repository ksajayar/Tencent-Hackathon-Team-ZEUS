import asyncio
import uuid
from pathlib import Path

from app.ai import gemini_client
from app.speech.audio import preprocess_for_stt


def _read_and_delete(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    finally:
        Path(path).unlink(missing_ok=True)  # scratch file, not the stored original


async def transcribe(raw_path: str, *, pipeline: str, user_id: uuid.UUID | None) -> dict | None:
    """§05 §5.2: ffmpeg normalise, then Gemini audio (transcribe + detect) in
    one call. Returns {"transcript","language","confidence"} or None -
    callers must have a degraded-mode fallback."""
    normalized_path = f"{raw_path}.norm.wav"
    await preprocess_for_stt(raw_path, normalized_path)
    audio_bytes = await asyncio.to_thread(_read_and_delete, normalized_path)

    return await gemini_client.transcribe_audio(
        audio_bytes=audio_bytes,
        mime_type="audio/wav",
        pipeline=pipeline,
        user_id=user_id,
    )
