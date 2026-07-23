import asyncio
import uuid
from datetime import UTC, datetime

from google.auth.transport.requests import Request
from google.oauth2 import id_token as google_id_token
from google.oauth2.credentials import Credentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import decrypt_token, encrypt_token
from app.db.models.google import OAuthToken

logger = get_logger(__name__)


def _extract_sub(raw_id_token: str | None) -> str | None:
    if not raw_id_token:
        return None
    try:
        claims = google_id_token.verify_oauth2_token(
            raw_id_token, Request(), audience=settings.google_client_id
        )
        return claims.get("sub")
    except Exception:
        logger.warning("google_id_token_verify_failed")
        return None


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def store_credentials(
    session: AsyncSession, *, user_id: uuid.UUID, credentials: Credentials
) -> None:
    sub = await asyncio.to_thread(_extract_sub, credentials.id_token)

    result = await session.execute(
        select(OAuthToken).where(OAuthToken.user_id == user_id, OAuthToken.provider == "google")
    )
    row = result.scalar_one_or_none()

    access_enc = encrypt_token(credentials.token)
    refresh_enc = encrypt_token(credentials.refresh_token)
    expires_at = _as_utc(credentials.expiry)
    scopes = " ".join(credentials.scopes) if credentials.scopes else None
    now = datetime.now(UTC)

    if row is None:
        row = OAuthToken(
            user_id=user_id,
            provider="google",
            access_token_enc=access_enc,
            refresh_token_enc=refresh_enc,
            scopes=scopes,
            expires_at=expires_at,
            google_sub=sub,
            last_refreshed_at=now,
        )
        session.add(row)
    else:
        row.access_token_enc = access_enc
        row.refresh_token_enc = refresh_enc
        row.scopes = scopes
        row.expires_at = expires_at
        row.google_sub = sub
        row.last_refreshed_at = now

    await session.flush()


def _refresh_sync(credentials: Credentials) -> None:
    credentials.refresh(Request())


async def refresh_token_row(session: AsyncSession, row: OAuthToken) -> bool:
    """Returns True on success. On invalid_grant (revoked/expired refresh
    token), the row is deleted and False is returned - per §04, don't retry,
    the user must reconnect."""
    refresh_token = decrypt_token(row.refresh_token_enc)
    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
    )
    try:
        await asyncio.to_thread(_refresh_sync, credentials)
    except Exception as exc:
        if "invalid_grant" in str(exc):
            logger.warning("google_refresh_invalid_grant", user_id=str(row.user_id))
            await session.delete(row)
            await session.flush()
            return False
        logger.error(
            "google_refresh_failed", user_id=str(row.user_id), error_class=exc.__class__.__name__
        )
        return False

    row.access_token_enc = encrypt_token(credentials.token)
    row.expires_at = _as_utc(credentials.expiry)
    row.last_refreshed_at = datetime.now(UTC)
    await session.flush()
    return True
