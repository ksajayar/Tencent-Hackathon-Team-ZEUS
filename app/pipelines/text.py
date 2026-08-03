import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import gemini_client
from app.ai.context import build_context
from app.ai.prompts.persona import (
    PERSONA_CAREGIVER_EN,
    PERSONA_CAREGIVER_ZH,
    PERSONA_EN,
    PERSONA_ZH,
)
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
    BLOOD_TYPE_RESULT,
    BLOOD_TYPE_RESULT_CAREGIVER,
    BLOODWORK_RESULT,
    BLOODWORK_RESULT_CAREGIVER,
    CAREGIVER_LINK_ACTIVATED,
    CONNECT_GOOGLE_LINK,
    CONTACT_ASK_CAREGIVER,
    CONTACT_CAREGIVER_NO,
    CONTACT_CAREGIVER_YES,
    CONTACT_EMERGENCY_NO,
    GEMINI_DEGRADED,
    HOME_ADDRESS_RESULT,
    HOME_ADDRESS_RESULT_CAREGIVER,
    MEDICATION_ACK_CONFIRMATION,
    MEDICATION_GUARD_FALLBACK,
    MEDICATION_GUARD_FALLBACK_CAREGIVER,
    NEXT_APPOINTMENT,
    NEXT_APPOINTMENT_CAREGIVER,
    NEXT_APPOINTMENT_LOCATION,
    NO_BLOODWORK,
    NO_BLOODWORK_CAREGIVER,
    NO_HOME_ADDRESS,
    NO_HOME_ADDRESS_CAREGIVER,
    NO_IMPORTANT_EMAILS,
    NO_MEDICATIONS,
    NO_MEDICATIONS_CAREGIVER,
    NO_UPCOMING_APPOINTMENTS,
    NO_UPCOMING_APPOINTMENTS_CAREGIVER,
)
from app.pipelines import caregiver as caregiver_pipeline
from app.pipelines.reply import Reply
from app.safety import medication_guard, sos
from app.safety.simplifier import simplify
from app.services import calendar as calendar_service
from app.services import caregiver as caregiver_service
from app.services import conversation as conversation_service
from app.services import documents as documents_service
from app.services import email as email_service
from app.services import medications as medications_service
from app.services.contacts import find_linked_patient, get_emergency_contacts
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

# docs/11 §11.7 demo step 3. Subject-agnostic phrasing (same reasoning as
# _MEDICATION_QUERY_RE), so the caregiver's third-person "when is her next
# appointment" matches the same pattern. Checked BEFORE the caregiver `set
# appointment` command can be confused for it - see the ordering note in
# handle()'s caregiver branch, and note "set appointment"/"设置预约" do not
# match any phrase here (asserted in tests).
_APPOINTMENT_QUERY_PHRASES = [
    "next appointment",
    "next visit",
    "upcoming appointment",
    "any appointments",
    "when is my appointment",
    "when's my appointment",
    "下一个预约",
    "下次预约",
    "接下来的预约",
    "什么时候看医生",
]
_APPOINTMENT_QUERY_RE = re.compile(
    "|".join(re.escape(p) for p in _APPOINTMENT_QUERY_PHRASES), re.IGNORECASE
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

# §17 §7 patient-side reads. Checked in this order in _generate_reply
# (blood type, then bloodwork, then address) so "what's my blood type from
# the bloodwork" - which contains both substrings - gets the more specific
# answer rather than the generic one. Subject-agnostic phrasing (same
# reasoning as _MEDICATION_QUERY_RE), reused as-is for the caregiver's
# third-person phrasing in handle()'s caregiver branch.
_BLOOD_TYPE_QUERY_PHRASES = ["blood type", "my blood type", "血型"]
_BLOOD_TYPE_QUERY_RE = re.compile(
    "|".join(re.escape(p) for p in _BLOOD_TYPE_QUERY_PHRASES), re.IGNORECASE
)

_BLOODWORK_QUERY_PHRASES = [
    "my bloodwork",
    "blood test",
    "blood work",
    "验血",
    "血检",
]
_BLOODWORK_QUERY_RE = re.compile(
    "|".join(re.escape(p) for p in _BLOODWORK_QUERY_PHRASES), re.IGNORECASE
)

_HOME_ADDRESS_QUERY_PHRASES = [
    "where's my home",
    "where is my home",
    "my home address",
    "我家在哪",
    "家庭地址",
]
_HOME_ADDRESS_QUERY_RE = re.compile(
    "|".join(re.escape(p) for p in _HOME_ADDRESS_QUERY_PHRASES), re.IGNORECASE
)

# Anchored to the whole message (not .search()): "ok" as a substring appears
# too often in ordinary sentences to safely trigger on a partial match, and
# _ack_reminder only actually short-circuits when a reminder is pending
# anyway, so this stays narrow on both ends.
_ACK_PHRASES = ["ok", "okay", "done", "taken", "took it", "好", "好的", "吃了", "已服用"]
_ACK_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(p) for p in _ACK_PHRASES) + r")\s*[.!。！]?\s*$", re.IGNORECASE
)

