import uuid

from app.channels import media as media_channel
from app.channels import outbound
from app.channels.base import MediaRef
from app.core.logging import get_logger
from app.db.models.media import MediaFile, Transcript
from app.db.models.message import Message
from app.db.models.user import User
from app.db.session import async_session
from app.i18n.strings import AUDIO_TOO_LONG, VOICE_UNCLEAR
from app.pipelines import text as text_pipeline
from app.services import conversation as conversation_service
from app.speech import audio as audio_processing
from app.speech import stt

logger = get_logger(__name__)

# §06 §6.1: reject <0.5s (accidental tap) and >60s.
MIN_DURATION_SECONDS = 0.5
MAX_DURATION_SECONDS = 60.0
LOW_CONFIDENCE_THRESHOLD = 0.6


async def handle(
    *, user_id: uuid.UUID, conversation_id: uuid.UUID, message_id: uuid.UUID, media: MediaRef
) -> None:
    """§05 §5.2: download -> ffmpeg normalise -> Gemini audio (transcribe +
    detect) -> text pipeline -> reply (+ TTS). Voice is an input format, not
    a separate feature: once transcribed, the transcript enters the same
    text pipeline unchanged (§06 §6.1) - one code path for both."""
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is None:
            logger.warning("voice_pipeline_user_missing", user_id=str(user_id))
            return

        try:
            content = await media_channel.download_media(media.remote_url)
        except Exception:
            logger.exception("voice_download_failed", user_id=str(user_id))
            return

        sniffed_mime = media_channel.sniff_mime_type(content)
        if not sniffed_mime.startswith("audio/"):
            logger.warning("voice_media_not_audio", user_id=str(user_id), sniffed_mime=sniffed_mime)
            return

        storage_path, sha256 = await media_channel.store_media(content, extension=".ogg")

        try:
            duration_seconds = await audio_processing.probe_duration_seconds(storage_path)
        except Exception:
            logger.exception("voice_probe_duration_failed", user_id=str(user_id))
            duration_seconds = None

        if duration_seconds is not None and duration_seconds < MIN_DURATION_SECONDS:
            # Accidental tap - not a real message, no reply expected.
            logger.info(
                "voice_note_too_short_ignored", user_id=str(user_id), duration_s=duration_seconds
            )
            return

        conversation = await conversation_service.get_or_create_open_conversation(session, user)

        if duration_seconds is not None and duration_seconds > MAX_DURATION_SECONDS:
            logger.info("voice_note_too_long", user_id=str(user_id), duration_s=duration_seconds)
            reply = AUDIO_TOO_LONG.get(user.preferred_language, AUDIO_TOO_LONG["en"])
            await outbound.send_text(session, user, conversation.id, reply)
            await session.commit()
            return

        media_file = MediaFile(
            message_id=message_id,
            kind="audio",
            mime_type=sniffed_mime,
            size_bytes=len(content),
            storage_path=storage_path,
            sha256=sha256,
            duration_ms=int(duration_seconds * 1000) if duration_seconds is not None else None,
        )
        session.add(media_file)
        await session.flush()

        result = await stt.transcribe(storage_path, pipeline="voice.transcribe", user_id=user.id)

        if result is None or not result.get("transcript", "").strip():
            # Empty or nonsense transcript: ask the user to repeat, don't guess (§06 §6.1).
            logger.warning("voice_transcript_empty", user_id=str(user_id))
            reply = VOICE_UNCLEAR.get(user.preferred_language, VOICE_UNCLEAR["en"])
            await outbound.send_text(session, user, conversation.id, reply)
            await session.commit()
            return

        transcript_text = result["transcript"].strip()
        confidence = result.get("confidence") or 0.0
        if confidence < LOW_CONFIDENCE_THRESHOLD:
            logger.warning(
                "voice_transcript_low_confidence", user_id=str(user_id), confidence=confidence
            )

        session.add(
            Transcript(
                media_id=media_file.id,
                text_content=transcript_text,
                language=result.get("language"),
                confidence=confidence,
                engine="gemini",
            )
        )

        message = await session.get(Message, message_id)
        if message is not None:
            message.body = transcript_text

        await session.commit()

    await text_pipeline.handle(
        user_id=user_id,
        conversation_id=conversation_id,
        message_id=message_id,
        text=transcript_text,
        reply_with_audio=True,
    )
