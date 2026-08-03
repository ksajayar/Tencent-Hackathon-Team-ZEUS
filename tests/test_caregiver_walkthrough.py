"""End-to-end walkthroughs of `set appointment` and `set medication`,
driven through the real `text_pipeline.handle()` entry point against a real
Postgres.

Scope, stated honestly: this covers everything from the pipeline entry point
down - role activation, the caregiver command dispatch, the multi-turn
`messages.meta` flow chain, the 24h-window logic in outbound.py, and the
actual rows written to `calendar_events` / `medications`. The ONLY thing
stubbed is `provider.send_text` (tests/conftest.py's autouse
`stub_twilio_send` fixture) - the single method that puts bytes on the
wire to Twilio. So this proves the logic and the persistence; it does NOT
prove a WhatsApp message was delivered. That needs a real send on the
deployed instance (docs/11.7).

Both flows are fully deterministic - no LLM call anywhere in the
`set appointment` / `set medication` paths (SAFETY-1, docs/17 D3) - so
running them without live Gemini loses no coverage.

`text_pipeline.handle()` opens its own session from the module-level
`app.db.session.async_session`; see tests/conftest.py for why that engine
is rebuilt with NullPool for the test run.
"""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest_asyncio
from sqlalchemy import delete, select

from app.db.models.calendar import CalendarEvent
from app.db.models.contact import Contact
from app.db.models.medication import Medication
from app.db.models.message import Conversation, Message
from app.db.models.user import User
from app.db.session import async_session
from app.pipelines import text as text_pipeline

PATIENT_PHONE = "+6591000001"
CAREGIVER_PHONE = "+6591000002"


@pytest_asyncio.fixture
async def demo_pair():
    """A patient, a caregiver whose `users` row does not exist yet as a
    caregiver (role defaults to 'patient'), and the `contacts` row with
    relationship='caregiver' that IS the link (docs/17 §4: there is no
    caregiver_links table). Mirrors the real state after the patient has
    completed the two-step contact chain and before the caregiver has ever
    written in.
    """
    async with async_session() as session:
        patient = User(
            wa_id=f"wa-patient-{uuid.uuid4().hex[:10]}",
            phone_e164=PATIENT_PHONE,
            display_name="Mary",
            preferred_language="en",
            timezone="Asia/Singapore",
            role="patient",
            last_inbound_at=datetime.now(UTC),
        )
        caregiver = User(
            wa_id=f"wa-caregiver-{uuid.uuid4().hex[:10]}",
            phone_e164=CAREGIVER_PHONE,
            display_name="Alice",
            preferred_language="en",
            timezone="Asia/Singapore",
            role="patient",
            last_inbound_at=datetime.now(UTC),
        )
        session.add_all([patient, caregiver])
        await session.flush()

        session.add(
            Contact(
                user_id=patient.id,
                display_name="Alice",
                phone_e164=CAREGIVER_PHONE,
                relationship="caregiver",
                is_emergency=True,
                priority=1,
                source="vcard",
            )
        )
        caregiver_conversation = Conversation(user_id=caregiver.id)
        patient_conversation = Conversation(user_id=patient.id)
        session.add_all([caregiver_conversation, patient_conversation])
        await session.flush()

        ids = {
            "patient_id": patient.id,
            "caregiver_id": caregiver.id,
            "caregiver_conversation_id": caregiver_conversation.id,
        }
        await session.commit()

    yield ids

    async with async_session() as session:
        await session.execute(
            delete(User).where(User.id.in_([ids["patient_id"], ids["caregiver_id"]]))
        )
        await session.commit()