# §07 §7.11 contact-question yes/no confirmation. Deliberately overlaps with
# _ACK_PHRASES ("ok"/"好") - _confirm_contact_question is checked first and
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
CONTACT_CONFIRM_WINDOW = timedelta(minutes=15)


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
    reply = simplify(reply)
    fallback = MEDICATION_GUARD_FALLBACK.get(reply_language, MEDICATION_GUARD_FALLBACK["en"])
    reply = medication_guard.screen_for_unlisted_medication(
        reply, active_medications, fallback=fallback
    )
    # Clause 4: a different failure than clause 5 above - not an invented
    # drug, but invented reasoning about a real, listed one. Chained after
    # the clause-5 check; harmless to run on text clause 5 already replaced,
    # since the fallback string itself never matches the advice phrases.
    return medication_guard.screen_for_dosage_advice(reply, active_medications, fallback=fallback)


async def _caregiver_qa(
    session: AsyncSession,
    caregiver: User,
    patient: User,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    text: str,
    reply_language: str,
) -> str:
    """§17: the caregiver-role counterpart to _general_qa. `history` is the
    CAREGIVER's own conversation with the assistant, never the patient's - a
    deliberate privacy default (the caregiver did not consent to having their
    relative's private chat exposed to them just by being linked), so
    build_context is called with `user=patient` for the facts (medications,
    schedule, emails - what a caregiver's questions are actually about) but
    speaker_label="caregiver" so that transcript is correctly attributed.

    No deterministic fast paths here (medication/email query, ack, repeat) -
    those exist for the patient's own regex-matched phrasing and pending
    reminder state, neither of which apply to a caregiver asking in the third
    person about someone else's data. Everything caregiver-side goes through
    this one path until dedicated caregiver commands exist (§17 §5)."""
    history = await conversation_service.get_recent_messages(
        session, conversation_id=conversation_id, exclude_message_id=message_id
    )
    events = await calendar_service.get_schedule_window(
        session, patient.id, tz_name=patient.timezone
    )
    active_medications = await medications_service.get_active_medications(session, patient.id)
    important_emails = await email_service.get_important_emails(session, patient.id)
    context_block = build_context(
        user=patient,
        history=history,
        events=events,
        medications=active_medications,
        emails=important_emails,
        speaker_label="caregiver",
    )
    persona = PERSONA_CAREGIVER_ZH if reply_language == "zh-Hans" else PERSONA_CAREGIVER_EN
    prompt = f"{context_block}\n\nThe caregiver just said: {text}"

    reply = await gemini_client.generate_text(
        system_prompt=persona,
        user_content=prompt,
        pipeline="text.caregiver_qa",
        model=settings.gemini_model_main,
        user_id=caregiver.id,
    )
    if reply is None:
        return GEMINI_DEGRADED.get(reply_language, GEMINI_DEGRADED["en"])
    reply = simplify(reply)
    # §17 safety walkthrough fix B: the patient-facing fallback string
    # ("check with your caregiver") is circular for a caregiver - this one
    # points them at the record directly instead.
    template = MEDICATION_GUARD_FALLBACK_CAREGIVER.get(
        reply_language, MEDICATION_GUARD_FALLBACK_CAREGIVER["en"]
    )
    fallback = template.format(patient_name=patient.display_name or "the patient")
    reply = medication_guard.screen_for_unlisted_medication(
        reply, active_medications, fallback=fallback
    )
    # Clause 4, caregiver side - same reasoning as _general_qa above.
    return medication_guard.screen_for_dosage_advice(reply, active_medications, fallback=fallback)


