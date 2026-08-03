"""Document-pipeline fixes, from real use.

1. Formatting. pypdf's raw extract_text() (the degraded-mode fallback, used
   when Gemini is unavailable) wraps at the PDF's physical line boundaries,
   not word or sentence boundaries - a sentence routinely comes back split
   mid-word across ragged lines. _normalize_extracted_text collapses it
   into one readable block - purely mechanical whitespace reflow, never
   touching which characters are present (SAFETY-1: stays a verbatim
   transcript).

2. Caregiver routing. A PDF classified as prescription/lab_report/
   discharge_note is routed straight to the linked caregiver with the
   actual content, rather than shown to the patient with a disclaimer
   attached - document.py's reply never passes through medication_guard
   the way _general_qa/_caregiver_qa do, so showing it to the patient
   directly, even with a warning appended, would still be unguarded. This
   also means the patient does not have to be the one to relay it to their
   caregiver. Same routing in degraded mode (doc_kind unknown, so
   unconditional there). A caregiver uploading a PDF themselves (outside
   set-bloodwork intake) still sees the summary directly - they are
   already the trusted party, and routing "caregiver notifies their own
   caregiver" would be nonsensical.
"""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.channels import media as media_channel
from app.db.models.contact import Contact
from app.db.models.message import Conversation, Message
from app.db.models.user import User
from app.db.session import async_session
from app.pipelines import document as document_pipeline
from app.vision import pdf as pdf_processing
from tests.conftest import unique_wa_id

PATIENT_PHONE = "+6594000001"
CAREGIVER_PHONE = "+6594000002"
LONER_PATIENT_PHONE = "+6594000003"  # no linked caregiver
STANDALONE_CAREGIVER_PHONE = "+6594000004"  # uploads a PDF, not mid-intake


# --- 1. formatting -----------------------------------------------------------


def test_normalize_collapses_ragged_pypdf_linebreaks():
    ragged = "Patient: Mary Tan\nPrescrip-\ntion: Donepezil\n5mg   once\n\ndaily"
    cleaned = pdf_processing._normalize_extracted_text(ragged)
    assert "\n" not in cleaned
    assert "  " not in cleaned
    assert cleaned == "Patient: Mary Tan Prescrip- tion: Donepezil 5mg once daily"


def test_normalize_does_not_alter_characters_only_whitespace():
    text = "AB+  cholesterol\t180"
    cleaned = pdf_processing._normalize_extracted_text(text)
    assert sorted(cleaned.replace(" ", "")) == sorted(text.replace(" ", "").replace("\t", ""))


def test_normalize_empty_string():
    assert pdf_processing._normalize_extracted_text("") == ""


# --- 2. caregiver routing ----------------------------------------------------


class _FakeMedia:
    remote_url = "https://fake-media.example/doc.pdf"


def _stub_pdf_download_and_probe(monkeypatch):
    async def _fake_download(_url):
        return b"%PDF-fake%"

    monkeypatch.setattr(media_channel, "download_media", _fake_download)
    monkeypatch.setattr(media_channel, "sniff_mime_type", lambda _content: "application/pdf")

    async def _fake_probe(_content):
        return {"page_count": 1, "was_scanned": False}

    monkeypatch.setattr(pdf_processing, "probe", _fake_probe)

    async def _fake_store(_content, *, extension):
        return (f"/fake/path{extension}", "0" * 64)

    monkeypatch.setattr(media_channel, "store_media", _fake_store)


def _stub_gemini_summary(monkeypatch, *, doc_kind: str, summary_en: str):
    from app.ai import gemini_client

    async def _fake_summarize(*, pdf_bytes, pipeline, user_id):
        return {
            "doc_kind": doc_kind,
            "extracted_text": "raw text",
            "summary_en": summary_en,
            "summary_zh": "这是一份文件。",
        }

    monkeypatch.setattr(gemini_client, "summarize_document", _fake_summarize)


def _stub_gemini_unavailable(monkeypatch, *, extracted: str):
    from app.ai import gemini_client

    async def _fake_summarize(*, pdf_bytes, pipeline, user_id):
        return None

    monkeypatch.setattr(gemini_client, "summarize_document", _fake_summarize)

    async def _fake_extract(_content):
        return extracted

    monkeypatch.setattr(pdf_processing, "extract_first_paragraph", _fake_extract)


