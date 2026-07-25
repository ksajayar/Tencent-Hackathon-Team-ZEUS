from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import outbound
from app.core.logging import get_logger
from app.db.models.medication import Medication
from app.db.models.reminder import Reminder
from app.db.models.user import User
from app.db.session import async_session
from app.i18n.strings import MEDICATION_GUARD_FALLBACK, MEDICATION_REMINDER
from app.safety import medication_guard
from app.services import calendar as calendar_service
from app.services import conversation as conversation_service
from app.services.medications import time_of_day_label
from app.services.rrule import next_occurrence

logger = get_logger(__name__)


async def _fire_medication_reminder(session: AsyncSession, user: User, reminder: Reminder) -> bool:
    """Renders the SAFETY-1 template from the medications row (never the LLM),
    guards it, and sends. Returns whether the send actually went out."""
    medication = await session.get(Medication, reminder.source_id)
    if medication is None or not medication.active or medication.verified_by is None:
        reminder.active = False
        logger.warning("reminder_medication_missing_or_unverified", reminder_id=str(reminder.id))
        return False

    local_hour = reminder.next_fire_at.astimezone(ZoneInfo(user.timezone)).hour
    template = MEDICATION_REMINDER.get(user.preferred_language, MEDICATION_REMINDER["en"])
    body = template.format(
        time_of_day=time_of_day_label(local_hour, user.preferred_language),
        name=medication.name,
        dose_text=medication.dose_text,
    )
    fallback = MEDICATION_GUARD_FALLBACK.get(
        user.preferred_language, MEDICATION_GUARD_FALLBACK["en"]
    )
    body = medication_guard.enforce(body, [medication], fallback=fallback)

    title = reminder.title_zh if user.preferred_language == "zh-Hans" else reminder.title_en
    when_text = calendar_service.render_when(
        reminder.next_fire_at,
        is_all_day=False,
        tz_name=user.timezone,
        language=user.preferred_language,
    )

    conversation = await conversation_service.get_or_create_open_conversation(session, user)
    message = await outbound.send_reminder(
        session,
        user,
        conversation.id,
        reminder=reminder,
        body=body,
        template_when=when_text,
        template_what=title,
    )
    return message is not None


async def fire_one(session: AsyncSession, reminder: Reminder) -> bool:
    """Render + send one reminder right now, then advance next_fire_at from
    its RRULE - shared by the batch job and POST /internal/reminders/fire/{id}
    (§09 demo control), so a forced fire behaves identically to a real one.
    Firing always advances the schedule regardless of send outcome - a failed
    send is retried by tomorrow's occurrence, not today's, since a same-day
    resend risks a confusing double-fire. Returns whether the send went out."""
    user = await session.get(User, reminder.user_id)
    if user is None:
        reminder.active = False
        return False

    if reminder.kind == "medication":
        ok = await _fire_medication_reminder(session, user, reminder)
    else:
        # appointment/routine/shopping reminders land in a later milestone.
        logger.info("reminder_kind_not_yet_handled", kind=reminder.kind)
        ok = False

    if not reminder.active:
        return ok

    if reminder.rrule:
        next_fire = next_occurrence(
            reminder.rrule, after=reminder.next_fire_at, tz_name=user.timezone
        )
        if next_fire is None:
            reminder.active = False
        else:
            reminder.next_fire_at = next_fire
    else:
        reminder.active = False
    reminder.last_fired_at = datetime.now(UTC)
    return ok


async def fire_due_reminders() -> None:
    """Every 60s (§09): fire everything due."""
    async with async_session() as session:
        result = await session.execute(
            select(Reminder).where(
                Reminder.active.is_(True), Reminder.next_fire_at <= datetime.now(UTC)
            )
        )
        due = list(result.scalars().all())
        if not due:
            return

        sent = 0
        for reminder in due:
            if await fire_one(session, reminder):
                sent += 1
        await session.commit()
        logger.info("fire_due_reminders_run", due_count=len(due), sent=sent)