async def _caregiver_medication_query(
    session: AsyncSession, patient: User, reply_language: str
) -> str:
    """§17 safety walkthrough fix C: caregiver counterpart to
    _medication_query - deterministic, no LLM call, so the caregiver's most
    common medication question gets the same zero-hallucination-risk answer
    the patient already gets via their own regex fast path, rather than
    riding on _caregiver_qa's free-text guard for every single ask (which
    has no deterministic escape hatch of its own - see the safety
    walkthrough, step 6, on why that gap is worse on the caregiver side than
    the patient side).

    Reuses _MEDICATION_QUERY_RE (app/pipelines/text.py) rather than a
    parallel caregiver-specific phrase list: "what medicine"/"what
    medication"/"什么药" are subject-agnostic substrings that match a
    third-person ask ("what medicine does she take") exactly as well as a
    first-person one, so a second list would just duplicate this one.
    """
    active_medications = await medications_service.get_active_medications(session, patient.id)
    name = patient.display_name or "the patient"
    if not active_medications:
        template = NO_MEDICATIONS_CAREGIVER.get(reply_language, NO_MEDICATIONS_CAREGIVER["en"])
        return template.format(patient_name=name)

    body = medications_service.render_medication_list(active_medications, reply_language)
    fallback_template = MEDICATION_GUARD_FALLBACK_CAREGIVER.get(
        reply_language, MEDICATION_GUARD_FALLBACK_CAREGIVER["en"]
    )
    fallback = fallback_template.format(patient_name=name)
    return medication_guard.enforce(body, active_medications, fallback=fallback)


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


async def _blood_type_query(
    session: AsyncSession, patient: User, reply_language: str, *, for_caregiver: bool
) -> str:
    """§17 §7: deterministic regex extraction over the latest blood_work
    document, never an LLM call - see documents_service.get_latest_bloodwork's
    docstring for why bloodwork text never enters the free-text QA context."""
    document = await documents_service.get_latest_bloodwork(session, patient.id)
    blood_type = documents_service.extract_blood_type(document)
    if blood_type is None:
        template = NO_BLOODWORK_CAREGIVER if for_caregiver else NO_BLOODWORK
        result = template.get(reply_language, template["en"])
        return (
            result.format(patient_name=patient.display_name or "the patient")
            if for_caregiver
            else result
        )
    template = BLOOD_TYPE_RESULT_CAREGIVER if for_caregiver else BLOOD_TYPE_RESULT
    result = template.get(reply_language, template["en"])
    if for_caregiver:
        return result.format(
            patient_name=patient.display_name or "the patient", blood_type=blood_type
        )
    return result.format(blood_type=blood_type)


async def _appointment_query(
    session: AsyncSession, patient: User, reply_language: str, *, for_caregiver: bool
) -> str:
    """docs/11 §11.7 step 3 - "What's my next appointment?". Deterministic,
    no LLM: the events are stored facts and `render_when` already produces
    the persona's spoken day/time register, so routing this through the
    model would only add a chance of it inventing or dropping the time
    (§07 §7.6). Reaches past the two-day context window that ordinary
    conversation uses - see get_upcoming_events for why that stays narrow.
    """
    events = await calendar_service.get_upcoming_events(session, patient.id)
    if not events:
        template = NO_UPCOMING_APPOINTMENTS_CAREGIVER if for_caregiver else NO_UPCOMING_APPOINTMENTS
        result = template.get(reply_language, template["en"])
        return (
            result.format(patient_name=patient.display_name or "the patient")
            if for_caregiver
            else result
        )

    event = events[0]
    when = calendar_service.render_when(
        event.start_at,
        is_all_day=event.is_all_day,
        tz_name=patient.timezone,
        language=reply_language,
    )
    template = NEXT_APPOINTMENT_CAREGIVER if for_caregiver else NEXT_APPOINTMENT
    result = template.get(reply_language, template["en"])
    reply = (
        result.format(
            patient_name=patient.display_name or "the patient", summary=event.summary, when=when
        )
        if for_caregiver
        else result.format(summary=event.summary, when=when)
    )
    if event.location:
        location_template = NEXT_APPOINTMENT_LOCATION.get(
            reply_language, NEXT_APPOINTMENT_LOCATION["en"]
        )
        reply += location_template.format(location=event.location)
    return reply


