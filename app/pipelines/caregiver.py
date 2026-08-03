import re
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil import parser as dateutil_parser
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import gemini_client
from app.channels import outbound
from app.core.logging import get_logger
from app.db.models.medication import MedicationCandidate
from app.db.models.user import User
from app.i18n.strings import (
    CAREGIVER_ADDRESS_ASK,
    CAREGIVER_ADDRESS_SAVED,
    CAREGIVER_APPOINTMENT_ASK_DATETIME,
    CAREGIVER_APPOINTMENT_ASK_LOCATION,
    CAREGIVER_APPOINTMENT_ASK_PURPOSE,
    CAREGIVER_APPOINTMENT_CANCELLED,
    CAREGIVER_APPOINTMENT_CONFIRM,
    CAREGIVER_APPOINTMENT_DATETIME_UNCLEAR,
    CAREGIVER_APPOINTMENT_SAVED,
    CAREGIVER_BLOODWORK_ASK_INTAKE,
    CAREGIVER_BLOODWORK_DONE,
    CAREGIVER_BLOODWORK_TEXT_SAVED,
    CAREGIVER_CANDIDATE_REJECTED,
    CAREGIVER_CANDIDATE_REVIEW_DONE,
    CAREGIVER_CANDIDATE_REVIEW_PROMPT,
    CAREGIVER_MEDICATION_ASK_DOSE,
    CAREGIVER_MEDICATION_ASK_NAME,
    CAREGIVER_MEDICATION_ASK_SCHEDULE,
    CAREGIVER_MEDICATION_CANCELLED,
    CAREGIVER_MEDICATION_CONFIRM,
    CAREGIVER_MEDICATION_SAVED,
    CAREGIVER_MEDICATION_SCHEDULE_UNCLEAR,
    CAREGIVER_NO_PENDING_CANDIDATES,
    PATIENT_NEW_APPOINTMENT,
)
from app.pipelines.reply import Reply
from app.services import calendar as calendar_service
from app.services import caregiver as caregiver_service
from app.services import conversation as conversation_service
from app.services import documents as documents_service
from app.services.contacts import find_linked_patient

logger = get_logger(__name__)

FLOW_WINDOW = timedelta(minutes=15)

# §17: one command exists so far as a real intent outside this module
# (_MEDICATION_QUERY_RE in text.py, reused for _caregiver_medication_query -
# see the safety walkthrough fix C). Everything below is new.
_SET_APPOINTMENT_PHRASES = [
    "set appointment",
    "add appointment",
    "设置预约",
    "新增预约",
    "添加预约",
]
_SET_BLOODWORK_PHRASES = ["set bloodwork", "add bloodwork", "设置验血", "添加验血"]
_SET_ADDRESS_PHRASES = ["set address", "设置地址"]
_SET_MEDICATION_PHRASES = ["set medication", "add medication", "设置用药", "添加用药"]
_CHECK_CANDIDATES_PHRASES = ["check candidates", "check candidate", "查看待审核", "查看候选"]

_SET_APPOINTMENT_RE = re.compile(
    "|".join(re.escape(p) for p in _SET_APPOINTMENT_PHRASES), re.IGNORECASE
)
_SET_BLOODWORK_RE = re.compile(
    "|".join(re.escape(p) for p in _SET_BLOODWORK_PHRASES), re.IGNORECASE
)
_SET_ADDRESS_RE = re.compile("|".join(re.escape(p) for p in _SET_ADDRESS_PHRASES), re.IGNORECASE)
_SET_MEDICATION_RE = re.compile(
    "|".join(re.escape(p) for p in _SET_MEDICATION_PHRASES), re.IGNORECASE
)
_CHECK_CANDIDATES_RE = re.compile(
    "|".join(re.escape(p) for p in _CHECK_CANDIDATES_PHRASES), re.IGNORECASE
)

