import asyncio
import hashlib
from pathlib import Path

import magic
import requests

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.media import MAX_MEDIA_SIZE_BYTES

logger = get_logger(__name__)


class MediaTooLargeError(Exception):
    pass


def _download_sync(media_url: str) -> bytes:
    response = requests.get(
        media_url,
        auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        timeout=30,
    )
    response.raise_for_status()
    return response.content


async def download_media(media_url: str) -> bytes:
    """Twilio media URLs require HTTP Basic auth (AccountSid:AuthToken) to
    fetch (§03 §3.3) - never fetchable anonymously."""
    content = await asyncio.to_thread(_download_sync, media_url)
    if len(content) > MAX_MEDIA_SIZE_BYTES:
        raise MediaTooLargeError(f"{len(content)} bytes > {MAX_MEDIA_SIZE_BYTES} cap")
    return content


def sniff_mime_type(content: bytes) -> str:
    """§03 §3.3: 'trust but verify' - MediaContentType0 is Twilio's claim,
    this is what the bytes actually are."""
    return magic.from_buffer(content, mime=True)


def _store_media_sync(content: bytes, *, extension: str) -> tuple[str, str]:
    sha256 = hashlib.sha256(content).hexdigest()
    filename = f"{sha256}{extension}"
    path = Path(settings.media_root) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(content)
    return str(path), sha256


async def store_media(content: bytes, *, extension: str) -> tuple[str, str]:
    """Saves under MEDIA_ROOT, keyed by content hash (dedupe, §08's sha256
    index). Returns (storage_path, sha256)."""
    return await asyncio.to_thread(_store_media_sync, content, extension=extension)
