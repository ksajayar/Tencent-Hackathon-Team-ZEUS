"""§11 priority test 2 - regression for the appointment date/time
default-zeroing bug.

`_continue_appointment`'s "datetime" step builds a `default` datetime for
dateutil to fill in any field the caregiver's text doesn't specify. An
earlier version passed a bare `datetime.now(...)` as that default, so "2pm"
(no minute/second given) picked up the *current* wall-clock minute/second -
e.g. parsed to 14:58:53 instead of 14:00:00. The fix zeroes
minute/second/microsecond on `default` before handing it to dateutil. This
locks that in, and also locks in `dayfirst=True` (Singapore/CLAUDE.md
convention, not dateutil's US-style MM/DD default).
"""

from datetime import datetime

import pytest

from app.db.models.user import User
from app.pipelines.caregiver import _continue_appointment


def _patient() -> User:
    return User(
        wa_id="wa-patient",
        phone_e164="+6591111111",
        display_name="Mary",
        preferred_language="en",
        timezone="Asia/Singapore",
        role="patient",
    )


def _caregiver() -> User:
    return User(
        wa_id="wa-caregiver",
        phone_e164="+6592222222",
        display_name="Alice",
        preferred_language="en",
        role="caregiver",
    )


@pytest.mark.asyncio
async def test_bare_hour_does_not_leak_current_minute_or_second():
    flow = {"step": "datetime", "data": {}}
    reply = await _continue_appointment(None, _caregiver(), _patient(), flow, "2pm", "en")

    start_at = datetime.fromisoformat(reply.meta["pending_caregiver_flow"]["data"]["start_at"])
    assert start_at.hour == 14
    assert start_at.minute == 0
    assert start_at.second == 0
    assert start_at.microsecond == 0


@pytest.mark.asyncio
async def test_hour_and_minute_specified_are_both_honoured():
    flow = {"step": "datetime", "data": {}}
    reply = await _continue_appointment(None, _caregiver(), _patient(), flow, "2:37pm", "en")

    start_at = datetime.fromisoformat(reply.meta["pending_caregiver_flow"]["data"]["start_at"])
    assert start_at.hour == 14
    assert start_at.minute == 37
    assert start_at.second == 0
    assert start_at.microsecond == 0


@pytest.mark.asyncio
async def test_slash_date_is_dayfirst_not_us_month_first():
    flow = {"step": "datetime", "data": {}}
    reply = await _continue_appointment(None, _caregiver(), _patient(), flow, "5/8 3pm", "en")

    start_at = datetime.fromisoformat(reply.meta["pending_caregiver_flow"]["data"]["start_at"])
    assert start_at.day == 5
    assert start_at.month == 8


@pytest.mark.asyncio
async def test_unparseable_text_reprompts_instead_of_crashing():
    flow = {"step": "datetime", "data": {}}
    reply = await _continue_appointment(None, _caregiver(), _patient(), flow, "asdkjhqwe", "en")

    assert reply.meta["pending_caregiver_flow"]["step"] == "datetime"
    assert "start_at" not in reply.meta["pending_caregiver_flow"]["data"]
