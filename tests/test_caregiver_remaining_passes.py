"""The remaining manual passes: contact card -> caregiver activation,
check candidates -> promote, set bloodwork, set address, and Chinese-language
set appointment / set medication - all driven through the real
`text_pipeline.handle()` / `contact_pipeline.handle()` against real Postgres.

Scope note on "pill-bottle photo -> caregiver notified -> check candidates ->
promote": the vision extraction step (app/pipelines/image.py) is pre-existing
M8 code that calls Gemini and isn't part of this session's caregiver-command
work. What IS new here is everything from "a medication_candidate row
exists" onward - `check candidates`, the review prompt, yes/no, and
promote_candidate_to_medication(). So the candidate is seeded directly
(as if OCR had already run, matching M8's own output shape), and the
walkthrough starts from there. This does not re-verify the vision call
itself, which stays out of scope.
"""

import uuid
from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import select

from app.channels import media as media_channel
from app.db.models.contact import Contact
from app.db.models.location import SafeZone
from app.db.models.media import MediaFile
from app.db.models.medication import Medication, MedicationCandidate
from app.db.models.message import Conversation, Message
from app.db.models.user import User
from app.db.session import async_session
from app.pipelines import contact as contact_pipeline
from app.pipelines import text as text_pipeline

PATIENT_PHONE = "+6592000001"
CAREGIVER_PHONE = "+6592000002"

VCARD = (
    "BEGIN:VCARD\r\n"
    "VERSION:3.0\r\n"
    "FN:Alice Tan\r\n"
    f"TEL:{CAREGIVER_PHONE}\r\n"
    "END:VCARD\r\n"
)


class _FakeMedia:
    def __init__(self, remote_url: str) -> None:
        self.remote_url = remote_url
        self.mime_type = "text/vcard"


@pytest_asyncio.fixture
async def demo_pair():
    """Same shape as tests/test_caregiver_walkthrough.py's fixture, minus the
    pre-seeded caregiver link - some tests here build that link themselves
    (the contact-card test), others need it already active (everything
    else), so the caregiver's own `users` row and the `contacts` link are
    created explicitly per test instead of unconditionally here.
    """
    async with async_session() as session:
        patient = User(
            wa_id=f"wa-p-{uuid.uuid4().hex[:10]}",
            phone_e164=PATIENT_PHONE,
            display_name="Mary",
            preferred_language="en",
            timezone="Asia/Singapore",
            role="patient",
            last_inbound_at=datetime.now(UTC),
        )
        session.add(patient)
        await session.flush()
        patient_conversation = Conversation(user_id=patient.id)
        session.add(patient_conversation)
        await session.flush()
        ids = {
            "patient_id": patient.id,
            "patient_conversation_id": patient_conversation.id,
        }
        await session.commit()

    yield ids

    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.phone_e164.in_([PATIENT_PHONE, CAREGIVER_PHONE]))
        )
        for user in result.scalars().all():
            await session.delete(user)
        await session.commit()


async def _link_caregiver(ids: dict, *, relationship: str = "caregiver") -> None:
    """Seeds the state that phase-2 activation and every `set` command
    dispatch depends on: a `contacts` row on the patient with
    relationship='caregiver' (docs/17 §4: this row IS the link, there is no
    caregiver_links table)."""
    async with async_session() as session:
        session.add(
            Contact(
                user_id=ids["patient_id"],
                display_name="Alice",
                phone_e164=CAREGIVER_PHONE,
                relationship=relationship,
                is_emergency=True,
                priority=1,
                source="vcard",
            )
        )
        await session.commit()