async def _last_reply(user_id) -> str:
    async with async_session() as session:
        result = await session.execute(
            select(Message)
            .where(Message.user_id == user_id, Message.direction == "outbound")
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        message = result.scalars().first()
        return message.body if message else ""


@pytest_asyncio.fixture
async def linked_pair():
    """A patient with a linked, already-active caregiver - the state
    find_caregiver_user depends on: a `contacts` row with
    relationship='caregiver', and the caregiver's own `users` row."""
    async with async_session() as session:
        patient = User(
            wa_id=unique_wa_id("doc"),
            phone_e164=PATIENT_PHONE,
            display_name="Mary",
            preferred_language="en",
            timezone="Asia/Singapore",
            role="patient",
            last_inbound_at=datetime.now(UTC),
        )
        caregiver = User(
            wa_id=unique_wa_id("doc-cg"),
            phone_e164=CAREGIVER_PHONE,
            display_name="Alice",
            preferred_language="en",
            timezone="Asia/Singapore",
            role="caregiver",
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
        patient_conversation = Conversation(user_id=patient.id)
        caregiver_conversation = Conversation(user_id=caregiver.id)
        session.add_all([patient_conversation, caregiver_conversation])
        await session.flush()
        message = Message(
            conversation_id=patient_conversation.id,
            user_id=patient.id,
            direction="inbound",
            channel_sid=f"SM{uuid.uuid4().hex}",
            kind="document",
            body=None,
            status="received",
        )
        session.add(message)
        await session.commit()
        ids = {
            "patient_id": patient.id,
            "caregiver_id": caregiver.id,
            "patient_conversation_id": patient_conversation.id,
            "message_id": message.id,
        }
    yield ids
    async with async_session() as session:
        for uid in (ids["patient_id"], ids["caregiver_id"]):
            obj = await session.get(User, uid)
            if obj is not None:
                await session.delete(obj)
        await session.commit()


@pytest_asyncio.fixture
async def unlinked_patient():
    async with async_session() as session:
        patient = User(
            wa_id=unique_wa_id("doc-loner"),
            phone_e164=LONER_PATIENT_PHONE,
            display_name="Mary",
            preferred_language="en",
            timezone="Asia/Singapore",
            role="patient",
            last_inbound_at=datetime.now(UTC),
        )
        session.add(patient)
        await session.flush()
        conversation = Conversation(user_id=patient.id)
        session.add(conversation)
        await session.flush()
        message = Message(
            conversation_id=conversation.id,
            user_id=patient.id,
            direction="inbound",
            channel_sid=f"SM{uuid.uuid4().hex}",
            kind="document",
            body=None,
            status="received",
        )
        session.add(message)
        await session.commit()
        ids = {
            "patient_id": patient.id,
            "conversation_id": conversation.id,
            "message_id": message.id,
        }
    yield ids
    async with async_session() as session:
        obj = await session.get(User, ids["patient_id"])
        if obj is not None:
            await session.delete(obj)
        await session.commit()


@pytest_asyncio.fixture
async def standalone_caregiver():
    """A caregiver uploading a PDF with no pending bloodwork intake - the
    edge case that must NOT try to route to "the caregiver's own
    caregiver"."""
    async with async_session() as session:
        caregiver = User(
            wa_id=unique_wa_id("doc-solo-cg"),
            phone_e164=STANDALONE_CAREGIVER_PHONE,
            display_name="Alice",
            preferred_language="en",
            timezone="Asia/Singapore",
            role="caregiver",
            last_inbound_at=datetime.now(UTC),
        )
        session.add(caregiver)
        await session.flush()
        conversation = Conversation(user_id=caregiver.id)
        session.add(conversation)
        await session.flush()
        message = Message(
            conversation_id=conversation.id,
            user_id=caregiver.id,
            direction="inbound",
            channel_sid=f"SM{uuid.uuid4().hex}",
            kind="document",
            body=None,
            status="received",
        )
        session.add(message)
        await session.commit()
        ids = {
            "caregiver_id": caregiver.id,
            "conversation_id": conversation.id,
            "message_id": message.id,
        }
    yield ids
    async with async_session() as session:
        obj = await session.get(User, ids["caregiver_id"])
        if obj is not None:
            await session.delete(obj)
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("doc_kind", ["prescription", "lab_report", "discharge_note"])
async def test_medication_relevant_pdf_routes_to_caregiver_not_patient(
    linked_pair, monkeypatch, doc_kind
):
    _stub_pdf_download_and_probe(monkeypatch)
    _stub_gemini_summary(monkeypatch, doc_kind=doc_kind, summary_en="Donepezil 5mg prescribed.")

    await document_pipeline.handle(
        user_id=linked_pair["patient_id"],
        conversation_id=linked_pair["patient_conversation_id"],
        message_id=linked_pair["message_id"],
        media=_FakeMedia(),
    )

    patient_reply = await _last_reply(linked_pair["patient_id"])
    assert "let your caregiver know" in patient_reply.lower()
    # The clinical content must NOT reach the patient - the whole point.
    assert "donepezil" not in patient_reply.lower()

    caregiver_reply = await _last_reply(linked_pair["caregiver_id"])
    assert "Donepezil 5mg prescribed" in caregiver_reply
    assert "Mary" in caregiver_reply


@pytest.mark.asyncio
async def test_appointment_letter_still_goes_straight_to_the_patient(linked_pair, monkeypatch):
    _stub_pdf_download_and_probe(monkeypatch)
    _stub_gemini_summary(
        monkeypatch, doc_kind="appointment_letter", summary_en="Appointment next Tuesday."
    )

    await document_pipeline.handle(
        user_id=linked_pair["patient_id"],
        conversation_id=linked_pair["patient_conversation_id"],
        message_id=linked_pair["message_id"],
        media=_FakeMedia(),
    )

    patient_reply = await _last_reply(linked_pair["patient_id"])
    assert "Appointment next Tuesday" in patient_reply
    assert "read this to you" in patient_reply.lower()

    # No caregiver notification for non-medication-relevant content.
    caregiver_reply = await _last_reply(linked_pair["caregiver_id"])
    assert caregiver_reply == ""


@pytest.mark.asyncio
async def test_prescription_with_no_linked_caregiver_still_hides_content_from_patient(
    unlinked_patient, monkeypatch
):
    _stub_pdf_download_and_probe(monkeypatch)
    _stub_gemini_summary(monkeypatch, doc_kind="prescription", summary_en="Panadol prescribed.")

    await document_pipeline.handle(
        user_id=unlinked_patient["patient_id"],
        conversation_id=unlinked_patient["conversation_id"],
        message_id=unlinked_patient["message_id"],
        media=_FakeMedia(),
    )

    reply = await _last_reply(unlinked_patient["patient_id"])
    assert "panadol" not in reply.lower()
    assert "let your caregiver know" in reply.lower()


@pytest.mark.asyncio
async def test_degraded_mode_routes_raw_extract_to_caregiver(linked_pair, monkeypatch):
    _stub_pdf_download_and_probe(monkeypatch)
    _stub_gemini_unavailable(monkeypatch, extracted="Ragged   pypdf\ntext with Donepezil")

    await document_pipeline.handle(
        user_id=linked_pair["patient_id"],
        conversation_id=linked_pair["patient_conversation_id"],
        message_id=linked_pair["message_id"],
        media=_FakeMedia(),
    )

    patient_reply = await _last_reply(linked_pair["patient_id"])
    assert "let your caregiver know" in patient_reply.lower()
    assert "donepezil" not in patient_reply.lower()

    caregiver_reply = await _last_reply(linked_pair["caregiver_id"])
    assert "Donepezil" in caregiver_reply


@pytest.mark.asyncio
async def test_caregiver_uploading_directly_sees_the_summary_not_a_routing_loop(
    standalone_caregiver, monkeypatch
):
    # A caregiver sending a PDF outside set-bloodwork intake must not
    # trigger find_caregiver_user(their own id) - that would look for
    # their own caregiver (never found) and reply with the wrong-voiced
    # "let your caregiver know" line to the trusted party itself.
    _stub_pdf_download_and_probe(monkeypatch)
    _stub_gemini_summary(monkeypatch, doc_kind="prescription", summary_en="Panadol prescribed.")

    await document_pipeline.handle(
        user_id=standalone_caregiver["caregiver_id"],
        conversation_id=standalone_caregiver["conversation_id"],
        message_id=standalone_caregiver["message_id"],
        media=_FakeMedia(),
    )

    reply = await _last_reply(standalone_caregiver["caregiver_id"])
    assert "Panadol prescribed" in reply
    assert "let your caregiver know" not in reply.lower()
