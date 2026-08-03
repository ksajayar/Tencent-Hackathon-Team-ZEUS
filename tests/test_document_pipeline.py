"""Two document-pipeline fixes, both reported from real use.

1. Formatting. pypdf's raw extract_text() (the degraded-mode fallback, used
   when Gemini is unavailable) wraps at the PDF's physical line boundaries,
   not word or sentence boundaries - a sentence routinely comes back split
   mid-word across ragged lines. That went to the patient completely
   unprocessed. _normalize_extracted_text collapses it into one readable
   block - purely mechanical whitespace reflow, never touching which
   characters are present (SAFETY-1: stays a verbatim transcript).

2. Caregiver-check disclaimer. _DOCUMENT_PROMPT (gemini_client.py) only
   gives "please check with your caregiver" as an EXAMPLE of tone in the
   prompt text - nothing guarantees the model's summary actually includes
   it. A genuine prescription/lab_report/discharge_note PDF could reach the
   patient with no caregiver-check line at all. document.py now appends it
   deterministically, in code, the same discipline image.py's
   PILL_BOTTLE_SAVED_*/PRESCRIPTION_SAVED already use.
"""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from app.channels import media as media_channel
from app.db.models.message import Conversation, Message
from app.db.models.user import User
from app.db.session import async_session
from app.pipelines import document as document_pipeline
from app.vision import pdf as pdf_processing
from tests.conftest import unique_wa_id

PATIENT_PHONE = "+6594000001"


# --- 1. formatting -----------------------------------------------------------


def test_normalize_collapses_ragged_pypdf_linebreaks():
    ragged = "Patient: Mary Tan\nPrescrip-\ntion: Donepezil\n5mg   once\n\ndaily"
    cleaned = pdf_processing._normalize_extracted_text(ragged)
    assert "\n" not in cleaned
    assert "  " not in cleaned
    assert cleaned == "Patient: Mary Tan Prescrip- tion: Donepezil 5mg once daily"


def test_normalize_does_not_alter_characters_only_whitespace():
    # SAFETY-1: mechanical reflow only - no character is added, removed, or
    # reordered, so this can never introduce or drop content.
    text = "AB+  cholesterol\t180"
    cleaned = pdf_processing._normalize_extracted_text(text)
    assert sorted(cleaned.replace(" ", "")) == sorted(text.replace(" ", "").replace("\t", ""))


def test_normalize_empty_string():
    assert pdf_processing._normalize_extracted_text("") == ""


# --- 2. caregiver-check disclaimer ------------------------------------------


@pytest_asyncio.fixture
async def patient():
    async with async_session() as session:
        user = User(
            wa_id=unique_wa_id("doc"),
            phone_e164=PATIENT_PHONE,
            display_name="Mary",
            preferred_language="en",
            timezone="Asia/Singapore",
            role="patient",
            last_inbound_at=datetime.now(UTC),
        )
        session.add(user)
        await session.flush()
        conversation = Conversation(user_id=user.id)
        session.add(conversation)
        await session.flush()
        message = Message(
            conversation_id=conversation.id,
            user_id=user.id,
            direction="inbound",
            channel_sid=f"SM{uuid.uuid4().hex}",
            kind="document",
            body=None,
            status="received",
        )
        session.add(message)
        await session.commit()
        ids = {"user_id": user.id, "conversation_id": conversation.id, "message_id": message.id}
    yield ids
    async with async_session() as session:
        obj = await session.get(User, ids["user_id"])
        if obj is not None:
            await session.delete(obj)
        await session.commit()


class _FakeMedia:
    remote_url = "https://fake-media.example/doc.pdf"


def _stub_pdf_pipeline(monkeypatch, *, doc_kind: str, summary_en: str = "It's a document."):
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

    from app.ai import gemini_client

    async def _fake_summarize(*, pdf_bytes, pipeline, user_id):
        return {
            "doc_kind": doc_kind,
            "extracted_text": "raw text",
            "summary_en": summary_en,
            "summary_zh": "这是一份文件。",
        }

    monkeypatch.setattr(gemini_client, "summarize_document", _fake_summarize)