_DONE_PHRASES = ["done", "finished", "完成", "好了", "结束"]
_DONE_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(p) for p in _DONE_PHRASES) + r")\s*[.!。！]?\s*$", re.IGNORECASE
)
_YES_PHRASES = ["yes", "yeah", "yep", "sure", "ok", "okay", "是", "是的", "可以", "好", "好的"]
_NO_PHRASES = ["no", "nope", "not", "不", "不要", "不是"]
_YES_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(p) for p in _YES_PHRASES) + r")\s*[.!。！]?\s*$", re.IGNORECASE
)
_NO_RE = re.compile(
    r"^\s*(" + "|".join(re.escape(p) for p in _NO_PHRASES) + r")\s*[.!。！]?\s*$", re.IGNORECASE
)


def _pick(bilingual: dict, language: str) -> str:
    return bilingual.get(language, bilingual["en"])


def _flow_reply(text: str, *, kind: str, step: str, data: dict | None = None) -> Reply:
    return Reply(text, {"pending_caregiver_flow": {"kind": kind, "step": step, "data": data or {}}})


async def _get_pending_flow(session: AsyncSession, caregiver_id: uuid.UUID) -> dict | None:
    """Same mechanism as the contact chain (§07 §7.11) and the exact reason
    it's a 15-minute window on messages.meta rather than a state table:
    nothing new to build, and a caregiver mid-flow who goes quiet for 15
    minutes falls back to ordinary conversation rather than being stuck."""
    last = await conversation_service.get_last_outbound_message(session, caregiver_id)
    if last is None or not last.meta:
        return None
    flow = last.meta.get("pending_caregiver_flow")
    if not flow:
        return None
    if datetime.now(UTC) - last.created_at > FLOW_WINDOW:
        return None
    return flow


async def get_pending_bloodwork_patient(session: AsyncSession, caregiver: User) -> User | None:
    """Used by pipelines/image.py and pipelines/document.py to recognise a
    caregiver mid set-bloodwork intake sending a photo or PDF, rather than
    an ordinary upload from a patient. Checks BOTH that a flow is pending
    AND that it is specifically the bloodwork-intake step - a caregiver
    mid-appointment flow who sends a photo by accident must not have it
    silently treated as a lab result."""
    flow = await _get_pending_flow(session, caregiver.id)
    if flow is None or flow.get("kind") != "bloodwork" or flow.get("step") != "intake":
        return None
    return await find_linked_patient(session, caregiver.phone_e164)


def bloodwork_intake_meta() -> dict:
    """The exact pending-flow meta shape that keeps the bloodwork intake
    flow open for another upload - exposed so image.py/document.py don't
    need to know this module's internal meta shape to keep the flow going
    after a photo or PDF."""
    return {"pending_caregiver_flow": {"kind": "bloodwork", "step": "intake", "data": {}}}


async def handle_command(
    session: AsyncSession,
    caregiver: User,
    patient: User,
    message_id: uuid.UUID,
    text: str,
    reply_language: str,
) -> Reply | None:
    """§17: top-level caregiver command dispatcher, called from
    text.py::handle before falling through to the medication fast path or
    _caregiver_qa. Returns None - not a fallback Reply - when nothing here
    matched, same narrow-both-ends contract as every other regex handler in
    this codebase (_ack_reminder, _confirm_contact_question): a caregiver
    asking an ordinary question must not be swallowed by this dispatcher.
    """
    flow = await _get_pending_flow(session, caregiver.id)
    if flow is not None:
        return await _continue_flow(session, caregiver, patient, flow, text, reply_language)

    if _SET_APPOINTMENT_RE.search(text):
        template = _pick(CAREGIVER_APPOINTMENT_ASK_DATETIME, reply_language)
        prompt = template.format(patient_name=patient.display_name or "the patient")
        return _flow_reply(prompt, kind="appointment", step="datetime")

    if _SET_BLOODWORK_RE.search(text):
        template = _pick(CAREGIVER_BLOODWORK_ASK_INTAKE, reply_language)
        prompt = template.format(patient_name=patient.display_name or "the patient")
        return _flow_reply(prompt, kind="bloodwork", step="intake")

    if _SET_ADDRESS_RE.search(text):
        template = _pick(CAREGIVER_ADDRESS_ASK, reply_language)
        prompt = template.format(patient_name=patient.display_name or "the patient")
        return _flow_reply(prompt, kind="address", step="address_text")

    if _SET_MEDICATION_RE.search(text):
        return _flow_reply(
            _pick(CAREGIVER_MEDICATION_ASK_NAME, reply_language), kind="medication", step="name"
        )

    if _CHECK_CANDIDATES_RE.search(text):
        return await _start_candidate_review(session, patient, reply_language)

    return None


