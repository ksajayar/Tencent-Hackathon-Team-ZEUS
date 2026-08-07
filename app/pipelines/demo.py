import uuid

from app.channels import outbound
from app.core.logging import get_logger
from app.db.models.user import User
from app.db.session import async_session
from app.google import oauth as google_oauth
from app.i18n.strings import CONNECT_GOOGLE_LINK, DEMO_WELCOME

logger = get_logger(__name__)


def _pick(bilingual: dict, language: str) -> str:
    return bilingual.get(language, bilingual["en"])


async def send_provisioning_messages(*, user_id: uuid.UUID, conversation_id: uuid.UUID) -> None:
    """DEMO_MODE requirement 3+4: called once from router.py right after a
    brand-new number has been auto-provisioned and cloned
    (app/services/demo.py::clone_template_for_patient, already committed by
    the webhook handler before this background task runs). Sends the welcome
    explaining the profile is ready, then the real Google OAuth consent link
    - Google connection is deliberately never faked or seeded, this is the
    same link-and-flow app/pipelines/text.py::_connect_google sends, just
    triggered proactively instead of waiting for "connect google". Both sends
    go through outbound.send_text (CHANNEL-1); the window is open because the
    webhook handler already recorded this same inbound message before
    scheduling this background task.
    """
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is None:
            logger.warning("demo_pipeline_user_missing", user_id=str(user_id))
            return
        reply_language = user.preferred_language

        welcome = _pick(DEMO_WELCOME, reply_language).format(
            patient_name=user.display_name or ("您" if reply_language == "zh-Hans" else "you")
        )
        await outbound.send_text(session, user, conversation_id, welcome)

        link = await google_oauth.create_connect_link(session, user.id)
        google_prompt = _pick(CONNECT_GOOGLE_LINK, reply_language).format(link=link)
        await outbound.send_text(session, user, conversation_id, google_prompt)

        await session.commit()
        logger.info("demo_provisioning_messages_sent", user_id=str(user_id))
