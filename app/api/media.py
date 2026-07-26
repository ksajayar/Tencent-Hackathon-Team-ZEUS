from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.security import verify_media_token

router = APIRouter()


@router.get("/media/{token}")
async def serve_media(token: str) -> FileResponse:
    """Serves TTS audio to Twilio (§09). Content-Type is what Twilio's HEAD
    check cares about (§03 §3.4) - a short fixed filename keeps us inside the
    <=20-ASCII-char rule regardless of the token's own length."""
    filename = verify_media_token(token)
    if filename is None:
        raise HTTPException(status_code=404, detail="not found")

    path = Path(settings.tts_cache_root) / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")

    return FileResponse(path, media_type="audio/ogg", filename="voice.ogg")
