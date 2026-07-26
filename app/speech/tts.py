import hashlib
from pathlib import Path
from typing import Protocol

from app.core.config import settings
from app.core.logging import get_logger
from app.speech import audio
from app.speech.edge_tts_provider import provider as edge_tts_provider

logger = get_logger(__name__)

# §06 §6.2: tuned for an over-70 audience - slower than default, unhurried.
DEFAULT_RATE = "-15%"
VOICES = {
    "en": "en-SG-LunaNeural",
    "zh-Hans": "zh-CN-XiaoxiaoNeural",
}


class TTSProvider(Protocol):
    async def synthesize_mp3(
        self, text: str, *, voice: str, rate: str, output_path: str
    ) -> None: ...


def _cache_key(text: str, voice: str, rate: str) -> str:
    return hashlib.sha256(f"{text}|{voice}|{rate}".encode()).hexdigest()


async def synthesize(text: str, *, language: str) -> str:
    """§06 §6.2: cache by sha256(text+voice+rate) - repeated reminders reuse
    the same file, which matters when the patient asks the same question
    five times. Returns the OGG/Opus filename inside TTS_CACHE_ROOT (never an
    absolute path - callers hand this straight to generate_media_token)."""
    voice = VOICES.get(language, VOICES["en"])
    ogg_filename = f"{_cache_key(text, voice, DEFAULT_RATE)}.ogg"
    ogg_path = Path(settings.tts_cache_root) / ogg_filename

    if ogg_path.exists():
        return ogg_filename

    ogg_path.parent.mkdir(parents=True, exist_ok=True)
    mp3_path = str(ogg_path.with_suffix(".mp3"))
    try:
        await edge_tts_provider.synthesize_mp3(
            text, voice=voice, rate=DEFAULT_RATE, output_path=mp3_path
        )
        await audio.transcode_tts_to_ogg(mp3_path, str(ogg_path))
    finally:
        Path(mp3_path).unlink(missing_ok=True)

    return ogg_filename