async def _continue_flow(
    session: AsyncSession,
    caregiver: User,
    patient: User,
    flow: dict,
    text: str,
    reply_language: str,
) -> Reply:
    kind = flow.get("kind")
    if kind == "appointment":
        return await _continue_appointment(session, caregiver, patient, flow, text, reply_language)
    if kind == "bloodwork":
        return await _continue_bloodwork(session, patient, flow, text, reply_language)
    if kind == "address":
        return await _continue_address(session, patient, flow, text, reply_language)
    if kind == "medication":
        return await _continue_medication(session, caregiver, patient, flow, text, reply_language)
    if kind == "candidate_review":
        return await _continue_candidate_review(
            session, caregiver, patient, flow, text, reply_language
        )
    # Unreachable in practice - every flow is started by this module - but a
    # dispatcher on untrusted stored state should never crash on a shape it
    # doesn't recognise.
    logger.error("caregiver_flow_unknown_kind", kind=kind)
    return Reply("Something went wrong. Please send your command again.")


# --- set appointment -------------------------------------------------------


async def _continue_appointment(
    session: AsyncSession,
    caregiver: User,
    patient: User,
    flow: dict,
    text: str,
    reply_language: str,
) -> Reply:
    step = flow["step"]
    data = flow["data"]

    if step == "datetime":
        try:
            # dayfirst=True: Singapore/CLAUDE.md convention is DD/MM, not
            # dateutil's US-style MM/DD default - verified this session that
            # "5/8" parses to 5 August only with this flag set. `default`
            # has minute/second/microsecond zeroed - dateutil fills in any
            # field the input text doesn't specify FROM `default`, so an
            # un-zeroed datetime.now() leaks the current wall-clock second
            # into the appointment time (verified this session: "2pm"
            # parsed to "14:58:53" using bare datetime.now() as default).
            default = datetime.now(ZoneInfo(patient.timezone)).replace(
                minute=0, second=0, microsecond=0
            )
            when = dateutil_parser.parse(text, fuzzy=True, dayfirst=True, default=default)
        except (ValueError, OverflowError):
            template = _pick(CAREGIVER_APPOINTMENT_DATETIME_UNCLEAR, reply_language)
            return _flow_reply(template, kind="appointment", step="datetime", data=data)
        data = {**data, "start_at": when.isoformat()}
        return _flow_reply(
            _pick(CAREGIVER_APPOINTMENT_ASK_LOCATION, reply_language),
            kind="appointment",
            step="location",
            data=data,
        )

    if step == "location":
        data = {**data, "location": text.strip()}
        return _flow_reply(
            _pick(CAREGIVER_APPOINTMENT_ASK_PURPOSE, reply_language),
            kind="appointment",
            step="purpose",
            data=data,
        )

    if step == "purpose":
        data = {**data, "purpose": text.strip()}
        start_at = datetime.fromisoformat(data["start_at"])
        when = calendar_service.render_when(
            start_at, is_all_day=False, tz_name=patient.timezone, language=reply_language
        )
        template = _pick(CAREGIVER_APPOINTMENT_CONFIRM, reply_language)
        prompt = template.format(
            patient_name=patient.display_name or "the patient",
            purpose=data["purpose"],
            when=when,
            location=data["location"],
        )
        return _flow_reply(prompt, kind="appointment", step="confirm", data=data)

    if step == "confirm":
        if _NO_RE.match(text):
            return Reply(_pick(CAREGIVER_APPOINTMENT_CANCELLED, reply_language))
        if not _YES_RE.match(text):
            # Re-ask rather than guess - same shape as every confirm step
            # in this module.
            start_at = datetime.fromisoformat(data["start_at"])
            when = calendar_service.render_when(
                start_at, is_all_day=False, tz_name=patient.timezone, language=reply_language
            )
            template = _pick(CAREGIVER_APPOINTMENT_CONFIRM, reply_language)
            prompt = template.format(
                patient_name=patient.display_name or "the patient",
                purpose=data["purpose"],
                when=when,
                location=data["location"],
            )
            return _flow_reply(prompt, kind="appointment", step="confirm", data=data)

        start_at = datetime.fromisoformat(data["start_at"])
        end_at = start_at + timedelta(hours=1)
        await caregiver_service.create_local_calendar_event(
            session,
            patient_id=patient.id,
            summary=data["purpose"],
            location=data["location"],
            start_at=start_at,
            end_at=end_at,
        )
        await _notify_patient_new_appointment(session, patient, data)
        template = _pick(CAREGIVER_APPOINTMENT_SAVED, reply_language)
        return Reply(template.format(patient_name=patient.display_name or "the patient"))

    logger.error("caregiver_appointment_flow_unknown_step", step=step)
    return Reply(_pick(CAREGIVER_APPOINTMENT_CANCELLED, reply_language))


