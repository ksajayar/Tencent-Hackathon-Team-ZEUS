import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.twilio_whatsapp import provider
from app.core.logging import get_logger
from app.db.models.message import Message
from app.db.models.user import User
from app.services import conversation as conversation_service

logger = get_logger(__name__)

WINDOW_HOURS = 24
THROTTLE_SECONDS = 3.0

_last_send_at = 0.0
_throttle_lock = asyncio.Lock()


def _window_open(user: User) -> bool:
    if user.last_inbound_at is None:
        return False
    return datetime.now(UTC) - user.last_inbound_at < timedelta(hours=WINDOW_HOURS)


async def _throttle() -> None:
    global _last_send_at
    async with _throttle_lock:
        wait = THROTTLE_SECONDS - (time.monotonic() - _last_send_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_send_at = time.monotonic()


async def send_text(
    session: AsyncSession, user: User, conversation_id: uuid.UUID, body: str
) -> Message | None:
    """The only function allowed to send outbound WhatsApp text (CHANNEL-1).

    Enforces the 24h window and the sandbox 1-per-3s throttle. A closed window
    has no template/queue fallback yet — that's M5 (reminders). For M1 it is
    logged and skipped rather than silently dropped.
    """
    if not _window_open(user):
        logger.warning("outbound_skipped_window_closed", user_id=str(user.id))
        return None

    await _throttle()
    sid = await provider.send_text(user.phone_e164, body)
    message = await conversation_service.record_outbound_message(
        session, user=user, conversation_id=conversation_id, channel_sid=sid, body=body
    )
    logger.info("outbound_sent", user_id=str(user.id), message_sid=sid)
    return message