async def _bloodwork_query(
    session: AsyncSession, patient: User, reply_language: str, *, for_caregiver: bool
) -> str:
    """§17 §7: latest blood_work document, rendered verbatim - whichever of
    summary_en/zh or extracted_text the document actually has (a text- or
    image-intake entry may carry no summary at all, see
    pipelines/caregiver.py's bloodwork intake)."""
    document = await documents_service.get_latest_bloodwork(session, patient.id)
    if document is None:
        template = NO_BLOODWORK_CAREGIVER if for_caregiver else NO_BLOODWORK
        result = template.get(reply_language, template["en"])
        return (
            result.format(patient_name=patient.display_name or "the patient")
            if for_caregiver
            else result
        )
    summary = (
        (document.summary_zh if reply_language == "zh-Hans" else document.summary_en)
        or document.extracted_text
        or ""
    )
    template = BLOODWORK_RESULT_CAREGIVER if for_caregiver else BLOODWORK_RESULT
    result = template.get(reply_language, template["en"])
    if for_caregiver:
        return result.format(patient_name=patient.display_name or "the patient", summary=summary)
    return result.format(summary=summary)


async def _home_address_query(
    session: AsyncSession, patient: User, reply_language: str, *, for_caregiver: bool
) -> str:
    """§17 §7: safe_zones.address for the kind='home' zone, verbatim."""
    zone = await caregiver_service.get_home_zone(session, patient.id)
    if zone is None or not zone.address:
        template = NO_HOME_ADDRESS_CAREGIVER if for_caregiver else NO_HOME_ADDRESS
        result = template.get(reply_language, template["en"])
        return (
            result.format(patient_name=patient.display_name or "the patient")
            if for_caregiver
            else result
        )
    template = HOME_ADDRESS_RESULT_CAREGIVER if for_caregiver else HOME_ADDRESS_RESULT
    result = template.get(reply_language, template["en"])
    if for_caregiver:
        return result.format(
            patient_name=patient.display_name or "the patient", address=zone.address
        )
    return result.format(address=zone.address)


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


