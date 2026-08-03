import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import gemini_client
from app.channels import media as media_channel
from app.channels import outbound
from app.channels.base import MediaRef
from app.channels.media import MediaTooLargeError
from app.core.logging import get_logger
from app.db.models.media import MediaFile
from app.db.models.user import User
from app.db.session import async_session
from app.i18n.strings import (
    CAREGIVER_BLOODWORK_MEDIA_SAVED,
    DOCUMENT_CHECK_WITH_CAREGIVER,
    DOCUMENT_DEGRADED_CHECK_WITH_CAREGIVER,
    DOCUMENT_DEGRADED_EMPTY,
    DOCUMENT_DEGRADED_PREFIX,
    DOCUMENT_OFFER_VOICE,
    DOCUMENT_TOO_LONG,
    DOCUMENT_UNREADABLE,
)
from app.pipelines import caregiver as caregiver_pipeline
from app.services import documents as documents_service
from app.vision import pdf as pdf_processing

logger = get_logger(__name__)

# doc_kind values (per _DOCUMENT_PROMPT in gemini_client.py) that can carry
# medication or health-result content the patient must not act on alone -
# discharge_note included since a hospital discharge routinely introduces or
# changes medications. appointment_letter/other excluded: no medical
# instruction content expected there.
_MEDICATION_RELEVANT_DOC_KINDS = frozenset({"prescription", "lab_report", "discharge_note"})


def _pick(bilingual: dict, language: str) -> str:
    return bilingual.get(language, bilingual["en"])


async def handle(
    *, user_id: uuid.UUID, conversation_id: uuid.UUID, message_id: uuid.UUID, media: MediaRef
) -> None:
    """§05 §5.4: download -> size/page check -> text probe -> Gemini document
    -> summarise -> DB -> reply. Same LANG-1 fallback reasoning as the image
    pipeline: no text on the inbound message, so `preferred_language` decides
    the reply language."""
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is None:
            logger.warning("document_pipeline_user_missing", user_id=str(user_id))
            return
        reply_language = user.preferred_language

        # §17 set bloodwork: unlike image.py, a PDF never gets misrouted to
        # medication_candidates (that vision-only bug doesn't exist here -
        # every PDF already lands in `documents` regardless of doc_kind).
        # This just needs to redirect doc_kind/patient_id to the caregiver's
        # linked patient and keep the intake flow open, once resolved here.
        bloodwork_patient = None
        if user.role == "caregiver":
            bloodwork_patient = await caregiver_pipeline.get_pending_bloodwork_patient(
                session, user
            )

        try:
            content = await media_channel.download_media(media.remote_url)
        except MediaTooLargeError:
            logger.warning("document_too_large", user_id=str(user_id))
            await outbound.send_text(
                session, user, conversation_id, _pick(DOCUMENT_TOO_LONG, reply_language)
            )
            await session.commit()
            return
        except Exception:
            logger.exception("document_download_failed", user_id=str(user_id))
            await outbound.send_text(
                session, user, conversation_id, _pick(DOCUMENT_UNREADABLE, reply_language)
            )
            await session.commit()
            return

        sniffed_mime = media_channel.sniff_mime_type(content)
        if sniffed_mime != "application/pdf":
            logger.warning(
                "document_media_not_pdf", user_id=str(user_id), sniffed_mime=sniffed_mime
            )
            await outbound.send_text(
                session, user, conversation_id, _pick(DOCUMENT_UNREADABLE, reply_language)
            )
            await session.commit()
            return

        try:
            probe = await pdf_processing.probe(content)
        except Exception:
            logger.exception("document_probe_failed", user_id=str(user_id))
            await outbound.send_text(
                session, user, conversation_id, _pick(DOCUMENT_UNREADABLE, reply_language)
            )
            await session.commit()
            return

        storage_path, sha256 = await media_channel.store_media(content, extension=".pdf")
        media_file = MediaFile(
            message_id=message_id,
            kind="document",
            mime_type="application/pdf",
            size_bytes=len(content),
            storage_path=storage_path,
            sha256=sha256,
            page_count=probe["page_count"],
        )
        session.add(media_file)
        await session.flush()

        if probe["page_count"] > pdf_processing.MAX_PDF_PAGES:
            logger.info(
                "document_too_many_pages", user_id=str(user_id), page_count=probe["page_count"]
            )
            await outbound.send_text(
                session, user, conversation_id, _pick(DOCUMENT_TOO_LONG, reply_language)
            )
            await session.commit()
            return

        result = await gemini_client.summarize_document(
            pdf_bytes=content, pipeline="document.summarize", user_id=user.id
        )

        if result is None:
            reply = await _degraded_reply(
                content, probe, media_file.id, session, reply_language, patient=bloodwork_patient
            )
            meta = caregiver_pipeline.bloodwork_intake_meta() if bloodwork_patient else None
            await outbound.send_text(session, user, conversation_id, reply, meta=meta)
            await session.commit()
            return

        await documents_service.create_document(
            session,
            media_id=media_file.id,
            doc_kind="blood_work" if bloodwork_patient else result["doc_kind"],
            extracted_text=result["extracted_text"] or None,
            summary_en=result["summary_en"] or None,
            summary_zh=result["summary_zh"] or None,
            was_scanned=probe["was_scanned"],
            patient_id=bloodwork_patient.id if bloodwork_patient else None,
        )

        if bloodwork_patient is not None:
            template = _pick(CAREGIVER_BLOODWORK_MEDIA_SAVED, reply_language)
            reply = template.format(patient_name=bloodwork_patient.display_name or "the patient")
            await outbound.send_text(
                session,
                user,
                conversation_id,
                reply,
                meta=caregiver_pipeline.bloodwork_intake_meta(),
            )
            await session.commit()
            return

        summary = _pick(
            {"en": result["summary_en"], "zh-Hans": result["summary_zh"]}, reply_language
        )
        reply = summary or _pick(DOCUMENT_DEGRADED_EMPTY, reply_language)
        if result["doc_kind"] in _MEDICATION_RELEVANT_DOC_KINDS:
            # Deterministic, not left to the model: _DOCUMENT_PROMPT only
            # offers "please check with your caregiver" as an example of
            # tone, so a genuine prescription summary could otherwise omit
            # it entirely if the model's wording happened not to include it.
            reply += _pick(DOCUMENT_CHECK_WITH_CAREGIVER, reply_language)
        reply += _pick(DOCUMENT_OFFER_VOICE, reply_language)
        await outbound.send_text(session, user, conversation_id, reply)
        await session.commit()


