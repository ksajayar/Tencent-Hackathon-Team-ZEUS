import uuid

from app.channels import media as media_channel
from app.channels import outbound
from app.channels.base import MediaRef
from app.core.logging import get_logger
from app.db.models.user import User
from app.db.session import async_session
from app.i18n.strings import CONTACT_SAVED_ASK_EMERGENCY, CONTACT_UNREADABLE
from app.services.contacts import parse_vcard, upsert_from_vcard

logger = get_logger(__name__)


def _pick(bilingual: dict, language: str) -> str:
    return bilingual.get(language, bilingual["en"])


async def handle(
    *, user_id: uuid.UUID, conversation_id: uuid.UUID, message_id: uuid.UUID, media: MediaRef
) -> None:
    """§07 §7.11: download -> parse with vobject -> upsert `contacts` -> ask
    the emergency-contact yes/no question. The question is tracked via the
    outbound message's `meta` (text.py's _confirm_emergency_contact resolves
    the answer) rather than a new conversation-state table."""
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is None:
            logger.warning("contact_pipeline_user_missing", user_id=str(user_id))
            return
        reply_language = user.preferred_language

        try:
            content = await media_channel.download_media(media.remote_url)
        except Exception:
            logger.exception("contact_download_failed", user_id=str(user_id))
            await outbound.send_text(
                session, user, conversation_id, _pick(CONTACT_UNREADABLE, reply_language)
            )
            await session.commit()
            return

        try:
            raw_text = content.decode("utf-8")
        except UnicodeDecodeError:
            raw_text = content.decode("latin-1", errors="ignore")

        parsed = parse_vcard(raw_text)
        if parsed is None:
            await outbound.send_text(
                session, user, conversation_id, _pick(CONTACT_UNREADABLE, reply_language)
            )
            await session.commit()
            return

        contact = await upsert_from_vcard(session, user_id=user_id, parsed=parsed)

        reply = _pick(CONTACT_SAVED_ASK_EMERGENCY, reply_language).format(name=contact.display_name)
        await outbound.send_text(
            session,
            user,
            conversation_id,
            reply,
            meta={"pending_emergency_contact_id": str(contact.id)},
        )
        await session.commit()