async def _notify_patient_new_appointment(session: AsyncSession, patient: User, data: dict) -> None:
    """Plain send_text, not send_reminder's template-fallback path -
    send_reminder is shaped around a real `Reminder` row firing at a
    specific clock time (a medication dose), which this isn't. An appointment
    notice isn't time-critical the way a fired reminder is, so if the
    patient's 24h window happens to be closed, queuing until their next
    message (send_text's existing behavior, app/channels/outbound.py) is
    the right degrade, not forcing an immediate templated send."""
    conversation = await conversation_service.get_or_create_open_conversation(session, patient)
    when = calendar_service.render_when(
        datetime.fromisoformat(data["start_at"]),
        is_all_day=False,
        tz_name=patient.timezone,
        language=patient.preferred_language,
    )
    template = _pick(PATIENT_NEW_APPOINTMENT, patient.preferred_language)
    body = template.format(purpose=data["purpose"], when=when, location=data["location"])
    await outbound.send_text(session, patient, conversation.id, body)


# --- set bloodwork -----------------------------------------------------


async def _continue_bloodwork(
    session: AsyncSession, patient: User, flow: dict, text: str, reply_language: str
) -> Reply:
    if _DONE_RE.match(text):
        template = _pick(CAREGIVER_BLOODWORK_DONE, reply_language)
        return Reply(template.format(patient_name=patient.display_name or "the patient"))

    # A typed lab result has no media behind it - documents.media_id is
    # nullable specifically for this case (migration m10). Never restates a
    # medication name or dose in what gets shown back to the patient later
    # (SAFETY-1) - stored verbatim as extracted_text, no AI summarization
    # applied here, so there is no summary to leak one through.
    await documents_service.create_document(
        session,
        doc_kind="blood_work",
        extracted_text=text.strip(),
        summary_en=None,
        summary_zh=None,
        was_scanned=False,
        patient_id=patient.id,
    )
    template = _pick(CAREGIVER_BLOODWORK_TEXT_SAVED, reply_language)
    return _flow_reply(template, kind="bloodwork", step="intake")


# --- set address -------------------------------------------------------


async def _continue_address(
    session: AsyncSession, patient: User, flow: dict, text: str, reply_language: str
) -> Reply:
    await caregiver_service.set_home_address(session, patient.id, text.strip())
    template = _pick(CAREGIVER_ADDRESS_SAVED, reply_language)
    return Reply(template.format(patient_name=patient.display_name or "the patient"))


# --- set medication ------------------------------------------------------


