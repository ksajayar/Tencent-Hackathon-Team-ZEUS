import asyncio
import uuid
from datetime import UTC, datetime

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import outbound
from app.core.deps import get_db
from app.core.logging import get_logger
from app.core.security import decrypt_token, require_admin_token
from app.db.models.google import OAuthState, OAuthToken
from app.db.models.user import User
from app.db.session import async_session
from app.google import oauth as google_oauth
from app.google import tokens as google_tokens
from app.i18n.strings import OAUTH_DENIED, OAUTH_ERROR, OAUTH_SUCCESS
from app.services import conversation as conversation_service

router = APIRouter()
logger = get_logger(__name__)

_EXPIRED_HTML = (
    "<h1>That link expired</h1><p>Please say 'connect google' on WhatsApp again for a new link.</p>"
)
_DENIED_HTML = "<h1>No problem</h1><p>You can connect your Google account later.</p>"
_SUCCESS_HTML = (
    "<h1>Google account connected</h1><p>You can close this page and return to WhatsApp.</p>"
)
_ERROR_HTML = "<h1>Something went wrong</h1><p>Please return to WhatsApp and try again.</p>"


async def _notify_user(user_id: uuid.UUID, strings: dict[str, str]) -> None:
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is None:
            return
        conversation = await conversation_service.get_or_create_open_conversation(session, user)
        body = strings.get(user.preferred_language, strings["en"])
        await outbound.send_text(session, user, conversation.id, body)
        await session.commit()


@router.get("/oauth/google/start")
async def oauth_start(state: str, session: AsyncSession = Depends(get_db)):
    result = await session.execute(select(OAuthState).where(OAuthState.state == state))
    row = result.scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at < datetime.now(UTC):
        return HTMLResponse(_EXPIRED_HTML, status_code=400)

    return RedirectResponse(google_oauth.build_authorization_url(state))


@router.get("/oauth/google/callback")
async def oauth_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
):
    params = request.query_params
    state = params.get("state")
    error = params.get("error")
    code = params.get("code")

    if not state:
        return HTMLResponse(_EXPIRED_HTML, status_code=400)

    result = await session.execute(select(OAuthState).where(OAuthState.state == state))
    state_row = result.scalar_one_or_none()
    expired = (
        state_row is None
        or state_row.used_at is not None
        or state_row.expires_at < datetime.now(UTC)
    )
    if expired:
        return HTMLResponse(_EXPIRED_HTML, status_code=400)

    # Single-use: mark immediately, before any further processing.
    state_row.used_at = datetime.now(UTC)
    user_id = state_row.user_id
    await session.commit()

    if error:
        logger.info("oauth_denied", user_id=str(user_id), error=error)
        background_tasks.add_task(_notify_user, user_id, OAUTH_DENIED)
        return HTMLResponse(_DENIED_HTML)

    try:
        credentials = await asyncio.to_thread(google_oauth.exchange_code, code=code, state=state)
        await google_tokens.store_credentials(session, user_id=user_id, credentials=credentials)
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception("oauth_callback_failed", user_id=str(user_id))
        background_tasks.add_task(_notify_user, user_id, OAUTH_ERROR)
        return HTMLResponse(_ERROR_HTML, status_code=500)

    logger.info("oauth_connected", user_id=str(user_id))
    background_tasks.add_task(_notify_user, user_id, OAUTH_SUCCESS)
    return HTMLResponse(_SUCCESS_HTML)


@router.post("/oauth/google/revoke", dependencies=[Depends(require_admin_token)])
async def oauth_revoke(user_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    result = await session.execute(
        select(OAuthToken).where(OAuthToken.user_id == user_id, OAuthToken.provider == "google")
    )
    row = result.scalar_one_or_none()
    if row is None:
        return {"status": "not_connected"}

    access_token = decrypt_token(row.access_token_enc)
    try:
        await asyncio.to_thread(
            requests.post,
            "https://oauth2.googleapis.com/revoke",
            params={"token": access_token},
            headers={"content-type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
    except Exception:
        logger.warning("google_revoke_call_failed", user_id=str(user_id))

    await session.delete(row)
    await session.commit()
    logger.info("oauth_revoked", user_id=str(user_id))
    return {"status": "revoked"}
