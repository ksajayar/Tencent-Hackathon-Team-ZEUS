import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.twilio_whatsapp import provider
from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import generate_media_token
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
    session: AsyncSession,
    user: User,
    conversation_id: uuid.UUID,
    body: str,
    *,
    meta: dict | None = None,
) -> Message:
    await _throttle()
    sid = await provider.send_text(user.phone_e164, body)
    message = await conversation_service.record_outbound_message(
        session, user=user, conversation_id=conversation_id, channel_sid=sid, body=body, meta=meta
    )
    logger.info("outbound_sent", user_id=str(user.id), message_sid=sid)
    return message


async def send_text(
    session: AsyncSession,
    user: User,
    conversation_id: uuid.UUID,
    body: str,
    *,
    meta: dict | None = None,
) -> Message | None:
    """The only function allowed to send outbound WhatsApp text (CHANNEL-1).

    Inside the 24h window: sends free-form immediately. Outside it: parks in
    outbound_queue (status=awaiting_window) rather than dropping - the
    flush_outbound_queue job (§09) sends it once the window reopens.

    `meta` (M9): only applied on the immediate-send path - a queued send has
    no `messages` row yet to attach it to, so a pending-confirmation flow
    started while the window is closed simply doesn't track state (the
    window being closed already means the demo's happy path doesn't apply).
    """
    if window_open(user):
        return await _send_free_form(session, user, conversation_id, body, meta=meta)

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
    fully-worded free-form message. Outside it: the approved 'appointment
    reminder' template ({{1}}=when, {{2}}=what) - a reminder is never silently
    dropped for being outside the window, unlike a plain reply.

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


async def send_audio(
    session: AsyncSession, user: User, conversation_id: uuid.UUID, *, filename: str, body_text: str
) -> Message | None:
    """CHANNEL-2/3: always a second, separate send after the text reply -
    never combined with a Body, and only OGG/Opus renders as a playable
    voice note rather than a file attachment. `body_text` (the same words
    just spoken) is stored so 'say that again' still works after a voice
    reply. Window-closed case: the paired text reply was already queued by
    send_text, so there's no fresh pairing to synthesize audio for here -
    log and skip rather than send an orphaned voice note later."""
    if not window_open(user):
        logger.warning("audio_reply_skipped_window_closed", user_id=str(user.id))
        return None

    await _throttle()
    token = generate_media_token(filename)
    media_url = f"{settings.public_base_url.rstrip('/')}/media/{token}"
    sid = await provider.send_media(user.phone_e164, media_url, "audio/ogg")

    message = await conversation_service.record_outbound_message(
        session,
        user=user,
        conversation_id=conversation_id,
        channel_sid=sid,
        body=body_text,
        kind="audio",
    )
    logger.info("outbound_audio_sent", user_id=str(user.id), message_sid=sid)
    return message


async def send_typing_indicator(message_sid: str) -> None:
    """Not a persisted message and not subject to the throttle/window rules
    above - a side signal on the inbound message, not an outbound send. Kept
    behind this module anyway (CHANNEL-1) so provider access stays in one place."""
    await provider.send_typing_indicator(message_sid)


async def send_urgent(to_phone_e164: str, body: str) -> str | None:
    """CLAUDE.md SAFETY-2 / §07 §7.9 + §7.10: SOS and safe-zone-breach alerts
    to an emergency contact's own phone. Bypasses the 24h window entirely -
    unlike send_text, an emergency alert must attempt delivery immediately,
    never park in outbound_queue awaiting a window that may not reopen in
    time. There is no `conversations` row for a third-party contact, so this
    doesn't persist a `messages` row either - the caller (app/safety/sos.py)
    records the outcome in `sos_events.notified`. Returns the Twilio SID on
    success, None on failure so the caller can try the next contact."""
    await _throttle()
    try:
        sid = await provider.send_text(to_phone_e164, body)
    except Exception:
        logger.exception("urgent_send_failed")
        return None
    logger.info("urgent_sent")
    return sid


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