async def _degraded_reply(
    content: bytes,
    probe: dict,
    media_file_id: uuid.UUID,
    session: AsyncSession,
    reply_language: str,
    *,
    patient: User | None = None,
) -> str:
    """§05 §5.4 degraded mode: extract the first paragraph with pypdf and
    return it unsummarised, rather than nothing. `patient` set (§17): a
    caregiver's bloodwork PDF that hits this path still needs to redirect
    doc_kind/patient_id, same as the non-degraded branch above - a
    caregiver's document should never end up filed as an ordinary
    "other"-kind, ownerless row just because summarisation failed."""
    paragraph = await pdf_processing.extract_first_paragraph(content)
    await documents_service.create_document(
        session,
        media_id=media_file_id,
        doc_kind="blood_work" if patient else "other",
        extracted_text=paragraph or None,
        summary_en=None,
        summary_zh=None,
        was_scanned=probe["was_scanned"],
        patient_id=patient.id if patient else None,
    )
    if patient is not None:
        # Caregiver-voiced, not the patient-voiced strings below - this is
        # a caregiver uploading on someone else's behalf, even in the
        # degraded case.
        return _pick(CAREGIVER_BLOODWORK_MEDIA_SAVED, reply_language).format(
            patient_name=patient.display_name or "the patient"
        )
    if not paragraph:
        return _pick(DOCUMENT_DEGRADED_EMPTY, reply_language)
    # doc_kind is never known here - there was no Gemini call to classify
    # it, only pypdf's raw text - so this is unconditional rather than
    # gated on kind, same reasoning as the classified branch above but more
    # cautious: nothing about this document has been reviewed at all.
    return (
        _pick(DOCUMENT_DEGRADED_PREFIX, reply_language)
        + paragraph
        + _pick(DOCUMENT_DEGRADED_CHECK_WITH_CAREGIVER, reply_language)
    )