async def _ensure_caregiver_conversation(ids: dict) -> None:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.phone_e164 == CAREGIVER_PHONE))
        caregiver = result.scalars().first()
        if caregiver is None:
            caregiver = User(
                wa_id=f"wa-c-{uuid.uuid4().hex[:10]}",
                phone_e164=CAREGIVER_PHONE,
                display_name="Alice",
                preferred_language="en",
                timezone="Asia/Singapore",
                role="patient",
                last_inbound_at=datetime.now(UTC),
            )
            session.add(caregiver)
            await session.flush()
        result = await session.execute(
            select(Conversation).where(Conversation.user_id == caregiver.id)
        )
        conversation = result.scalars().first()
        if conversation is None:
            conversation = Conversation(user_id=caregiver.id)
            session.add(conversation)
            await session.flush()
        await session.commit()
        ids["caregiver_id"] = caregiver.id
        ids["caregiver_conversation_id"] = conversation.id


async def _says(*, user_key: str, conversation_key: str, ids: dict, text: str) -> str:
    async with async_session() as session:
        message = Message(
            conversation_id=ids[conversation_key],
            user_id=ids[user_key],
            direction="inbound",
            channel_sid=f"SM{uuid.uuid4().hex}",
            kind="text",
            body=text,
            status="received",
        )
        session.add(message)
        speaker = await session.get(User, ids[user_key])
        speaker.last_inbound_at = datetime.now(UTC)
        await session.commit()
        message_id = message.id

    await text_pipeline.handle(
        user_id=ids[user_key],
        conversation_id=ids[conversation_key],
        message_id=message_id,
        text=text,
    )

    async with async_session() as session:
        result = await session.execute(
            select(Message)
            .where(Message.user_id == ids[user_key], Message.direction == "outbound")
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        latest = result.scalars().first()
        return latest.body if latest else ""


async def _patient_says(ids: dict, text: str) -> str:
    return await _says(
        user_key="patient_id", conversation_key="patient_conversation_id", ids=ids, text=text
    )


async def _caregiver_says(ids: dict, text: str) -> str:
    return await _says(
        user_key="caregiver_id", conversation_key="caregiver_conversation_id", ids=ids, text=text
    )


async def _activate_caregiver(ids: dict) -> str:
    await _ensure_caregiver_conversation(ids)
    return await _caregiver_says(ids, "hi")


# --- contact card -> caregiver activation -----------------------------------


async def test_contact_card_through_to_caregiver_activation(demo_pair, monkeypatch):
    ids = demo_pair

    async def _fake_download_media(_url: str) -> bytes:
        return VCARD.encode("utf-8")

    monkeypatch.setattr(media_channel, "download_media", _fake_download_media)

    # 1) Patient shares the contact card.
    async with async_session() as session:
        message = Message(
            conversation_id=ids["patient_conversation_id"],
            user_id=ids["patient_id"],
            direction="inbound",
            channel_sid=f"SM{uuid.uuid4().hex}",
            kind="contact",
            body=None,
            status="received",
        )
        session.add(message)
        await session.commit()
        contact_message_id = message.id

    await contact_pipeline.handle(
        user_id=ids["patient_id"],
        conversation_id=ids["patient_conversation_id"],
        message_id=contact_message_id,
        media=_FakeMedia("https://fake-media.example/vcard"),
    )

    async with async_session() as session:
        result = await session.execute(select(Contact).where(Contact.user_id == ids["patient_id"]))
        contact = result.scalars().first()
    assert contact is not None
    assert contact.phone_e164 == CAREGIVER_PHONE
    assert contact.is_emergency is False
    assert contact.relationship is None

    # 2) Q1 - "call them in an emergency?" - yes.
    q1 = await _patient_says(ids, "yes")
    assert "Alice" in q1

    async with async_session() as session:
        contact = await session.get(Contact, contact.id)
    assert contact.is_emergency is True

    # 3) Q2 - "caregiver as well?" - yes.
    q2 = await _patient_says(ids, "yes")
    assert "caregiver" in q2.lower()

    async with async_session() as session:
        contact = await session.get(Contact, contact.id)
    assert contact.relationship == "caregiver"

    # 4) The caregiver's own first message activates them.
    activation = await _activate_caregiver(ids)
    assert "caregiver" in activation.lower()
    assert "Mary" in activation

    async with async_session() as session:
        caregiver = await session.get(User, ids["caregiver_id"])
        assert caregiver.role == "caregiver"


# --- check candidates -> promote --------------------------------------------


async def test_check_candidates_yes_promotes_to_medication(demo_pair):
    # Schedule step uses the autouse stub_medication_schedule_parse fixture's
    # default result ("once a day, in the morning") - no override needed,
    # the free text below just needs to be plausible for a human reading it.
    ids = demo_pair
    await _link_caregiver(ids)
    await _activate_caregiver(ids)

    async with async_session() as session:
        inbound = Message(
            conversation_id=ids["patient_conversation_id"],
            user_id=ids["patient_id"],
            direction="inbound",
            channel_sid=f"SM{uuid.uuid4().hex}",
            kind="image",
            body=None,
            status="received",
        )
        session.add(inbound)
        await session.flush()
        media = MediaFile(
            message_id=inbound.id,
            kind="image",
            mime_type="image/jpeg",
            size_bytes=1024,
            storage_path="/data/media/fake-pill-bottle.jpg",
            sha256="0" * 64,
        )
        session.add(media)
        await session.flush()
        # Matches the shape M8's vision extraction produces (image.py ->
        # candidates_service.create_candidate()) - seeded directly since the
        # Gemini vision call itself is out of scope here.
        candidate = MedicationCandidate(
            patient_id=ids["patient_id"],
            source_media_id=media.id,
            extracted={
                "text_verbatim": "Donepezil 5mg",
                "structured": {"drug_name": "Donepezil", "dose": "5mg"},
            },
            confidence=0.9,
            status="pending",
        )
        session.add(candidate)
        await session.commit()
        candidate_id = candidate.id

    review_prompt = await _caregiver_says(ids, "check candidates")
    assert "Donepezil" in review_prompt or "5mg" in review_prompt

    ask_dose = await _caregiver_says(ids, "yes")
    assert ask_dose

    ask_confirm = await _caregiver_says(ids, "5mg")
    confirm = await _caregiver_says(ids, "once a day in the morning")
    assert confirm != ask_confirm
    assert "Donepezil" in confirm

    async with async_session() as session:
        result = await session.execute(
            select(Medication).where(Medication.patient_id == ids["patient_id"])
        )
        assert result.scalars().all() == []

    saved = await _caregiver_says(ids, "yes")
    assert "Donepezil" in saved

    async with async_session() as session:
        result = await session.execute(
            select(Medication).where(Medication.patient_id == ids["patient_id"])
        )
        medications = result.scalars().all()
        assert len(medications) == 1
        medication = medications[0]
        assert medication.name == "Donepezil"
        assert medication.dose_text == "5mg"
        assert medication.verified_by == ids["caregiver_id"]

        candidate = await session.get(MedicationCandidate, candidate_id)
        assert candidate.status == "approved"
        assert candidate.reviewed_by == ids["caregiver_id"]
        assert candidate.reviewed_at is not None


async def test_check_candidates_no_pending_says_so(demo_pair):
    ids = demo_pair
    await _link_caregiver(ids)
    await _activate_caregiver(ids)

    reply = await _caregiver_says(ids, "check candidates")
    assert "no" in reply.lower() or "none" in reply.lower() or "candidate" in reply.lower()


# --- set bloodwork -----------------------------------------------------------


async def test_set_bloodwork_text_intake(demo_pair):
    ids = demo_pair
    await _link_caregiver(ids)
    await _activate_caregiver(ids)

    ask_intake = await _caregiver_says(ids, "set bloodwork")
    assert ask_intake

    saved_prompt = await _caregiver_says(ids, "Blood Type AB+, Hemoglobin 13.2 g/dL, normal range.")
    assert saved_prompt  # still in the intake loop, ready for more or "done"

    done_reply = await _caregiver_says(ids, "done")
    assert done_reply

    from app.db.models.document import Document

    async with async_session() as session:
        result = await session.execute(
            select(Document).where(
                Document.patient_id == ids["patient_id"], Document.doc_kind == "blood_work"
            )
        )
        docs = result.scalars().all()
        assert len(docs) == 1
        assert "AB+" in docs[0].extracted_text
        assert docs[0].media_id is None  # typed text, no photo/PDF behind it


async def test_patient_can_read_back_bloodwork_after_set(demo_pair):
    ids = demo_pair
    await _link_caregiver(ids)
    await _activate_caregiver(ids)
    await _caregiver_says(ids, "set bloodwork")
    await _caregiver_says(ids, "Blood Type O-, cholesterol normal.")
    await _caregiver_says(ids, "done")

    reply = await _patient_says(ids, "what's my blood type")
    assert "O-" in reply or "O" in reply


# --- set address --------------------------------------------------------------


async def test_set_address(demo_pair):
    ids = demo_pair
    await _link_caregiver(ids)
    await _activate_caregiver(ids)

    ask_address = await _caregiver_says(ids, "set address")
    assert ask_address

    saved = await _caregiver_says(ids, "12 Toa Payoh Lorong 3, #05-123, Singapore 310012")
    assert saved

    async with async_session() as session:
        result = await session.execute(
            select(SafeZone).where(SafeZone.user_id == ids["patient_id"], SafeZone.kind == "home")
        )
        zone = result.scalars().first()
        assert zone is not None
        assert "Toa Payoh" in zone.address


async def test_patient_can_read_back_home_address(demo_pair):
    ids = demo_pair
    await _link_caregiver(ids)
    await _activate_caregiver(ids)
    await _caregiver_says(ids, "set address")
    await _caregiver_says(ids, "88 Bishan Street 22")

    reply = await _patient_says(ids, "where's my home")
    assert "Bishan" in reply


# --- Chinese-language set appointment / set medication ------------------------


async def test_set_appointment_in_chinese(demo_pair):
    ids = demo_pair
    await _link_caregiver(ids)
    await _activate_caregiver(ids)

    ask_datetime = await _caregiver_says(ids, "设置预约")
    assert ask_datetime

    ask_location = await _caregiver_says(ids, "明天下午3点")
    assert ask_location != ask_datetime

    ask_purpose = await _caregiver_says(ids, "中央医院")
    assert ask_purpose != ask_location

    confirm = await _caregiver_says(ids, "心脏科复诊")
    assert confirm

    saved = await _caregiver_says(ids, "是的")
    assert saved

    from app.db.models.calendar import CalendarEvent

    async with async_session() as session:
        result = await session.execute(
            select(CalendarEvent).where(CalendarEvent.user_id == ids["patient_id"])
        )
        events = result.scalars().all()
        assert len(events) == 1
        assert events[0].summary == "心脏科复诊"
        assert events[0].location == "中央医院"


async def test_set_medication_in_chinese(demo_pair, stub_medication_schedule_parse):
    stub_medication_schedule_parse(
        {
            "rrule": "FREQ=DAILY;BYHOUR=8,20;BYMINUTE=0",
            "label_en": "twice a day, morning and evening",
            "label_zh": "每天两次，早晚各一次",
        }
    )
    ids = demo_pair
    await _link_caregiver(ids)
    await _activate_caregiver(ids)

    await _caregiver_says(ids, "设置用药")
    await _caregiver_says(ids, "美金刚")
    await _caregiver_says(ids, "一片")
    confirm = await _caregiver_says(ids, "每天两次，早晚各一次")
    assert "美金刚" in confirm

    saved = await _caregiver_says(ids, "是的")
    assert "美金刚" in saved

    async with async_session() as session:
        result = await session.execute(
            select(Medication).where(Medication.patient_id == ids["patient_id"])
        )
        medications = result.scalars().all()
        assert len(medications) == 1
        medication = medications[0]
        assert medication.name == "美金刚"
        assert medication.instruction_zh == "每天两次，早晚各一次服用一片。"
        assert medication.verified_by == ids["caregiver_id"]