async def _continue_medication(
    session: AsyncSession,
    caregiver: User,
    patient: User,
    flow: dict,
    text: str,
    reply_language: str,
) -> Reply:
    step = flow["step"]
    data = flow["data"]

    if step == "name":
        data = {**data, "name": text.strip()}
        return _flow_reply(
            _pick(CAREGIVER_MEDICATION_ASK_DOSE, reply_language),
            kind="medication",
            step="dose",
            data=data,
        )

    if step == "dose":
        data = {**data, "dose_text": text.strip()}
        template = _pick(CAREGIVER_MEDICATION_ASK_SCHEDULE, reply_language)
        prompt = template.format(patient_name=patient.display_name or "the patient")
        return _flow_reply(prompt, kind="medication", step="schedule", data=data)

    if step == "schedule":
        # §17 set medication: the caregiver describes the schedule in free
        # text - see the module comment on parse_medication_schedule
        # (gemini_client.py) for why this is safe to leave open-ended: the
        # parsed rrule is validated against a strict shape there, and
        # nothing is written here either way - only the "confirm" step
        # below, on an explicit yes, ever calls the DB write. A caregiver
        # reading label_en/label_zh back and confirming it IS the guard
        # against a misparse, same "parse -> confirm -> write" discipline
        # this flow already uses for name and dose.
        parsed = await gemini_client.parse_medication_schedule(
            text.strip(), pipeline="caregiver.medication_schedule", user_id=caregiver.id
        )
        if parsed is None:
            template = _pick(CAREGIVER_MEDICATION_SCHEDULE_UNCLEAR, reply_language)
            return _flow_reply(template, kind="medication", step="schedule", data=data)
        data = {
            **data,
            "schedule_rrule": parsed["rrule"],
            "schedule_label_en": parsed["label_en"],
            "schedule_label_zh": parsed["label_zh"],
        }
        schedule_label = _pick(
            {"en": parsed["label_en"], "zh-Hans": parsed["label_zh"]}, reply_language
        )
        template = _pick(CAREGIVER_MEDICATION_CONFIRM, reply_language)
        prompt = template.format(
            name=data["name"],
            dose_text=data["dose_text"],
            schedule_label=schedule_label,
            patient_name=patient.display_name or "the patient",
        )
        return _flow_reply(prompt, kind="medication", step="confirm", data=data)

    if step == "confirm":
        schedule_label = _pick(
            {"en": data["schedule_label_en"], "zh-Hans": data["schedule_label_zh"]}, reply_language
        )
        if _NO_RE.match(text):
            return Reply(_pick(CAREGIVER_MEDICATION_CANCELLED, reply_language))
        if not _YES_RE.match(text):
            template = _pick(CAREGIVER_MEDICATION_CONFIRM, reply_language)
            prompt = template.format(
                name=data["name"],
                dose_text=data["dose_text"],
                schedule_label=schedule_label,
                patient_name=patient.display_name or "the patient",
            )
            return _flow_reply(prompt, kind="medication", step="confirm", data=data)

        instruction_en, instruction_zh = _render_instruction(data)
        candidate_id = data.get("candidate_id")
        if candidate_id:
            candidate = await session.get(MedicationCandidate, uuid.UUID(candidate_id))
            await caregiver_service.promote_candidate_to_medication(
                session,
                candidate,
                caregiver_id=caregiver.id,
                name=data["name"],
                dose_text=data["dose_text"],
                schedule_rrule=data["schedule_rrule"],
                instruction_en=instruction_en,
                instruction_zh=instruction_zh,
            )
        else:
            await caregiver_service.create_medication_direct(
                session,
                patient_id=patient.id,
                caregiver_id=caregiver.id,
                name=data["name"],
                dose_text=data["dose_text"],
                schedule_rrule=data["schedule_rrule"],
                instruction_en=instruction_en,
                instruction_zh=instruction_zh,
            )
        template = _pick(CAREGIVER_MEDICATION_SAVED, reply_language)
        return Reply(
            template.format(patient_name=patient.display_name or "the patient", name=data["name"])
        )

    logger.error("caregiver_medication_flow_unknown_step", step=step)
    return Reply(_pick(CAREGIVER_MEDICATION_CANCELLED, reply_language))


def _render_instruction(data: dict) -> tuple[str, str]:
    """Plain-language instruction built from the labels the caregiver
    already confirmed at the "schedule" step (parse_medication_schedule),
    not generated fresh here - SAFETY-1: medication instructions must be
    exact, not paraphrased by a model at write time. This only formats the
    pieces that were already shown back and agreed to."""
    return (
        f"Take {data['dose_text']}, {data['schedule_label_en']}.",
        f"{data['schedule_label_zh']}服用{data['dose_text']}。",
    )


# --- check candidates (closes CLAUDE.md SAFETY-1 clause 3) ---------------


