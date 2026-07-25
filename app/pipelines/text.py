import re
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import gemini_client
from app.ai.context import build_context
from app.ai.prompts.persona import PERSONA_EN, PERSONA_ZH
from app.channels import outbound
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.message import Message
from app.db.models.user import User
from app.db.session import async_session
from app.google import oauth as google_oauth
from app.i18n.strings import CONNECT_GOOGLE_LINK, GEMINI_DEGRADED
from app.safety.simplifier import simplify
from app.services import calendar as calendar_service
from app.services import conversation as conversation_service

logger = get_logger(__name__)

_CJK_RE = re.compile(r"[一-鿿㐀-䶿豈-﫿]")
_LATIN_RE = re.compile(r"[A-Za-z]")

_REPEAT_PHRASES = [
    "say that again",
    "repeat that",
    "repeat please",
    "what did you say",
    "come again",
    "再说一次",
    "你说什么",
    "再讲一遍",
    "重复一下",
]
_REPEAT_RE = re.compile("|".join(re.escape(p) for p in _REPEAT_PHRASES), re.IGNORECASE)

_CONNECT_GOOGLE_PHRASES = ["connect google", "连接谷歌", "连接谷歌账号"]
_CONNECT_GOOGLE_RE = re.compile(
    "|".join(re.escape(p) for p in _CONNECT_GOOGLE_PHRASES), re.IGNORECASE
)


def detect_language(text: str, *, fallback: str) -> tuple[str | None, str]:
    """Script-based detection (§05 §5.1) - beats a language-detection library on
    short WhatsApp messages.

    Returns (stored_label, reply_language):
      stored_label -> messages.detected_language: 'en'|'zh-Hans'|'mixed'|None
        (None only when the text has no script signal at all - digits/emoji/etc).
      reply_language -> always a concrete 'en' or 'zh-Hans' to actually reply in:
        the dominant script when mixed, or `fallback` (LANG-1: the user's
        preferred_language is the fallback only when detection is ambiguous).
    """
    cjk_count = len(_CJK_RE.findall(text))
    latin_count = len(_LATIN_RE.findall(text))

    if cjk_count == 0 and latin_count == 0:
        return None, fallback
    if cjk_count > 0 and latin_count > 0:
        dominant = "zh-Hans" if cjk_count >= latin_count else "en"
        return "mixed", dominant
    if cjk_count > 0:
        return "zh-Hans", "zh-Hans"
    return "en", "en"


async def _general_qa(
    session: AsyncSession,
    user: User,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    text: str,
    reply_language: str,
) -> str:
    history = await conversation_service.get_recent_messages(
        session, conversation_id=conversation_id, exclude_message_id=message_id
    )
    events = await calendar_service.get_schedule_window(session, user.id, tz_name=user.timezone)
    context_block = build_context(user=user, history=history, events=events)
    persona = PERSONA_ZH if reply_language == "zh-Hans" else PERSONA_EN
    prompt = f"{context_block}\n\nThe patient just said: {text}"

    reply = await gemini_client.generate_text(
        system_prompt=persona,
        user_content=prompt,
        pipeline="text.general_qa",
        model=settings.gemini_model_main,
        user_id=user.id,
    )
    if reply is None:
        return GEMINI_DEGRADED.get(reply_language, GEMINI_DEGRADED["en"])
    return simplify(reply)


async def _connect_google(session: AsyncSession, user: User, reply_language: str) -> str:
    link = await google_oauth.create_connect_link(session, user.id)
    template = CONNECT_GOOGLE_LINK.get(reply_language, CONNECT_GOOGLE_LINK["en"])
    return template.format(link=link)


async def _generate_reply(
    session: AsyncSession,
    user: User,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    text: str,
    reply_language: str,
) -> str:
    if _CONNECT_GOOGLE_RE.search(text):
        return await _connect_google(session, user, reply_language)

    if _REPEAT_RE.search(text):
        last = await conversation_service.get_last_outbound_message(session, user.id)
        if last is not None and last.body:
            return last.body
        # Nothing to repeat yet - fall through to general_qa rather than go silent.

    return await _general_qa(session, user, conversation_id, message_id, text, reply_language)


async def handle(
    *, user_id: uuid.UUID, conversation_id: uuid.UUID, message_id: uuid.UUID, text: str
) -> None:
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is None:
            logger.warning("text_pipeline_user_missing", user_id=str(user_id))
            return

        stored_label, reply_language = detect_language(text, fallback=user.preferred_language)

        message = await session.get(Message, message_id)
        if message is not None:
            message.detected_language = stored_label
            await session.flush()

        reply_text = await _generate_reply(
            session, user, conversation_id, message_id, text, reply_language
        )

        await outbound.send_text(session, user, conversation_id, reply_text)
        await session.commit()
