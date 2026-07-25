import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.medication import Medication
from app.db.models.reminder import Reminder, ReminderAck
from app.db.models.user import User
from app.services.rrule import next_occurrence

UNACKED_LOOKBACK = timedelta(hours=12)


async def get_active_medications(session: AsyncSession, patient_id: uuid.UUID) -> list[Medication]:
    """Caregiver-verified, active rows only (SAFETY-1)."""
    result = await session.execute(
        select(Medication).where(
            Medication.patient_id == patient_id,
            Medication.active.is_(True),
            Medication.verified_by.is_not(None),
        )
    )
    return list(result.scalars().all())


def render_medication_list(medications: list[Medication], language: str) -> str:
    """Deterministic, no LLM: instruction_en/zh are pre-written simple language
    (§08) copied verbatim, name-prefixed so multiple medications don't blur
    together and so the guard's verbatim-name check is meaningful."""
    lines = [
        f"{m.name}: {m.instruction_zh if language == 'zh-Hans' else m.instruction_en}"
        for m in medications
    ]
    return "\n".join(lines)


def time_of_day_label(local_hour: int, language: str) -> str:
    if language == "zh-Hans":
        if 5 <= local_hour < 12:
            return "早上"
        if 12 <= local_hour < 18:
            return "下午"
        if 18 <= local_hour < 22:
            return "晚上"
        return "凌晨"
    if 5 <= local_hour < 12:
        return "morning"
    if 12 <= local_hour < 18:
        return "afternoon"
    if 18 <= local_hour < 22:
        return "evening"
    return "night"


def _titles(medication: Medication, fire_at: datetime | None, tz_name: str) -> tuple[str, str]:
    if fire_at is None:
        hour_en, hour_zh = "", ""
    else:
        local_hour = fire_at.astimezone(ZoneInfo(tz_name)).hour
        hour_en = time_of_day_label(local_hour, "en")
        hour_zh = time_of_day_label(local_hour, "zh-Hans")
    title_en = f"your {hour_en} medicine — {medication.name}, {medication.dose_text}".strip()
    title_zh = f"{hour_zh}的药——{medication.name}，{medication.dose_text}".strip()
    return title_en, title_zh


async def sync_medication_reminders(session: AsyncSession) -> int:
    """Reconciles reminders (kind='medication') from medications: every
    active+verified row gets a reminder; anything that lost verification or
    was deactivated has its reminder switched off (SAFETY-1: unverified rows
    are invisible to the reminder system). Returns the number of reminder
    rows touched."""
    med_result = await session.execute(select(Medication))
    medications = {m.id: m for m in med_result.scalars().all()}

    reminder_result = await session.execute(
        select(Reminder).where(Reminder.source == "medication", Reminder.kind == "medication")
    )
    existing = {r.source_id: r for r in reminder_result.scalars().all()}

    touched = 0
    for medication in medications.values():
        eligible = medication.active and medication.verified_by is not None
        reminder = existing.get(medication.id)

        if not eligible:
            if reminder is not None and reminder.active:
                reminder.active = False
                touched += 1
            continue

        user = await session.get(User, medication.patient_id)
        if user is None:
            continue

        if reminder is None:
            next_fire_at = next_occurrence(
                medication.schedule_rrule, after=datetime.now(UTC), tz_name=user.timezone
            )
            title_en, title_zh = _titles(medication, next_fire_at, user.timezone)
            session.add(
                Reminder(
                    user_id=medication.patient_id,
                    kind="medication",
                    source="medication",
                    source_id=medication.id,
                    title_en=title_en,
                    title_zh=title_zh,
                    rrule=medication.schedule_rrule,
                    next_fire_at=next_fire_at,
                    active=True,
                )
            )
            touched += 1
            continue

        changed = False
        if not reminder.active:
            reminder.active = True
            changed = True
        if reminder.rrule != medication.schedule_rrule:
            reminder.rrule = medication.schedule_rrule
            reminder.next_fire_at = next_occurrence(
                medication.schedule_rrule, after=datetime.now(UTC), tz_name=user.timezone
            )
            changed = True
        title_en, title_zh = _titles(medication, reminder.next_fire_at, user.timezone)
        if reminder.title_en != title_en or reminder.title_zh != title_zh:
            reminder.title_en = title_en
            reminder.title_zh = title_zh
            changed = True
        if changed:
            touched += 1

    await session.flush()
    return touched


async def find_unacked_reminder(session: AsyncSession, user_id: uuid.UUID) -> Reminder | None:
    """Most recent medication reminder that fired but hasn't been acked since
    its last firing - used by the ack regex handler and the ack watchdog."""
    cutoff = datetime.now(UTC) - UNACKED_LOOKBACK
    result = await session.execute(
        select(Reminder)
        .where(
            Reminder.kind == "medication",
            Reminder.user_id == user_id,
            Reminder.last_fired_at.is_not(None),
            Reminder.last_fired_at > cutoff,
        )
        .order_by(Reminder.last_fired_at.desc())
    )
    for reminder in result.scalars().all():
        ack_result = await session.execute(
            select(ReminderAck).where(
                ReminderAck.reminder_id == reminder.id,
                ReminderAck.acked_at > reminder.last_fired_at,
            )
        )
        if ack_result.scalar_one_or_none() is None:
            return reminder
    return None
