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
    CAREGIVER_DOCUMENT_NOTIFY,
    DOCUMENT_DEGRADED_EMPTY,
    DOCUMENT_OFFER_VOICE,
    DOCUMENT_SAVED_FOR_CAREGIVER,
    DOCUMENT_TOO_LONG,
    DOCUMENT_UNREADABLE,
)
from app.pipelines import caregiver as caregiver_pipeline
from app.services import conversation as conversation_service
from app.services import documents as documents_service
from app.services.contacts import find_caregiver_user
from app.vision import pdf as pdf_processing

logger = get_logger(__name__)

# doc_kind values (per _DOCUMENT_PROMPT in gemini_client.py) that can carry
# medication or health-result content that must not reach the patient
# unguarded - document.py never runs a reply through medication_guard the
# way _general_qa/_caregiver_qa do (SAFETY-1), so these are routed to the
# caregiver instead of shown to the patient at all, rather than shown with
# a disclaimer attached. discharge_note included since a hospital discharge
# routinely introduces or changes medications. appointment_letter/other
# excluded: no medical instruction content expected there.
_MEDICATION_RELEVANT_DOC_KINDS = frozenset({"prescription", "lab_report", "discharge_note"})

_DOC_KIND_LABEL_EN = {
    "prescription": "a prescription",
    "lab_report": "a lab report",
    "discharge_note": "a discharge note",
}
_DOC_KIND_LABEL_ZH = {
    "prescription": "一份处方",
    "lab_report": "一份化验报告",
    "discharge_note": "一份出院记录",
}


def _pick(bilingual: dict, language: str) -> str:
    return bilingual.get(language, bilingual["en"])


async def _notify_caregiver_of_document(
    session: AsyncSession, patient: User, *, doc_kind: str, summary_en: str, summary_zh: str
) -> None:
    """Routes the document straight to the linked caregiver, with the actual
    content, rather than telling the patient to go relay it themselves - the
    patient does not have to do that work, and the caregiver can act (or
    just reply to the bot) on this one message instead of a second round
    trip through a review command. Same shape as image.py's
    _notify_caregiver_of_candidate. Best-effort: a failed lookup or send
    here must never affect the patient's own reply, already sent by the
    caller, so exceptions are logged and swallowed rather than propagated.
    """
    try:
        caregiver = await find_caregiver_user(session, patient.id)
        if caregiver is None:
            return
        conversation = await conversation_service.get_or_create_open_conversation(
            session, caregiver
        )
        label = _pick(
            {
                "en": _DOC_KIND_LABEL_EN.get(doc_kind, "a document"),
                "zh-Hans": _DOC_KIND_LABEL_ZH.get(doc_kind, "一份文件"),
            },
            caregiver.preferred_language,
        )
        summary = _pick({"en": summary_en, "zh-Hans": summary_zh}, caregiver.preferred_language)
        body = _pick(CAREGIVER_DOCUMENT_NOTIFY, caregiver.preferred_language).format(
            patient_name=patient.display_name or "the patient",
            doc_kind_label=label,
            summary=summary or "(no text could be read)",
        )
        await outbound.send_text(session, caregiver, conversation.id, body)
    except Exception:
        logger.exception("caregiver_document_notify_failed", patient_id=str(patient.id))


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
                content,
                probe,
                media_file.id,
                session,
                user,
                reply_language,
                patient=bloodwork_patient,
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

        if user.role == "patient" and result["doc_kind"] in _MEDICATION_RELEVANT_DOC_KINDS:
            # Routed to the caregiver instead of shown to the patient at
            # all - document.py's reply never passes through
            # medication_guard the way _general_qa/_caregiver_qa do
            # (SAFETY-1), so a genuine prescription's AI summary reaching
            # the patient directly would be unguarded. The caregiver gets
            # the actual content and can act without the patient relaying
            # anything.
            #
            # Gated on user.role=='patient': a caregiver CAN send an
            # arbitrary PDF outside of set-bloodwork intake (bloodwork_patient
            # is None here means "not mid-intake", not "not a caregiver").
            # Without this check, find_caregiver_user would look for the
            # caregiver's OWN caregiver (never found) and the caregiver
            # would be told "I've saved this and let your caregiver know" -
            # nonsensical sent to the trusted party. A caregiver already has
            # full access (same as _caregiver_qa), so they just see the
            # summary directly, same as before this change.
            await _notify_caregiver_of_document(
                session,
                user,
                doc_kind=result["doc_kind"],
                summary_en=result["summary_en"],
                summary_zh=result["summary_zh"],
            )
            reply = _pick(DOCUMENT_SAVED_FOR_CAREGIVER, reply_language)
        else:
            summary = _pick(
                {"en": result["summary_en"], "zh-Hans": result["summary_zh"]}, reply_language
            )
            reply = (summary or _pick(DOCUMENT_DEGRADED_EMPTY, reply_language)) + _pick(
                DOCUMENT_OFFER_VOICE, reply_language
            )
        await outbound.send_text(session, user, conversation_id, reply)
        await session.commit()


async def _degraded_reply(
    content: bytes,
    probe: dict,
    media_file_id: uuid.UUID,
    session: AsyncSession,
    user: User,
    reply_language: str,
    *,
    patient: User | None = None,
) -> str:
    """§05 §5.4 degraded mode: extract the first paragraph with pypdf,
    rather than nothing. `patient` set (§17): a caregiver's bloodwork PDF
    that hits this path still needs to redirect doc_kind/patient_id, same
    as the non-degraded branch above - a caregiver's document should never
    end up filed as an ordinary "other"-kind, ownerless row just because
    summarisation failed.

    `user` is the uploader - needed separately from `patient` (which is
    only set for the bloodwork-intake redirect) because the ordinary case
    below routes to the UPLOADER's own linked caregiver, same as the
    classified branch in handle().
    """
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
    if user.role != "patient":
        # A caregiver uploading outside of set-bloodwork intake (patient is
        # None here meaning "not mid-intake", not "not a caregiver") - see
        # the matching guard in handle() for why this can't route to
        # find_caregiver_user(user.id). Same pre-existing behaviour as
        # before this change: show them the raw excerpt directly.
        return paragraph
    # doc_kind is never known here - there was no Gemini call to classify
    # it, only pypdf's raw text. Routed to the caregiver unconditionally
    # rather than gated on kind like the classified branch in handle() -
    # same reasoning, more cautious: we don't even know what this is, so
    # it must not reach the patient unguarded either way.
    await _notify_caregiver_of_document(
        session, user, doc_kind="unclassified", summary_en=paragraph, summary_zh=paragraph
    )
    return _pick(DOCUMENT_SAVED_FOR_CAREGIVER, reply_language)
