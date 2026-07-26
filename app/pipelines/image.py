import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import gemini_client
from app.channels import media as media_channel
from app.channels import outbound
from app.channels.base import MediaRef
from app.core.logging import get_logger
from app.db.models.location import LocationPing
from app.db.models.media import MediaFile
from app.db.models.user import User
from app.db.session import async_session
from app.i18n.strings import (
    IMAGE_UNREADABLE,
    PILL_BOTTLE_SAVED_GENERIC,
    PILL_BOTTLE_SAVED_NAMED,
    PRESCRIPTION_SAVED,
    VISION_DEGRADED,
)
from app.services import documents as documents_service
from app.services import medication_candidates as candidates_service
from app.vision import image as image_preprocessing

logger = get_logger(__name__)

# §06 §6.4: only use a location_ping this fresh as scene context.
LOCATION_HINT_MAX_AGE = timedelta(hours=1)


async def _recent_location_hint(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    result = await session.execute(
        select(LocationPing)
        .where(LocationPing.user_id == user_id)
        .order_by(LocationPing.created_at.desc())
        .limit(1)
    )
    ping = result.scalar_one_or_none()
    if ping is None or datetime.now(UTC) - ping.created_at > LOCATION_HINT_MAX_AGE:
        return None
    if ping.address:
        return ping.address
    return f"approximately {ping.lat}, {ping.lon}"


def _pick(bilingual: dict, language: str) -> str:
    return bilingual.get(language, bilingual["en"])


async def handle(
    *, user_id: uuid.UUID, conversation_id: uuid.UUID, message_id: uuid.UUID, media: MediaRef
) -> None:
    """§05 §5.3: download -> validate -> preprocess -> one Gemini call
    classifies and extracts -> sub-route by kind -> DB -> reply. No text on
    the inbound message to detect language from, so `preferred_language` is
    the fallback (LANG-1: per-message detection needs actual text)."""
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is None:
            logger.warning("image_pipeline_user_missing", user_id=str(user_id))
            return
        reply_language = user.preferred_language

        try:
            content = await media_channel.download_media(media.remote_url)
        except Exception:
            logger.exception("image_download_failed", user_id=str(user_id))
            await outbound.send_text(
                session, user, conversation_id, _pick(IMAGE_UNREADABLE, reply_language)
            )
            await session.commit()
            return

        sniffed_mime = media_channel.sniff_mime_type(content)
        if not sniffed_mime.startswith("image/"):
            logger.warning("image_media_not_image", user_id=str(user_id), sniffed_mime=sniffed_mime)
            await outbound.send_text(
                session, user, conversation_id, _pick(IMAGE_UNREADABLE, reply_language)
            )
            await session.commit()
            return

        try:
            preprocessed = await image_preprocessing.preprocess(content)
        except Exception:
            logger.exception("image_preprocess_failed", user_id=str(user_id))
            await outbound.send_text(
                session, user, conversation_id, _pick(IMAGE_UNREADABLE, reply_language)
            )
            await session.commit()
            return

        storage_path, sha256 = await media_channel.store_media(preprocessed, extension=".jpg")
        media_file = MediaFile(
            message_id=message_id,
            kind="image",
            mime_type="image/jpeg",
            size_bytes=len(preprocessed),
            storage_path=storage_path,
            sha256=sha256,
        )
        session.add(media_file)
        await session.flush()

        location_hint = await _recent_location_hint(session, user_id)

        result = await gemini_client.analyze_image(
            image_bytes=preprocessed,
            mime_type="image/jpeg",
            location_hint=location_hint,
            pipeline="image.analyze",
            user_id=user.id,
        )

        if result is None:
            await outbound.send_text(
                session, user, conversation_id, _pick(VISION_DEGRADED, reply_language)
            )
            await session.commit()
            return

        reply = await _route_by_kind(session, user, media_file.id, result, reply_language)
        await outbound.send_text(session, user, conversation_id, reply)
        await session.commit()


async def _route_by_kind(
    session: AsyncSession,
    user: User,
    media_file_id: uuid.UUID,
    result: dict,
    reply_language: str,
) -> str:
    kind = result.get("kind")

    if kind == "pill_bottle":
        await candidates_service.create_candidate(
            session, patient_id=user.id, source_media_id=media_file_id, vision_result=result
        )
        drug_name = (result.get("structured") or {}).get("drug_name")
        confidence = float(result.get("confidence") or 0.0)
        if drug_name and confidence >= candidates_service.MIN_MEDICAL_FIELD_CONFIDENCE:
            return _pick(PILL_BOTTLE_SAVED_NAMED, reply_language).format(name=drug_name)
        return _pick(PILL_BOTTLE_SAVED_GENERIC, reply_language)

    if kind == "prescription":
        await candidates_service.create_candidate(
            session, patient_id=user.id, source_media_id=media_file_id, vision_result=result
        )
        return _pick(PRESCRIPTION_SAVED, reply_language)

    if kind == "document":
        await documents_service.create_document(
            session,
            media_id=media_file_id,
            doc_kind="photographed_document",
            extracted_text=result.get("text_verbatim") or None,
            summary_en=result.get("description_en") or None,
            summary_zh=result.get("description_zh") or None,
            was_scanned=False,
        )

    description = _pick(
        {"en": result.get("description_en", ""), "zh-Hans": result.get("description_zh", "")},
        reply_language,
    )
    if not description:
        return _pick(VISION_DEGRADED, reply_language)
    return description
