import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import gemini_client
from app.ai.context import build_context
from app.ai.prompts.persona import PERSONA_EN, PERSONA_ZH
from app.channels import outbound
from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.contact import Contact
from app.db.models.message import Message
from app.db.models.reminder import ReminderAck
from app.db.models.user import User
from app.db.session import async_session
from app.google import oauth as google_oauth
from app.i18n.strings import (
    CONNECT_GOOGLE_LINK,
    CONTACT_EMERGENCY_NO,
    CONTACT_EMERGENCY_YES,
    GEMINI_DEGRADED,
    MEDICATION_ACK_CONFIRMATION,
    MEDICATION_GUARD_FALLBACK,
    NO_IMPORTANT_EMAILS,
    NO_MEDICATIONS,
)
from app.safety import medication_guard, sos
from app.safety.simplifier import simplify
from app.services import calendar as calendar_service
from app.services import conversation as conversation_service
from app.services import email as email_service
from app.services import medications as medications_service
from app.services.contacts import get_emergency_contacts
from app.speech import tts

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

_MEDICATION_QUERY_PHRASES = [
    "what medicine",
    "what medication",
    "my medicine",
    "my medication",
    "which medicine",
    "what pills",
    "what tablets",
    "吃什么药",
    "我的药",
    "什么药",
]
_MEDICATION_QUERY_RE = re.compile(
    "|".join(re.escape(p) for p in _MEDICATION_QUERY_PHRASES), re.IGNORECASE
)

_EMAIL_QUERY_PHRASES = [
    "important email",
    "important emails",
    "any emails",
    "any important",
    "check my email",
    "check my mail",
    "my emails",
    "重要邮件",
    "邮件",
    "有邮件吗",
    "有信吗",
]
_EMAIL_QUERY_RE = re.compile("|".join(re.escape(p) for p in _EMAIL_QUERY_PHRASES), re.IGNORECASE)

# Anchored to the whole message (not .search()): "ok" as a substring appears
# too often in ordinary sentences to safely trigger on a partial match, and
# _ack_reminder only actually short-circuits when a reminder is pending
# anyway, so this stays narrow on both ends.
_ACK_PHRASES = ["ok", "okay", "done", "taken", "took it", "好", "好的", "吃了", "已服用"]
_ACK_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(p) for p in _ACK_PHRASES) + r")\s*[.!。！]?\s*$", re.IGNORECASE
)

# §07 §7.11 emergency-contact yes/no confirmation. Deliberately overlaps with
# _ACK_PHRASES ("ok"/"好") - _confirm_emergency_contact is checked first and
# only actually short-circuits when a question is genuinely pending
# (message.meta), same narrow-both-ends reasoning as the ack regex above.
_YES_PHRASES = ["yes", "yeah", "yep", "sure", "ok", "okay", "是", "是的", "可以", "好", "好的"]
_NO_PHRASES = ["no", "nope", "not", "不", "不要", "不是"]
_YES_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(p) for p in _YES_PHRASES) + r")\s*[.!。！]?\s*$", re.IGNORECASE
)
_NO_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(p) for p in _NO_PHRASES) + r")\s*[.!。！]?\s*$", re.IGNORECASE
)
EMERGENCY_CONFIRM_WINDOW = timedelta(minutes=15)


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
    active_medications = await medications_service.get_active_medications(session, user.id)
    important_emails = await email_service.get_important_emails(session, user.id)
    context_block = build_context(
        user=user,
        history=history,
        events=events,
        medications=active_medications,
        emails=important_emails,
    )
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


async def _medication_query(session: AsyncSession, user: User, reply_language: str) -> str:
    """§07 §7.6: 'What medicine do I take?' - medications only, deterministic,
    no LLM call, so it can never hallucinate a name or dose."""
    active_medications = await medications_service.get_active_medications(session, user.id)
    if not active_medications:
        return NO_MEDICATIONS.get(reply_language, NO_MEDICATIONS["en"])

    body = medications_service.render_medication_list(active_medications, reply_language)
    fallback = MEDICATION_GUARD_FALLBACK.get(reply_language, MEDICATION_GUARD_FALLBACK["en"])
    return medication_guard.enforce(body, active_medications, fallback=fallback)


async def _email_query(session: AsyncSession, user: User, reply_language: str) -> str:
    """§04 §4.2: 'any important emails?' - deterministic, no LLM call, the
    summaries were pre-computed at sync time."""
    important = await email_service.get_important_emails(session, user.id)
    if not important:
        return NO_IMPORTANT_EMAILS.get(reply_language, NO_IMPORTANT_EMAILS["en"])
    return email_service.render_important_emails(important, reply_language)


