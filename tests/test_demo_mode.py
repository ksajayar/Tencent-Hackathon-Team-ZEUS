"""DEMO_MODE end-to-end coverage: auto-provision + clone on first contact (no
caregiver step), per-number isolation, the proactive Google-connect link, and
the SOS simulation branch. Driven through the real service/pipeline functions
against real Postgres - same reasoning as
tests/test_caregiver_walkthrough.py's module docstring: this proves the logic
and the persistence, not that a WhatsApp message was actually delivered
(stub_twilio_send, conftest.py, stands in for that).

settings.demo_mode defaults to False (app/core/config.py) and is monkeypatched
True only inside the tests that need it via the `demo_mode_on` fixture -
test_sos_real_path_still_runs_when_demo_mode_off is the explicit regression
proof that the real SOS path is untouched when the flag is off.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.core.config import settings
from app.db.models.calendar import CalendarEvent
from app.db.models.contact import Contact
from app.db.models.document import Document
from app.db.models.location import SafeZone
from app.db.models.medication import Medication
from app.db.models.message import Conversation
from app.db.models.sos import SosEvent
from app.db.models.user import User
from app.db.session import async_session
from app.pipelines import demo as demo_pipeline
from app.safety import sos
from app.services import conversation as conversation_service
from app.services import demo as demo_service
from tests.conftest import unique_wa_id


@pytest.fixture
def demo_mode_on(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)


@pytest_asyncio.fixture
async def seeded_template():
    """The template patient/demo caregiver are a shared, idempotent singleton
    (reserved wa_id) - not torn down between tests, same as seed.py's
    CAREGIVER_WA_ID sentinel never being deleted by the existing suite."""
    async with async_session() as session:
        await demo_service.ensure_demo_template(session)
        await session.commit()


async def _make_new_patient(phone: str) -> dict:
    async with async_session() as session:
        patient = User(
            wa_id=unique_wa_id("demo-patient"),
            phone_e164=phone,
            display_name=None,
            role="patient",
            last_inbound_at=datetime.now(UTC),
        )
        session.add(patient)
        await session.flush()
        conversation = Conversation(user_id=patient.id)
        session.add(conversation)
        await session.flush()
        ids = {"patient_id": patient.id, "conversation_id": conversation.id}
        await session.commit()
    return ids


async def _cleanup_user(user_id) -> None:
    async with async_session() as session:
        await session.execute(delete(User).where(User.id == user_id))
        await session.commit()


async def test_clone_populates_verified_rows(demo_mode_on, seeded_template):
    ids = await _make_new_patient("+6593000001")
    try:
        async with async_session() as session:
            patient = await session.get(User, ids["patient_id"])
            await demo_service.clone_template_for_patient(session, patient)
            await session.commit()

        async with async_session() as session:
            medications = (
                (
                    await session.execute(
                        select(Medication).where(Medication.patient_id == ids["patient_id"])
                    )
                )
                .scalars()
                .all()
            )
            assert len(medications) == 3
            # SAFETY-1: get_active_medications() and the reminder scheduler
            # gate on verified_by being non-null - a cloned medication must
            # be indistinguishable from a caregiver-verified one.
            assert all(m.verified_by is not None and m.active for m in medications)

            zones = (
                (
                    await session.execute(
                        select(SafeZone).where(SafeZone.user_id == ids["patient_id"])
                    )
                )
                .scalars()
                .all()
            )
            assert {z.kind for z in zones} == {"home", "shop"}
            home = next(z for z in zones if z.kind == "home")
            assert home.address

            document = (
                (
                    await session.execute(
                        select(Document).where(
                            Document.patient_id == ids["patient_id"],
                            Document.doc_kind == "blood_work",
                        )
                    )
                )
                .scalars()
                .first()
            )
            assert document is not None
            assert "O+" in document.summary_en

            event = (
                (
                    await session.execute(
                        select(CalendarEvent).where(CalendarEvent.user_id == ids["patient_id"])
                    )
                )
                .scalars()
                .first()
            )
            assert event is not None
            assert event.start_at > datetime.now(UTC)  # always a future appointment

            contact = (
                (await session.execute(select(Contact).where(Contact.user_id == ids["patient_id"])))
                .scalars()
                .first()
            )
            assert contact is not None
            assert contact.is_emergency is True
    finally:
        await _cleanup_user(ids["patient_id"])


async def test_clone_is_isolated_per_patient(demo_mode_on, seeded_template):
    """Requirement 3: "clone, don't share" - concurrent judges never collide
    or edit each other's data."""
    ids_a = await _make_new_patient("+6593000002")
    ids_b = await _make_new_patient("+6593000003")
    try:
        async with async_session() as session:
            patient_a = await session.get(User, ids_a["patient_id"])
            await demo_service.clone_template_for_patient(session, patient_a)
            patient_b = await session.get(User, ids_b["patient_id"])
            await demo_service.clone_template_for_patient(session, patient_b)
            await session.commit()

        async with async_session() as session:
            meds_a = (
                (
                    await session.execute(
                        select(Medication).where(Medication.patient_id == ids_a["patient_id"])
                    )
                )
                .scalars()
                .all()
            )
            meds_b = (
                (
                    await session.execute(
                        select(Medication).where(Medication.patient_id == ids_b["patient_id"])
                    )
                )
                .scalars()
                .all()
            )
            assert {m.id for m in meds_a}.isdisjoint({m.id for m in meds_b})
            assert {m.name for m in meds_a} == {m.name for m in meds_b}
    finally:
        await _cleanup_user(ids_a["patient_id"])
        await _cleanup_user(ids_b["patient_id"])