async def _caregiver_says(ids: dict, text: str) -> str:
    """One inbound turn from the caregiver, through the real pipeline.
    Returns the reply body actually persisted as the newest outbound
    message - i.e. what the caregiver would have received."""
    async with async_session() as session:
        message = Message(
            conversation_id=ids["caregiver_conversation_id"],
            user_id=ids["caregiver_id"],
            direction="inbound",
            channel_sid=f"SM{uuid.uuid4().hex}",
            kind="text",
            body=text,
            status="received",
        )
        session.add(message)
        caregiver = await session.get(User, ids["caregiver_id"])
        caregiver.last_inbound_at = datetime.now(UTC)
        await session.commit()
        message_id = message.id

    await text_pipeline.handle(
        user_id=ids["caregiver_id"],
        conversation_id=ids["caregiver_conversation_id"],
        message_id=message_id,
        text=text,
    )

    async with async_session() as session:
        result = await session.execute(
            select(Message)
            .where(Message.user_id == ids["caregiver_id"], Message.direction == "outbound")
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        latest = result.scalars().first()
        return latest.body if latest else ""


async def _activate_caregiver(ids: dict) -> str:
    """First inbound message flips role='patient' -> 'caregiver' and returns
    the deterministic activation string, deliberately WITHOUT also answering
    the message (text.py: the first thing a caregiver reads is never
    LLM-generated)."""
    return await _caregiver_says(ids, "hi")


async def test_caregiver_activation_on_first_message(demo_pair):
    reply = await _activate_caregiver(demo_pair)

    assert "caregiver" in reply.lower()
    assert "Mary" in reply
    # The four set commands are advertised on activation (docs/17 §4.1).
    for command in ("set appointment", "set bloodwork", "set address", "set medication"):
        assert command in reply

    async with async_session() as session:
        caregiver = await session.get(User, demo_pair["caregiver_id"])
        assert caregiver.role == "caregiver"


async def test_set_appointment_walkthrough(demo_pair):
    await _activate_caregiver(demo_pair)

    ask_datetime = await _caregiver_says(demo_pair, "set appointment")
    assert "Mary" in ask_datetime

    ask_location = await _caregiver_says(demo_pair, "next Tuesday 3pm")
    assert ask_location != ask_datetime

    ask_purpose = await _caregiver_says(demo_pair, "Singapore General Hospital")
    assert ask_purpose != ask_location

    confirm = await _caregiver_says(demo_pair, "Cardiology check-up")
    # The confirm step must echo back all three collected values before
    # anything is written (docs/17 §6.2: confirm-before-write).
    assert "Cardiology check-up" in confirm
    assert "Singapore General Hospital" in confirm

    # Nothing may exist in calendar_events until the explicit yes.
    async with async_session() as session:
        result = await session.execute(
            select(CalendarEvent).where(CalendarEvent.user_id == demo_pair["patient_id"])
        )
        assert result.scalars().all() == []

    saved = await _caregiver_says(demo_pair, "yes")
    assert "Mary" in saved

    async with async_session() as session:
        result = await session.execute(
            select(CalendarEvent).where(CalendarEvent.user_id == demo_pair["patient_id"])
        )
        events = result.scalars().all()
        assert len(events) == 1
        event = events[0]
        assert event.summary == "Cardiology check-up"
        assert event.location == "Singapore General Hospital"
        # D1-b: a local event, never a real Google Calendar write.
        assert event.google_event_id.startswith("local:")
        assert event.is_all_day is False
        assert event.end_at - event.start_at == timedelta(hours=1)
        # The default-zeroing fix: a bare "3pm" must not carry the current
        # wall-clock minute/second into the stored appointment.
        local_start = event.start_at.astimezone(ZoneInfo("Asia/Singapore"))
        assert (local_start.hour, local_start.minute, local_start.second) == (15, 0, 0)


async def test_set_appointment_notifies_the_patient(demo_pair, stub_twilio_send):
    await _activate_caregiver(demo_pair)
    for turn in ("set appointment", "tomorrow 10am", "Raffles Clinic", "Blood pressure review"):
        await _caregiver_says(demo_pair, turn)
    await _caregiver_says(demo_pair, "yes")

    # docs/17 §6.2: "Confirm to the caregiver separately - the caregiver must
    # know the patient was told." Assert the patient actually got their own
    # message, addressed to the patient's number.
    patient_sends = [body for to, body in stub_twilio_send if to == PATIENT_PHONE]
    assert patient_sends, "patient was never notified of the new appointment"
    notice = patient_sends[-1]
    assert "Blood pressure review" in notice
    assert "Raffles Clinic" in notice

    async with async_session() as session:
        result = await session.execute(
            select(Message).where(
                Message.user_id == demo_pair["patient_id"], Message.direction == "outbound"
            )
        )
        assert result.scalars().all(), "no outbound message row persisted for the patient"


async def test_set_appointment_no_at_confirm_writes_nothing(demo_pair):
    await _activate_caregiver(demo_pair)
    for turn in ("set appointment", "tomorrow 9am", "Clinic", "Follow-up"):
        await _caregiver_says(demo_pair, turn)

    cancelled = await _caregiver_says(demo_pair, "no")
    assert cancelled

    async with async_session() as session:
        result = await session.execute(
            select(CalendarEvent).where(CalendarEvent.user_id == demo_pair["patient_id"])
        )
        assert result.scalars().all() == [], "a 'no' at confirm still wrote an event"


async def test_set_medication_walkthrough(demo_pair, stub_medication_schedule_parse):
    stub_medication_schedule_parse(
        {
            "rrule": "FREQ=DAILY;BYHOUR=8,20;BYMINUTE=0",
            "label_en": "twice a day, morning and evening",
            "label_zh": "每天两次，早晚各一次",
        }
    )
    await _activate_caregiver(demo_pair)

    ask_name = await _caregiver_says(demo_pair, "set medication")
    assert ask_name

    ask_dose = await _caregiver_says(demo_pair, "Donepezil")
    assert ask_dose != ask_name

    ask_confirm = await _caregiver_says(demo_pair, "1 tablet")
    # Free text now, not a numbered menu - describe it naturally.
    confirm = await _caregiver_says(demo_pair, "twice a day, morning and evening")
    assert confirm != ask_confirm
    assert "Donepezil" in confirm
    assert "1 tablet" in confirm
    assert "twice a day" in confirm

    # Nothing written before the explicit yes (SAFETY-1 / docs/17 D3).
    async with async_session() as session:
        result = await session.execute(
            select(Medication).where(Medication.patient_id == demo_pair["patient_id"])
        )
        assert result.scalars().all() == []

    saved = await _caregiver_says(demo_pair, "yes")
    assert "Donepezil" in saved

    async with async_session() as session:
        result = await session.execute(
            select(Medication).where(Medication.patient_id == demo_pair["patient_id"])
        )
        medications = result.scalars().all()
        assert len(medications) == 1
        medication = medications[0]
        assert medication.name == "Donepezil"
        assert medication.dose_text == "1 tablet"
        assert medication.schedule_rrule == "FREQ=DAILY;BYHOUR=8,20;BYMINUTE=0"
        assert medication.instruction_en == "Take 1 tablet, twice a day, morning and evening."
        assert medication.instruction_zh == "每天两次，早晚各一次服用1 tablet。"
        assert medication.active is True
        # D3: verified_by MUST be set, or the row is invisible to
        # get_active_medications() and the reminder scheduler.
        assert medication.verified_by == demo_pair["caregiver_id"]
        assert medication.verified_at is not None


async def test_set_medication_reprompts_on_unparseable_schedule(
    demo_pair, stub_medication_schedule_parse
):
    stub_medication_schedule_parse(None)  # simulates Gemini unable to parse it
    await _activate_caregiver(demo_pair)
    for turn in ("set medication", "Metformin", "500mg"):
        await _caregiver_says(demo_pair, turn)

    reprompt = await _caregiver_says(demo_pair, "asdkjhqwe nonsense")
    assert reprompt

    async with async_session() as session:
        result = await session.execute(
            select(Medication).where(Medication.patient_id == demo_pair["patient_id"])
        )
        assert result.scalars().all() == [], "an unparseable schedule still wrote a medication"


async def test_set_medication_no_at_confirm_writes_nothing(demo_pair):
    await _activate_caregiver(demo_pair)
    for turn in ("set medication", "Aspirin", "100mg", "once a day in the morning"):
        await _caregiver_says(demo_pair, turn)

    cancelled = await _caregiver_says(demo_pair, "no")
    assert cancelled

    async with async_session() as session:
        result = await session.execute(
            select(Medication).where(Medication.patient_id == demo_pair["patient_id"])
        )
        assert result.scalars().all() == [], "a 'no' at confirm still wrote a medication"