async def _confirm_contact_question(
    session: AsyncSession, user: User, text: str, reply_language: str
) -> Reply | None:
    """§07 §7.11: resolves the yes/no answer to the two-step contact chain -
    'should I call them in an emergency?' (asked by app/pipelines/contact.py)
    and then, on a yes, 'caregiver as well?' (asked by this function). Both
    pending questions live on the last outbound message's `meta`, not a
    conversation-state table, and the two keys never co-exist on one message,
    so whichever key the newest outbound carries is the question being
    answered.

    Returns None - falling through to ack/general_qa - when nothing is
    pending, it has expired, or the text is neither a yes nor a no; same
    narrow-both-ends shape as _ack_reminder.
    """
    last = await conversation_service.get_last_outbound_message(session, user.id)
    if last is None or not last.meta:
        return None
    # Caregiver first in both places, so the id resolved and the branch taken
    # below can never disagree if a future caller ever sets both keys at once.
    caregiver_pending_id = last.meta.get("pending_caregiver_contact_id")
    pending_id = caregiver_pending_id or last.meta.get("pending_emergency_contact_id")
    if not pending_id:
        return None
    if datetime.now(UTC) - last.created_at > CONTACT_CONFIRM_WINDOW:
        return None

    is_yes = bool(_YES_RE.match(text))
    if not is_yes and not _NO_RE.match(text):
        return None

    contact = await session.get(Contact, uuid.UUID(pending_id))
    if contact is None:
        return None

    if caregiver_pending_id:
        if is_yes:
            # Matches the value seed.py writes, so sos._contact_display renders
            # a seeded and a shared caregiver identically.
            contact.relationship = "caregiver"
            await session.flush()
            logger.info("caregiver_contact_confirmed", contact_id=str(contact.id))
            template = CONTACT_CAREGIVER_YES.get(reply_language, CONTACT_CAREGIVER_YES["en"])
        else:
            # Says the first answer still stands - a no here must not read as
            # having undone the emergency-contact grant.
            template = CONTACT_CAREGIVER_NO.get(reply_language, CONTACT_CAREGIVER_NO["en"])
        return Reply(template.format(name=contact.display_name))

    if not is_yes:
        template = CONTACT_EMERGENCY_NO.get(reply_language, CONTACT_EMERGENCY_NO["en"])
        return Reply(template.format(name=contact.display_name))

    existing = await get_emergency_contacts(session, user.id)
    contact.is_emergency = True
    contact.priority = len(existing) + 1
    await session.flush()
    logger.info("emergency_contact_confirmed", contact_id=str(contact.id))

    # A yes doesn't end the flow, it asks the second, larger question. The
    # smaller permission is deliberately asked for first: dropping out of a
    # chain mid-way is expected of someone with dementia, and an abandoned
    # chain should leave "call them in an emergency" stored rather than
    # "they may write to her records".
    template = CONTACT_ASK_CAREGIVER.get(reply_language, CONTACT_ASK_CAREGIVER["en"])
    return Reply(
        template.format(name=contact.display_name),
        {"pending_caregiver_contact_id": str(contact.id)},
    )


