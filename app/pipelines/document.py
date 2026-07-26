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
    DOCUMENT_DEGRADED_EMPTY,
    DOCUMENT_DEGRADED_PREFIX,
    DOCUMENT_OFFER_VOICE,
    DOCUMENT_TOO_LONG,
    DOCUMENT_UNREADABLE,
)
from app.services import documents as documents_service
from app.vision import pdf as pdf_processing

logger = get_logger(__name__)


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
            reply = await _degraded_reply(content, probe, media_file.id, session, reply_language)
            await outbound.send_text(session, user, conversation_id, reply)
            await session.commit()
            return

        await documents_service.create_document(
            session,
            media_id=media_file.id,
            doc_kind=result["doc_kind"],
            extracted_text=result["extracted_text"] or None,
            summary_en=result["summary_en"] or None,
            summary_zh=result["summary_zh"] or None,
            was_scanned=probe["was_scanned"],
        )

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
    reply_language: str,
) -> str:
    """§05 §5.4 degraded mode: extract the first paragraph with pypdf and
    return it unsummarised, rather than nothing."""
    paragraph = await pdf_processing.extract_first_paragraph(content)
    await documents_service.create_document(
        session,
        media_id=media_file_id,
        doc_kind="other",
        extracted_text=paragraph or None,
        summary_en=None,
        summary_zh=None,
        was_scanned=probe["was_scanned"],
    )
    if not paragraph:
        return _pick(DOCUMENT_DEGRADED_EMPTY, reply_language)
    return _pick(DOCUMENT_DEGRADED_PREFIX, reply_language) + paragraph
