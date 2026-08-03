"""Two related calendar fixes.

1. Past tense. `render_when` compared dates only, so an appointment that
   finished at 1:45pm still rendered as "today at 1:45 in the afternoon" at
   4pm - and every template and persona rule around it is present tense, so
   the patient was told they still HAVE an appointment they had already
   attended. For someone with dementia that is the harmful direction of
   wrong: they may set out for it again.

2. Lookahead. `get_schedule_window` stops at tomorrow, by design (§05 §5.1 -
   ordinary conversation should not carry three months of calendar). But
   nothing else reached further out, so "What's my next appointment?" -
   docs/11 §11.7's own demo step 3 - had no answer path at all: the context
   block just said "No events scheduled today or tomorrow".
"""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.ai.context import _format_event_line
from app.db.models.calendar import CalendarEvent
from app.db.models.user import User
from app.pipelines.text import _APPOINTMENT_QUERY_RE, _appointment_query
from app.services.calendar import get_upcoming_events, has_finished, render_when
from tests.conftest import unique_wa_id

SG = ZoneInfo("Asia/Singapore")


def _event(start_local: datetime, *, summary="Health checkup", location=None, hours=1):
    return CalendarEvent(
        user_id=uuid.uuid4(),
        google_event_id=f"local:{uuid.uuid4()}",
        summary=summary,
        location=location,
        start_at=start_local.astimezone(UTC),
        end_at=(start_local + timedelta(hours=hours)).astimezone(UTC),
        is_all_day=False,
        content_hash=f"local:{uuid.uuid4()}",
        synced_at=datetime.now(UTC),
    )


# --- 1. past tense ----------------------------------------------------------


def test_event_finished_earlier_today_reads_as_past():
    now = datetime.now(SG)
    # Halfway between local midnight and now, so this stays same-day
    # whatever time the suite runs at - a fixed "-3 hours" rolled into
    # yesterday for anything run in the first 3 hours after midnight
    # (caught by this suite running at 00:18 local).
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    finished = midnight + (now - midnight) / 2
    if finished >= now:
        pytest.skip("no elapsed same-day past instant yet")
    rendered = render_when(
        finished.astimezone(UTC), is_all_day=False, tz_name="Asia/Singapore", language="en"
    )
    assert rendered.startswith("earlier today")
    assert "tomorrow" not in rendered


def test_event_later_today_still_reads_as_today():
    now = datetime.now(SG)
    # Halfway between now and local midnight, so this stays same-day
    # whatever time the suite runs at - a fixed "+3 hours" silently skipped
    # itself every evening, which is exactly when it would have been most
    # worth running.
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    upcoming = now + (midnight - now) / 2
    if upcoming <= now:
        pytest.skip("no remaining same-day future instant")
    rendered = render_when(
        upcoming.astimezone(UTC), is_all_day=False, tz_name="Asia/Singapore", language="en"
    )
    assert rendered.startswith("today")
    assert "earlier" not in rendered


def test_past_and_future_are_distinguishable_in_chinese():
    now = datetime.now(SG)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    finished = midnight + (now - midnight) / 2
    if finished >= now:
        pytest.skip("no elapsed same-day past instant yet")
    past = render_when(
        finished.astimezone(UTC),
        is_all_day=False,
        tz_name="Asia/Singapore",
        language="zh-Hans",
    )
    assert past.startswith("今天稍早")


def test_has_finished_uses_end_time_not_start():
    now = datetime.now(SG)
    # Started an hour ago, runs for two hours - in progress, NOT finished.
    in_progress = _event(now - timedelta(hours=1), hours=2)
    assert has_finished(in_progress) is False

    over = _event(now - timedelta(hours=3), hours=1)
    assert has_finished(over) is True


def test_context_block_tags_finished_events_for_the_model():
    now = datetime.now(SG)
    over = _event(now - timedelta(hours=3), hours=1)
    line = _format_event_line(over, "Asia/Singapore")
    assert line.startswith("[already happened]")


