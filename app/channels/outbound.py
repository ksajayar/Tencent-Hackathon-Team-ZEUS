import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.twilio_whatsapp import provider
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.message import Message
from app.db.models.outbound_queue import OutboundQueueEntry
from app.db.models.reminder import Reminder
from app.db.models.user import User
from app.services import conversation as conversation_service

logger = get_logger(__name__)

WINDOW_HOURS = 24
THROTTLE_SECONDS = 3.0
MAX_SEND_ATTEMPTS = 3

_last_send_at = 0.0
_throttle_lock = asyncio.Lock()


def window_open(user: User) -> bool:
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


async def _send_free_form(
    session: AsyncSession, user: User, conversation_id: uuid.UUID, body: str
) -> Message:
    await _throttle()
    sid = await provider.send_text(user.phone_e164, body)
    message = await conversation_service.record_outbound_message(
        session, user=user, conversation_id=conversation_id, channel_sid=sid, body=body
    )
    logger.info("outbound_sent", user_id=str(user.id), message_sid=sid)
    return message


async def send_text(
    session: AsyncSession, user: User, conversation_id: uuid.UUID, body: str
) -> Message | None:
    """The only function allowed to send outbound WhatsApp text (CHANNEL-1).

    Inside the 24h window: sends free-form immediately. Outside it: parks in
    outbound_queue (status=awaiting_window) rather than dropping - the
    flush_outbound_queue job (§09) sends it once the window reopens.
    """
    if window_open(user):
        return await _send_free_form(session, user, conversation_id, body)

    session.add(OutboundQueueEntry(user_id=user.id, body=body, status="awaiting_window"))
    await session.flush()
    logger.info("outbound_queued_awaiting_window", user_id=str(user.id))
    return None


async def send_reminder(
    session: AsyncSession,
    user: User,
    conversation_id: uuid.UUID,
    *,
    reminder: Reminder,
    body: str,
    template_when: str,
    template_what: str,
) -> Message | None:
    """For scheduler-fired reminders only (§03 §3.4). Inside the window: the
    fully-worded free-form message. Outside it: the sandbox's one fixed
    'appointment reminder' template ({{1}}=when, {{2}}=what) - a reminder is
    never silently dropped for being outside the window, unlike a plain reply.

    On template-send failure this is logged, not queued for retry: the next
    day's RRULE-advanced occurrence is the natural retry for a recurring
    medication reminder, and a same-day resend risks a confusing double-fire.
    """
    if window_open(user):
        return await _send_free_form(session, user, conversation_id, body)

    if not settings.twilio_appointment_template_sid:
        logger.error(
            "reminder_template_not_configured",
            user_id=str(user.id),
            reminder_id=str(reminder.id),
        )
        return None

    await _throttle()
    try:
        sid = await provider.send_template(
            user.phone_e164,
            settings.twilio_appointment_template_sid,
            {"1": template_when, "2": template_what},
        )
    except Exception:
        logger.exception(
            "reminder_template_send_failed",
            user_id=str(user.id),
            reminder_id=str(reminder.id),
        )
        return None

    message = await conversation_service.record_outbound_message(
        session, user=user, conversation_id=conversation_id, channel_sid=sid, body=body
    )
    logger.info("outbound_reminder_sent", user_id=str(user.id), message_sid=sid, via="template")
    return message


async def flush_awaiting_window(session: AsyncSession) -> int:
    """Every 5s (§09): claim parked sends whose user's window has reopened
    since parking, and send them free-form. FOR UPDATE SKIP LOCKED is why
    there's no Redis (§08) - harmless with today's single worker, correct if
    that ever changes."""
    result = await session.execute(
        select(OutboundQueueEntry)
        .where(OutboundQueueEntry.status == "awaiting_window")
        .order_by(OutboundQueueEntry.scheduled_for)
        .with_for_update(skip_locked=True)
    )
    entries = list(result.scalars().all())
    if not entries:
        return 0

    sent = 0
    for entry in entries:
        user = await session.get(User, entry.user_id)
        if user is None or not window_open(user):
            continue
        conversation = await conversation_service.get_or_create_open_conversation(session, user)
        try:
            await _send_free_form(session, user, conversation.id, entry.body)
            entry.status = "sent"
            entry.sent_at = datetime.now(UTC)
            sent += 1
        except Exception as exc:
            entry.attempts += 1
            entry.last_error = str(exc)[:300]
            entry.status = "failed"
            logger.error(
                "outbound_queue_send_failed", user_id=str(user.id), attempts=entry.attempts
            )
    await session.flush()
    return sent


async def retry_failed_sends(session: AsyncSession) -> int:
    """Every 5 minutes (§09): give failed sends that haven't exhausted their
    attempt budget another chance - reset to awaiting_window so the next
    flush_awaiting_window pass (within 5s) actually resends it. The 5-minute
    job cadence is the backoff; attempts >= MAX_SEND_ATTEMPTS stops retrying
    for good (an alertable event, per the structured log)."""
    result = await session.execute(
        select(OutboundQueueEntry).where(
            OutboundQueueEntry.status == "failed",
            OutboundQueueEntry.attempts < MAX_SEND_ATTEMPTS,
        )
    )
    entries = list(result.scalars().all())
    for entry in entries:
        entry.status = "awaiting_window"
    await session.flush()
    return len(entries)