async def _last_reply(user_id) -> str:
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(
            select(Message)
            .where(Message.user_id == user_id, Message.direction == "outbound")
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        message = result.scalars().first()
        return message.body if message else ""


@pytest.mark.asyncio
async def test_prescription_pdf_gets_deterministic_caregiver_check(patient, monkeypatch):
    _stub_pdf_pipeline(monkeypatch, doc_kind="prescription", summary_en="Panadol prescribed.")
    await document_pipeline.handle(
        user_id=patient["user_id"],
        conversation_id=patient["conversation_id"],
        message_id=patient["message_id"],
        media=_FakeMedia(),
    )
    reply = await _last_reply(patient["user_id"])
    assert "check this with your caregiver" in reply.lower()


@pytest.mark.asyncio
async def test_lab_report_pdf_gets_deterministic_caregiver_check(patient, monkeypatch):
    _stub_pdf_pipeline(monkeypatch, doc_kind="lab_report", summary_en="Cholesterol normal.")
    await document_pipeline.handle(
        user_id=patient["user_id"],
        conversation_id=patient["conversation_id"],
        message_id=patient["message_id"],
        media=_FakeMedia(),
    )
    reply = await _last_reply(patient["user_id"])
    assert "check this with your caregiver" in reply.lower()


@pytest.mark.asyncio
async def test_discharge_note_pdf_gets_deterministic_caregiver_check(patient, monkeypatch):
    _stub_pdf_pipeline(monkeypatch, doc_kind="discharge_note", summary_en="Discharged Tuesday.")
    await document_pipeline.handle(
        user_id=patient["user_id"],
        conversation_id=patient["conversation_id"],
        message_id=patient["message_id"],
        media=_FakeMedia(),
    )
    reply = await _last_reply(patient["user_id"])
    assert "check this with your caregiver" in reply.lower()


@pytest.mark.asyncio
async def test_appointment_letter_pdf_does_not_get_the_medication_disclaimer(patient, monkeypatch):
    _stub_pdf_pipeline(
        monkeypatch, doc_kind="appointment_letter", summary_en="Appointment next Tuesday."
    )
    await document_pipeline.handle(
        user_id=patient["user_id"],
        conversation_id=patient["conversation_id"],
        message_id=patient["message_id"],
        media=_FakeMedia(),
    )
    reply = await _last_reply(patient["user_id"])
    assert "check this with your caregiver" not in reply.lower()


@pytest.mark.asyncio
async def test_disclaimer_still_offers_to_read_it_aloud(patient, monkeypatch):
    # The two additions must stack, not replace each other - docs/09's
    # existing voice offer must not be lost when the disclaimer is added.
    _stub_pdf_pipeline(monkeypatch, doc_kind="prescription", summary_en="Panadol prescribed.")
    await document_pipeline.handle(
        user_id=patient["user_id"],
        conversation_id=patient["conversation_id"],
        message_id=patient["message_id"],
        media=_FakeMedia(),
    )
    reply = await _last_reply(patient["user_id"])
    assert "read this to you" in reply.lower()
    assert reply.index("check this with your caregiver") < reply.index("read this to you")


@pytest.mark.asyncio
async def test_degraded_mode_always_includes_a_caregiver_check(patient, monkeypatch):
    # No Gemini classification available here at all, so this cannot be
    # gated on doc_kind - unconditional and more cautious instead.
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

    async def _fake_extract_first_paragraph(_content):
        return "Ragged   pypdf\ntext here"

    monkeypatch.setattr(pdf_processing, "extract_first_paragraph", _fake_extract_first_paragraph)

    from app.ai import gemini_client

    async def _fake_summarize(*, pdf_bytes, pipeline, user_id):
        return None  # Gemini unavailable -> degraded path

    monkeypatch.setattr(gemini_client, "summarize_document", _fake_summarize)

    await document_pipeline.handle(
        user_id=patient["user_id"],
        conversation_id=patient["conversation_id"],
        message_id=patient["message_id"],
        media=_FakeMedia(),
    )
    reply = await _last_reply(patient["user_id"])
    assert "hasn't been reviewed" in reply.lower()