def test_context_block_does_not_tag_an_upcoming_event():
    now = datetime.now(SG)
    upcoming = _event(now + timedelta(days=1))
    line = _format_event_line(upcoming, "Asia/Singapore")
    assert "[already happened]" not in line


# --- 2. lookahead -----------------------------------------------------------


def test_appointment_query_regex_matches_both_languages():
    for phrase in [
        "what's my next appointment",
        "When is my appointment?",
        "any appointments coming up",
        "我下一个预约是什么时候",
        "下次预约",
    ]:
        assert _APPOINTMENT_QUERY_RE.search(phrase), phrase


def test_set_appointment_command_does_not_trigger_the_query():
    # The caregiver command must not be swallowed by the query intent.
    for phrase in ["set appointment", "add appointment", "设置预约", "添加预约"]:
        assert not _APPOINTMENT_QUERY_RE.search(phrase), phrase


@pytest.mark.asyncio
async def test_get_upcoming_events_reaches_beyond_the_two_day_window(db_session):
    user = User(wa_id=unique_wa_id("look"), phone_e164="+6593000001", display_name="Mary")
    db_session.add(user)
    await db_session.flush()

    now = datetime.now(SG)
    next_month = _event(now + timedelta(days=35), summary="Cardiology review")
    next_month.user_id = user.id
    db_session.add(next_month)
    await db_session.flush()

    events = await get_upcoming_events(db_session, user.id)
    assert len(events) == 1
    assert events[0].summary == "Cardiology review"


@pytest.mark.asyncio
async def test_get_upcoming_events_excludes_past_and_orders_soonest_first(db_session):
    user = User(wa_id=unique_wa_id("look"), phone_e164="+6593000002", display_name="Mary")
    db_session.add(user)
    await db_session.flush()

    now = datetime.now(SG)
    for offset, summary in [
        (timedelta(days=-2), "Old visit"),
        (timedelta(days=20), "Later visit"),
        (timedelta(days=5), "Sooner visit"),
    ]:
        event = _event(now + offset, summary=summary)
        event.user_id = user.id
        db_session.add(event)
    await db_session.flush()

    events = await get_upcoming_events(db_session, user.id)
    assert [e.summary for e in events] == ["Sooner visit", "Later visit"]


@pytest.mark.asyncio
async def test_appointment_query_answers_with_a_next_month_event(db_session):
    user = User(
        wa_id=unique_wa_id("look"),
        phone_e164="+6593000003",
        display_name="Mary",
        timezone="Asia/Singapore",
    )
    db_session.add(user)
    await db_session.flush()

    now = datetime.now(SG)
    event = _event(
        now + timedelta(days=35), summary="Cardiology review", location="Singapore General Hospital"
    )
    event.user_id = user.id
    db_session.add(event)
    await db_session.flush()

    reply = await _appointment_query(db_session, user, "en", for_caregiver=False)
    assert "Cardiology review" in reply
    assert "Singapore General Hospital" in reply
    # Persona rule: never a 24-hour clock time.
    assert ":00" not in reply


@pytest.mark.asyncio
async def test_appointment_query_with_nothing_upcoming(db_session):
    user = User(
        wa_id=unique_wa_id("look"),
        phone_e164="+6593000004",
        display_name="Mary",
        timezone="Asia/Singapore",
    )
    db_session.add(user)
    await db_session.flush()

    reply = await _appointment_query(db_session, user, "en", for_caregiver=False)
    assert "no appointments" in reply.lower()


@pytest.mark.asyncio
async def test_appointment_query_caregiver_phrasing_names_the_patient(db_session):
    user = User(
        wa_id=unique_wa_id("look"),
        phone_e164="+6593000005",
        display_name="Mary",
        timezone="Asia/Singapore",
    )
    db_session.add(user)
    await db_session.flush()

    now = datetime.now(SG)
    event = _event(now + timedelta(days=10), summary="Dental checkup")
    event.user_id = user.id
    db_session.add(event)
    await db_session.flush()

    reply = await _appointment_query(db_session, user, "en", for_caregiver=True)
    assert "Mary" in reply
    assert "Dental checkup" in reply
