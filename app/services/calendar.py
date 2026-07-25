import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.calendar import CalendarEvent


async def get_schedule_window(
    session: AsyncSession, user_id: uuid.UUID, *, tz_name: str
) -> list[CalendarEvent]:
    """Today's and tomorrow's events in the user's local timezone (§05 §5.1
    <schedule> context block: 'today's + tomorrow's calendar events')."""
    tz = ZoneInfo(tz_name)
    today_local = datetime.now(tz).date()
    window_start = datetime.combine(today_local, time.min, tzinfo=tz).astimezone(UTC)
    window_end = window_start + timedelta(days=2)

    result = await session.execute(
        select(CalendarEvent)
        .where(
            CalendarEvent.user_id == user_id,
            CalendarEvent.start_at < window_end,
            CalendarEvent.end_at > window_start,
        )
        .order_by(CalendarEvent.start_at)
    )
    return list(result.scalars().all())


def find_conflicts(
    events: list[CalendarEvent],
) -> list[tuple[CalendarEvent, CalendarEvent]]:
    """Pure, no DB (§04 §4.3 conflict scan). Sort by start; for each event scan
    forward until an event starts at/after it ends - since the list is sorted,
    nothing further out can overlap it either."""
    ordered = sorted(events, key=lambda e: e.start_at)
    conflicts: list[tuple[CalendarEvent, CalendarEvent]] = []
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if b.start_at >= a.end_at:
                break
            conflicts.append((a, b))
    return conflicts


def render_when(start_at: datetime, *, is_all_day: bool, tz_name: str, language: str) -> str:
    """Day-relative phrasing for the one templated (non-LLM) calendar message -
    the reschedule notice fired from the sync job. Everywhere else, the LLM
    phrases day-relative language itself per the persona rules (§07 §7.6),
    given the raw local time in the <schedule> context block."""
    tz = ZoneInfo(tz_name)
    local = start_at.astimezone(tz)
    delta_days = (local.date() - datetime.now(tz).date()).days

    if language == "zh-Hans":
        if delta_days == 0:
            day_part = "今天"
        elif delta_days == 1:
            day_part = "明天"
        else:
            day_part = f"{local.month}月{local.day}日"
        return day_part if is_all_day else f"{day_part}{_time_phrase_zh(local)}"

    if delta_days == 0:
        day_part = "today"
    elif delta_days == 1:
        day_part = "tomorrow"
    else:
        day_part = f"on {local.day} {local.strftime('%B')}"
    return day_part if is_all_day else f"{day_part} at {_time_phrase_en(local)}"


def _time_phrase_en(local: datetime) -> str:
    hour12 = local.strftime("%I").lstrip("0") or "12"
    if 5 <= local.hour < 12:
        period = "in the morning"
    elif 12 <= local.hour < 18:
        period = "in the afternoon"
    elif 18 <= local.hour < 22:
        period = "in the evening"
    else:
        period = "at night"
    if local.minute:
        return f"{hour12}:{local.minute:02d} {period}"
    return f"{hour12} {period}"


def _time_phrase_zh(local: datetime) -> str:
    if 5 <= local.hour < 12:
        period = "上午"
    elif local.hour == 12:
        period = "中午"
    elif 12 < local.hour < 18:
        period = "下午"
    elif 18 <= local.hour < 22:
        period = "晚上"
    else:
        period = "凌晨"
    hour12 = local.hour % 12 or 12
    if local.minute:
        return f"{period}{hour12}点{local.minute}分"
    return f"{period}{hour12}点"
