import uuid

from app.channels.base import InboundMessage
from app.core.logging import get_logger
from app.pipelines import document as document_pipeline
from app.pipelines import image as image_pipeline
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

    # location/contact pipelines land in M9.
    logger.info("pipeline_kind_not_yet_handled", kind=inbound.kind)
