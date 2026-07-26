from sqlalchemy import select

from app.channels import outbound
from app.core.logging import get_logger
from app.db.models.user import User
from app.db.session import async_session
from app.i18n.strings import CHECKIN_PROMPT
from app.services import conversation as conversation_service

logger = get_logger(__name__)


async def send_checkin_requests() -> int:
    """§07 §7.10 / §09: pull-based check-in - 'where are you now?' at
    scheduled times, since WhatsApp only gives a location on request, never
    a background stream. `meta.checkin_prompt` marks the outbound message so
    checkin_watchdog.py can find prompts still waiting on a reply."""
    async with async_session() as session:
        result = await session.execute(
            select(User).where(User.role == "patient", User.is_active.is_(True))
        )
        patients = list(result.scalars().all())

        sent = 0
        for user in patients:
            conversation = await conversation_service.get_or_create_open_conversation(session, user)
            prompt = CHECKIN_PROMPT.get(user.preferred_language, CHECKIN_PROMPT["en"])
            message = await outbound.send_text(
                session, user, conversation.id, prompt, meta={"checkin_prompt": True}
            )
            if message is not None:
                sent += 1

        await session.commit()
        logger.info("checkin_requests_sent", patients=len(patients), sent=sent)
        return sent
