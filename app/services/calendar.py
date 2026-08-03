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


LOOKAHEAD_DAYS = 90
LOOKAHEAD_LIMIT = 3


async def get_upcoming_events(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    days: int = LOOKAHEAD_DAYS,
    limit: int = LOOKAHEAD_LIMIT,
) -> list[CalendarEvent]:
    """§17 §7-style deterministic read for "what's my next appointment?" -
    the demo script's own step 3 (docs/11 §11.7), which nothing answered
    before: `get_schedule_window` stops at tomorrow, so the LLM's context
    block said "No events scheduled today or tomorrow" for anything further
    out and the model had nothing to work from.

    Deliberately a SEPARATE query rather than a wider `get_schedule_window`.
    That two-day window is a persona decision, not an oversight (§05 §5.1) -
    a dementia patient's ordinary conversation should not carry three months
    of calendar. This answers the explicit question only, capped at `limit`
    events so the reply stays within the persona's three-item rule.

    Filters on start_at > now, not >= midnight: "next" means still to come,
    so an appointment that finished an hour ago is not it.
    """
    now = datetime.now(UTC)
    result = await session.execute(
        select(CalendarEvent)
        .where(
            CalendarEvent.user_id == user_id,
            CalendarEvent.start_at > now,
            CalendarEvent.start_at < now + timedelta(days=days),
        )
        .order_by(CalendarEvent.start_at)
        .limit(limit)
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


def has_finished(event: CalendarEvent) -> bool:
    """Whether an event is already over. `get_schedule_window` starts at
    local midnight, so events that finished earlier today are deliberately
    still in the window (a patient who asks "do I have a checkup today?"
    after the fact needs to hear that they already went, not silence) -
    but every caller rendering one has to know to say so in the past tense.
    Compares end_at, not start_at: an appointment in progress is not over."""
    return event.end_at <= datetime.now(UTC)


def render_when(start_at: datetime, *, is_all_day: bool, tz_name: str, language: str) -> str:
    """Day-relative phrasing ("tomorrow at 2 in the afternoon") for the
    templated (non-LLM) reschedule notice, and for the <schedule> context
    block. The context block feeds the LLM this phrasing already rendered
    rather than a raw "14:00" it is then forbidden to echo (§07 §7.6): given
    only the clock form, the model tended to drop the time from the reply
    altogether instead of converting it.

    Same-day events distinguish past from future ("earlier today at 1:45 in
    the afternoon" vs "today at 3 in the afternoon"). Comparing dates alone
    rendered a checkup that finished at 1:45pm as plain "today at 1:45 in
    the afternoon" at 4pm, which every surrounding template then stated in
    the present tense - telling a dementia patient they still have an
    appointment they had already attended. Only same-day needs the
    distinction: "yesterday"/"on 3 August" already read as past, and
    "tomorrow" cannot be.
    """
    tz = ZoneInfo(tz_name)
    local = start_at.astimezone(tz)
    now_local = datetime.now(tz)
    delta_days = (local.date() - now_local.date()).days
    is_past_today = delta_days == 0 and local < now_local

    if language == "zh-Hans":
        if delta_days == 0:
            day_part = "今天稍早" if is_past_today else "今天"
        elif delta_days == 1:
            day_part = "明天"
        elif delta_days == -1:
            day_part = "昨天"
        else:
            day_part = f"{local.month}月{local.day}日"
        return day_part if is_all_day else f"{day_part}{_time_phrase_zh(local)}"

    if delta_days == 0:
        day_part = "earlier today" if is_past_today else "today"
    elif delta_days == 1:
        day_part = "tomorrow"
    elif delta_days == -1:
        day_part = "yesterday"
    else:
        day_part = f"on {local.day} {local.strftime('%B')}"
    return day_part if is_all_day else f"{day_part} at {_time_phrase_en(local)}"


def render_time_of_day(moment: datetime, *, tz_name: str, language: str) -> str:
    """Just the spoken time part ("3 in the afternoon"), no day. Used for an
    event's end time, where the day is already carried by render_when."""
    local = moment.astimezone(ZoneInfo(tz_name))
    return _time_phrase_zh(local) if language == "zh-Hans" else _time_phrase_en(local)


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