async def _ack_reminder(
    session: AsyncSession, user: User, message_id: uuid.UUID, reply_language: str
) -> str | None:
    """§07 §7.7 step 7. Returns None (not a fallback string) when there is no
    pending reminder to ack, so the caller can fall through to general_qa -
    'ok' is common enough in ordinary conversation that hijacking it when
    nothing is actually pending would be wrong."""
    reminder = await medications_service.find_unacked_reminder(session, user.id)
    if reminder is None:
        return None

    session.add(ReminderAck(reminder_id=reminder.id, user_id=user.id, via_message_id=message_id))
    await session.flush()
    logger.info("reminder_acked", reminder_id=str(reminder.id), user_id=str(user.id))
    return MEDICATION_ACK_CONFIRMATION.get(reply_language, MEDICATION_ACK_CONFIRMATION["en"])


async def _confirm_emergency_contact(
    session: AsyncSession, user: User, text: str, reply_language: str
) -> str | None:
    """§07 §7.11: resolves the yes/no answer to 'should I call them in an
    emergency?'. The pending question is tracked on the last outbound
    message's `meta` (set by app/pipelines/contact.py), not a new
    conversation-state table. Returns None (falls through to ack/general_qa)
    when there is no pending question, it has expired, or the text is
    neither a yes nor a no - same narrow-both-ends shape as _ack_reminder."""
    last = await conversation_service.get_last_outbound_message(session, user.id)
    if last is None or not last.meta:
        return None
    pending_id = last.meta.get("pending_emergency_contact_id")
    if not pending_id:
        return None
    if datetime.now(UTC) - last.created_at > EMERGENCY_CONFIRM_WINDOW:
        return None

    is_yes = bool(_YES_RE.match(text))
    if not is_yes and not _NO_RE.match(text):
        return None

    contact = await session.get(Contact, uuid.UUID(pending_id))
    if contact is None:
        return None

    if is_yes:
        existing = await get_emergency_contacts(session, user.id)
        contact.is_emergency = True
        contact.priority = len(existing) + 1
        await session.flush()
        logger.info("emergency_contact_confirmed", contact_id=str(contact.id))
        template = CONTACT_EMERGENCY_YES.get(reply_language, CONTACT_EMERGENCY_YES["en"])
    else:
        template = CONTACT_EMERGENCY_NO.get(reply_language, CONTACT_EMERGENCY_NO["en"])
    return template.format(name=contact.display_name)


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

    if _YES_RE.match(text) or _NO_RE.match(text):
        contact_reply = await _confirm_emergency_contact(session, user, text, reply_language)
        if contact_reply is not None:
            return contact_reply
        # No pending emergency-contact question - fall through normally.

    if _ACK_RE.match(text):
        ack_reply = await _ack_reminder(session, user, message_id, reply_language)
        if ack_reply is not None:
            return ack_reply
        # No pending reminder - "ok" was just a normal reply, fall through.

    if _MEDICATION_QUERY_RE.search(text):
        return await _medication_query(session, user, reply_language)

    if _EMAIL_QUERY_RE.search(text):
        return await _email_query(session, user, reply_language)

    if _REPEAT_RE.search(text):
        last = await conversation_service.get_last_outbound_message(session, user.id)
        if last is not None and last.body:
            return last.body
        # Nothing to repeat yet - fall through to general_qa rather than go silent.

    return await _general_qa(session, user, conversation_id, message_id, text, reply_language)


async def _send_audio_reply(
    session: AsyncSession, user: User, conversation_id: uuid.UUID, text: str, language: str
) -> None:
    """Best-effort: the text reply is already sent by the time this runs, so
    a TTS/ffmpeg failure degrades to text-only rather than losing the reply
    entirely (every external call has a timeout and a fallback)."""
    try:
        filename = await tts.synthesize(text, language=language)
        await outbound.send_audio(session, user, conversation_id, filename=filename, body_text=text)
    except Exception:
        logger.exception("tts_reply_failed", user_id=str(user.id))


async def handle(
    *,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    text: str,
    reply_with_audio: bool = False,
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

        if sos.is_sos_trigger(text):
            # CLAUDE.md SAFETY-2: deterministic, checked before every other
            # intent and before any LLM call - an outage elsewhere in this
            # function must never be able to swallow an SOS.
            reply_text = await sos.trigger(
                session, user=user, trigger_text=text, reply_language=reply_language
            )
            await outbound.send_text(session, user, conversation_id, reply_text)
            await session.commit()
            return

        reply_text = await _generate_reply(
            session, user, conversation_id, message_id, text, reply_language
        )

        await outbound.send_text(session, user, conversation_id, reply_text)
        if reply_with_audio:
            # CHANNEL-2: always a second, separate send after the text reply.
            await _send_audio_reply(session, user, conversation_id, reply_text, reply_language)
        await session.commit()
