import base64
import hashlib
import hmac
import secrets
import time

from cryptography.fernet import Fernet
from fastapi import Header, HTTPException

from app.core.config import settings

_fernet = Fernet(settings.token_encryption_key.encode())

MEDIA_TOKEN_TTL_SECONDS = 900  # 15 min (§09)


def encrypt_token(plaintext: str) -> bytes:
    return _fernet.encrypt(plaintext.encode())


def decrypt_token(ciphertext: bytes) -> str:
    # Decrypt at point of use only - never hold plaintext in module-level state (DATA-1).
    return _fernet.decrypt(ciphertext).decode()


async def require_admin_token(x_admin_token: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_admin_token, settings.admin_token):
        raise HTTPException(status_code=403, detail="Forbidden")


def _sign_media_payload(payload_b64: str) -> str:
    return hmac.new(
        settings.media_signing_key.encode(), payload_b64.encode(), hashlib.sha256
    ).hexdigest()


def generate_media_token(filename: str) -> str:
    """Signed, time-limited token for GET /media/{token} (§09). Encodes only a
    filename, never an absolute path - the endpoint always resolves it inside
    TTS_CACHE_ROOT, so even a forged token (which requires MEDIA_SIGNING_KEY
    anyway) can't reach outside it."""
    expires_at = int(time.time()) + MEDIA_TOKEN_TTL_SECONDS
    payload = f"{filename}|{expires_at}"
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    signature = _sign_media_payload(payload_b64)
    return f"{payload_b64}.{signature}"


def verify_media_token(token: str) -> str | None:
    """Returns the filename if the token is valid and unexpired, else None."""
    try:
        payload_b64, signature = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(_sign_media_payload(payload_b64), signature):
        return None

    padding = "=" * (-len(payload_b64) % 4)
    try:
        payload = base64.urlsafe_b64decode(payload_b64 + padding).decode()
        filename, expires_at_str = payload.rsplit("|", 1)
        expires_at = int(expires_at_str)
    except (ValueError, UnicodeDecodeError):
        return None

    if time.time() > expires_at:
        return None
    return filename