async def test_ensure_demo_template_is_idempotent():
    async with async_session() as session:
        await demo_service.ensure_demo_template(session)
        await session.commit()

    async with async_session() as session:
        template = (
            await session.execute(
                select(User).where(User.wa_id == demo_service.DEMO_TEMPLATE_WA_ID)
            )
        ).scalar_one()
        template_id = template.id
        meds_first = (
            (await session.execute(select(Medication).where(Medication.patient_id == template_id)))
            .scalars()
            .all()
        )

    async with async_session() as session:
        await demo_service.ensure_demo_template(session)
        await session.commit()

    async with async_session() as session:
        meds_second = (
            (await session.execute(select(Medication).where(Medication.patient_id == template_id)))
            .scalars()
            .all()
        )

    assert len(meds_first) == len(meds_second) == 3


async def test_provisioning_messages_sends_welcome_and_google_link(
    demo_mode_on, seeded_template, stub_twilio_send
):
    ids = await _make_new_patient("+6593000004")
    try:
        async with async_session() as session:
            patient = await session.get(User, ids["patient_id"])
            await demo_service.clone_template_for_patient(session, patient)
            await session.commit()

        await demo_pipeline.send_provisioning_messages(
            user_id=ids["patient_id"], conversation_id=ids["conversation_id"]
        )

        bodies = [body for _to, body in stub_twilio_send]
        assert len(bodies) == 2
        assert any("/oauth/google/start" in body for body in bodies)
    finally:
        await _cleanup_user(ids["patient_id"])


async def test_sos_demo_routes_to_demo_caregiver_via_send_text(
    demo_mode_on, seeded_template, stub_twilio_send, monkeypatch
):
    ids = await _make_new_patient("+6593000005")
    try:
        async with async_session() as session:
            patient = await session.get(User, ids["patient_id"])
            await demo_service.clone_template_for_patient(session, patient)
            # Simulate the demo caregiver having already messaged the bot
            # once, so their window is open and the alert sends immediately
            # rather than queuing - both are valid demo-mode outcomes, but
            # asserting "sent" also proves the alert body is exactly what a
            # judge would see land on the demo caregiver's phone.
            caregiver = await demo_service.get_demo_caregiver(session)
            caregiver.last_inbound_at = datetime.now(UTC)
            await session.commit()

        from app.channels import outbound as outbound_module

        async def _fail_if_called(*args, **kwargs):
            raise AssertionError(
                "send_urgent (the window-bypass send) must never be called in demo mode"
            )

        monkeypatch.setattr(outbound_module, "send_urgent", _fail_if_called)

        async with async_session() as session:
            patient = await session.get(User, ids["patient_id"])
            reply = await sos.trigger(
                session, user=patient, trigger_text="help me", reply_language="en"
            )
            await session.commit()

        assert "caregiver" in reply.lower()

        async with async_session() as session:
            events = (
                (
                    await session.execute(
                        select(SosEvent).where(SosEvent.user_id == ids["patient_id"])
                    )
                )
                .scalars()
                .all()
            )
            assert len(events) == 1
            assert events[0].notified == [{"contact_id": "demo-caregiver", "outcome": "sent"}]

        demo_caregiver_sends = [body for _to, body in stub_twilio_send]
        assert any("URGENT" in body for body in demo_caregiver_sends)
    finally:
        await _cleanup_user(ids["patient_id"])


async def test_sos_real_path_still_runs_when_demo_mode_off():
    """Regression proof for the "off is byte-for-byte unchanged" constraint -
    this test does NOT use the demo_mode_on fixture, so settings.demo_mode is
    the real default (False), and sos.trigger() must take the pre-existing
    contact-lookup branch (SOS_NO_CONTACT, since this fresh patient has no
    emergency contacts), never the demo branch."""
    assert settings.demo_mode is False
    ids = await _make_new_patient("+6593000006")
    try:
        async with async_session() as session:
            patient = await session.get(User, ids["patient_id"])
            reply = await sos.trigger(
                session, user=patient, trigger_text="help", reply_language="en"
            )
            await session.commit()
        assert "995" in reply

        async with async_session() as session:
            events = (
                (
                    await session.execute(
                        select(SosEvent).where(SosEvent.user_id == ids["patient_id"])
                    )
                )
                .scalars()
                .all()
            )
            assert len(events) == 1
            assert events[0].notified == []
    finally:
        await _cleanup_user(ids["patient_id"])


async def test_get_or_create_user_reports_is_new():
    """Regression proof for the get_or_create_user signature change (now
    returns (user, is_new)) that app/api/webhooks.py's DEMO_MODE branch
    depends on to fire only on a number's genuine first message."""
    wa_id = unique_wa_id("get-or-create")
    async with async_session() as session:
        user1, is_new1 = await conversation_service.get_or_create_user(
            session, wa_id=wa_id, phone_e164="+6593000007", display_name="Test"
        )
        await session.commit()
        user1_id = user1.id
    assert is_new1 is True

    try:
        async with async_session() as session:
            user2, is_new2 = await conversation_service.get_or_create_user(
                session, wa_id=wa_id, phone_e164="+6593000007", display_name="Test"
            )
            await session.commit()
        assert is_new2 is False
        assert user2.id == user1_id
    finally:
        await _cleanup_user(user1_id)
