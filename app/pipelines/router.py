import uuid

from app.channels import outbound
from app.channels.base import InboundMessage
from app.core.logging import get_logger
from app.pipelines import contact as contact_pipeline
from app.pipelines import document as document_pipeline
from app.pipelines import image as image_pipeline
from app.pipelines import location as location_pipeline
from app.pipelines import text as text_pipeline
from app.pipelines import voice as voice_pipeline

logger = get_logger(__name__)


async def route_inbound(
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    inbound: InboundMessage,
) -> None:
    # Fired once here (not per-pipeline) so every kind - text, voice, image,
    # etc. - shows "typing..." while the slow work (Gemini, STT, OCR) runs.
    await outbound.send_typing_indicator(inbound.message_sid)

    if inbound.kind == "text" and inbound.text:
        await text_pipeline.handle(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            text=inbound.text,
        )
        return

    if inbound.kind == "audio" and inbound.media:
        await voice_pipeline.handle(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            media=inbound.media,
        )
        return

    if inbound.kind == "image" and inbound.media:
        await image_pipeline.handle(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            media=inbound.media,
        )
        return

    if inbound.kind == "document" and inbound.media:
        await document_pipeline.handle(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            media=inbound.media,
        )
        return

    if inbound.kind == "location" and inbound.coords:
        await location_pipeline.handle(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            lat=inbound.coords[0],
            lon=inbound.coords[1],
        )
        return

    if inbound.kind == "contact" and inbound.media:
        await contact_pipeline.handle(
            user_id=user_id,
            conversation_id=conversation_id,
            message_id=message_id,
            media=inbound.media,
        )
        return

    logger.info("pipeline_kind_not_handled", kind=inbound.kind)