async def _generate_reply(
    session: AsyncSession,
    user: User,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    text: str,
    reply_language: str,
) -> Reply:
    if _CONNECT_GOOGLE_RE.search(text):
        return Reply(await _connect_google(session, user, reply_language))

    if _YES_RE.match(text) or _NO_RE.match(text):
        contact_reply = await _confirm_contact_question(session, user, text, reply_language)
        if contact_reply is not None:
            return contact_reply
        # No pending contact question - fall through normally.

    if _ACK_RE.match(text):
        ack_reply = await _ack_reminder(session, user, message_id, reply_language)
        if ack_reply is not None:
            return Reply(ack_reply)
        # No pending reminder - "ok" was just a normal reply, fall through.

    if _MEDICATION_QUERY_RE.search(text):
        return Reply(await _medication_query(session, user, reply_language))

    if _APPOINTMENT_QUERY_RE.search(text):
        return Reply(await _appointment_query(session, user, reply_language, for_caregiver=False))

    if _EMAIL_QUERY_RE.search(text):
        return Reply(await _email_query(session, user, reply_language))

    # §17 §7: checked in this order so "what's my blood type from the
    # bloodwork" (both substrings present) gets the more specific answer.
    if _BLOOD_TYPE_QUERY_RE.search(text):
        return Reply(await _blood_type_query(session, user, reply_language, for_caregiver=False))

    if _BLOODWORK_QUERY_RE.search(text):
        return Reply(await _bloodwork_query(session, user, reply_language, for_caregiver=False))

    if _HOME_ADDRESS_QUERY_RE.search(text):
        return Reply(await _home_address_query(session, user, reply_language, for_caregiver=False))

    if _REPEAT_RE.search(text):
        last = await conversation_service.get_last_outbound_message(session, user.id)
        if last is not None and last.body:
            return Reply(last.body)
        # Nothing to repeat yet - fall through to general_qa rather than go silent.

    return Reply(
        await _general_qa(session, user, conversation_id, message_id, text, reply_language)
    )


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

        # §17 role detection: a role='patient' user (the server default,
        # app/db/models/user.py) whose phone now matches a contacts row with
        # relationship='caregiver' is a caregiver who just messaged for the
        # first time - there's no other trigger point for this, since nothing
        # pushes a message TO them at link time (§17 §4.1: the sandbox/Meta
        # 24h-window problem means we wait for them to speak first). Checked
        # before SOS deliberately: a first-time caregiver typing "help" as a
        # greeting should still get flipped to their real role, and SOS text
        # is vanishingly unlikely to double as someone's very first message.
        if user.role == "patient":
            linked_patient = await find_linked_patient(session, user.phone_e164)
            if linked_patient is not None:
                user.role = "caregiver"
                await session.flush()
                logger.info(
                    "caregiver_role_activated",
                    user_id=str(user.id),
                    patient_id=str(linked_patient.id),
                )
                template = CAREGIVER_LINK_ACTIVATED.get(
                    reply_language, CAREGIVER_LINK_ACTIVATED["en"]
                )
                name = linked_patient.display_name or "the patient"
                reply_text = template.format(patient_name=name)
                await outbound.send_text(session, user, conversation_id, reply_text)
                await session.commit()
                # The message that triggered activation is answered next time
                # they write, now that role/persona are correctly set up -
                # deliberately not also answered as caregiver_qa in the same
                # turn, so the first thing a caregiver ever reads is the
                # deterministic activation string, never LLM-generated text.
                return

        if sos.is_sos_trigger(text):
            # CLAUDE.md SAFETY-2: deterministic, checked before every other
            # intent and before any LLM call - an outage elsewhere in this
            # function must never be able to swallow an SOS. Applies to
            # whoever sent the message, patient or caregiver, unchanged from
            # before this file had a caregiver role at all.
            reply_text = await sos.trigger(
                session, user=user, trigger_text=text, reply_language=reply_language
            )
            await outbound.send_text(session, user, conversation_id, reply_text)
            await session.commit()
            return

        if user.role == "caregiver":
            linked_patient = await find_linked_patient(session, user.phone_e164)
            if linked_patient is None:
                # Role was set on a previous message but the contacts row
                # backing the link is gone now (e.g. re-saved without the
                # caregiver flag). No product decision exists yet for this
                # edge case, so degrade to the ordinary patient path rather
                # than invent caregiver-specific error handling for it.
                logger.warning("caregiver_link_missing", user_id=str(user.id))
                reply = await _generate_reply(
                    session, user, conversation_id, message_id, text, reply_language
                )
            else:
                # §17: caregiver.handle_command owns the set-X commands and
                # any multi-turn flow already in progress (pending state on
                # messages.meta, same mechanism as the contact chain above).
                # Returns None - same narrow-both-ends contract as
                # _ack_reminder/_confirm_contact_question - when the text is
                # neither a command trigger nor an answer to a pending
                # question, so ordinary caregiver conversation still falls
                # through normally.
                command_reply = await caregiver_pipeline.handle_command(
                    session, user, linked_patient, message_id, text, reply_language
                )
                if command_reply is not None:
                    reply = command_reply
                elif _MEDICATION_QUERY_RE.search(text):
                    reply = Reply(
                        await _caregiver_medication_query(session, linked_patient, reply_language)
                    )
                elif _APPOINTMENT_QUERY_RE.search(text):
                    reply = Reply(
                        await _appointment_query(
                            session, linked_patient, reply_language, for_caregiver=True
                        )
                    )
                elif _BLOOD_TYPE_QUERY_RE.search(text):
                    reply = Reply(
                        await _blood_type_query(
                            session, linked_patient, reply_language, for_caregiver=True
                        )
                    )
                elif _BLOODWORK_QUERY_RE.search(text):
                    reply = Reply(
                        await _bloodwork_query(
                            session, linked_patient, reply_language, for_caregiver=True
                        )
                    )
                elif _HOME_ADDRESS_QUERY_RE.search(text):
                    reply = Reply(
                        await _home_address_query(
                            session, linked_patient, reply_language, for_caregiver=True
                        )
                    )
                else:
                    reply = Reply(
                        await _caregiver_qa(
                            session,
                            user,
                            linked_patient,
                            conversation_id,
                            message_id,
                            text,
                            reply_language,
                        )
                    )
        else:
            reply = await _generate_reply(
                session, user, conversation_id, message_id, text, reply_language
            )

        await outbound.send_text(session, user, conversation_id, reply.text, meta=reply.meta)
        if reply_with_audio:
            # CHANNEL-2: always a second, separate send after the text reply.
            await _send_audio_reply(session, user, conversation_id, reply.text, reply_language)
        await session.commit()