async def _start_candidate_review(
    session: AsyncSession, patient: User, reply_language: str
) -> Reply:
    candidates = await caregiver_service.get_pending_candidates(session, patient.id)
    if not candidates:
        return Reply(_pick(CAREGIVER_NO_PENDING_CANDIDATES, reply_language))
    return _prompt_candidate(candidates[0], candidates[1:], patient, reply_language)


def _prompt_candidate(candidate, remaining: list, patient: User, reply_language: str) -> Reply:
    structured = (candidate.extracted or {}).get("structured") or {}
    text_verbatim = (candidate.extracted or {}).get("text_verbatim") or ""
    extracted = text_verbatim.strip() or (
        "No text could be read" if reply_language != "zh-Hans" else "无法读取标签上的文字"
    )
    days_ago = _relative_day(candidate.created_at, reply_language)
    template = _pick(CAREGIVER_CANDIDATE_REVIEW_PROMPT, reply_language)
    prompt = template.format(
        days_ago=days_ago,
        extracted=extracted,
        patient_name=patient.display_name or "the patient",
    )
    data = {
        "candidate_id": str(candidate.id),
        "remaining_ids": [str(c.id) for c in remaining],
        "drug_name": structured.get("drug_name"),
        "dose": structured.get("dose"),
    }
    return _flow_reply(prompt, kind="candidate_review", step="review", data=data)


def _relative_day(created_at: datetime, reply_language: str) -> str:
    delta_days = (datetime.now(UTC).date() - created_at.date()).days
    if reply_language == "zh-Hans":
        if delta_days <= 0:
            return "今天"
        if delta_days == 1:
            return "昨天"
        return f"{delta_days}天前"
    if delta_days <= 0:
        return "today"
    if delta_days == 1:
        return "yesterday"
    return f"{delta_days} days ago"


async def _continue_candidate_review(
    session: AsyncSession,
    caregiver: User,
    patient: User,
    flow: dict,
    text: str,
    reply_language: str,
) -> Reply:
    data = flow["data"]
    if not _YES_RE.match(text) and not _NO_RE.match(text):
        candidate = await session.get(MedicationCandidate, uuid.UUID(data["candidate_id"]))
        remaining = [
            await session.get(MedicationCandidate, uuid.UUID(cid)) for cid in data["remaining_ids"]
        ]
        return _prompt_candidate(candidate, remaining, patient, reply_language)

    if _NO_RE.match(text):
        candidate = await session.get(MedicationCandidate, uuid.UUID(data["candidate_id"]))
        await caregiver_service.reject_candidate(session, candidate, caregiver.id)
        prefix = _pick(CAREGIVER_CANDIDATE_REJECTED, reply_language)
        remaining_ids = data["remaining_ids"]
        if not remaining_ids:
            done = _pick(CAREGIVER_CANDIDATE_REVIEW_DONE, reply_language)
            return Reply(f"{prefix} {done}")
        next_candidate = await session.get(MedicationCandidate, uuid.UUID(remaining_ids[0]))
        further_remaining = [
            await session.get(MedicationCandidate, uuid.UUID(cid)) for cid in remaining_ids[1:]
        ]
        next_reply = _prompt_candidate(next_candidate, further_remaining, patient, reply_language)
        return Reply(f"{prefix} {next_reply.text}", next_reply.meta)

    # Yes: extracted name/dose (if any) pre-fill set medication's flow -
    # vision extraction never includes a schedule, so that's still
    # collected fresh, and the caregiver confirms the whole thing again
    # before anything is written (same confirm-before-write discipline as
    # every other command here). The rejected candidate's own siblings
    # (remaining_ids) are simply not revisited by this path - approving one
    # ends the review session; "check candidates" starts a fresh one for
    # whatever is still pending.
    prefill = {
        "name": data.get("drug_name") or "",
        "candidate_id": data["candidate_id"],
    }
    if prefill["name"]:
        prompt = _pick(CAREGIVER_MEDICATION_ASK_DOSE, reply_language)
        return _flow_reply(prompt, kind="medication", step="dose", data=prefill)
    prompt = _pick(CAREGIVER_MEDICATION_ASK_NAME, reply_language)
    return _flow_reply(prompt, kind="medication", step="name", data=prefill)
