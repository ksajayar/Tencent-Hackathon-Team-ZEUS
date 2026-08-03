from datetime import datetime
from zoneinfo import ZoneInfo

from app.db.models.calendar import CalendarEvent
from app.db.models.email import EmailCache
from app.db.models.medication import Medication
from app.db.models.message import Message
from app.db.models.user import User
from app.services import calendar as calendar_service

# Cap the history window here rather than by token-counting; at 6 short WhatsApp
# turns this comfortably stays well under the ~2000 token budget for the whole
# context block, so a real tokenizer pass isn't worth the dependency yet.
MAX_HISTORY_TURNS = 6


def build_context(
    *,
    user: User,
    history: list[Message],
    events: list[CalendarEvent] | None = None,
    medications: list[Medication] | None = None,
    emails: list[EmailCache] | None = None,
    speaker_label: str = "patient",
) -> str:
    """Pure formatter: no DB access, so it's testable without a session.

    The contacts block lands with its table (M9+). <schedule> (M4),
    <medications> (M5), and <recent_emails> (M7) are here (§05 §5.1) so a
    question that doesn't match its dedicated regex fast path (§07 §7.6)
    still gets answered from real, verified data instead of the model
    guessing - medication_guard itself only applies to the deterministic
    template/query paths, not this free-text one (see medication_guard.py).

    `speaker_label` (§17): the patient pipeline always passes `user` as both
    the subject of <patient>/<schedule>/<medications> and the "inbound"
    speaker in <conversation>, so those are the same person there and the
    default "patient" label is correct. The caregiver pipeline passes the
    PATIENT as `user` (the data is about them) but the CAREGIVER's own
    conversation as `history` - labelling that transcript "patient" would
    misattribute the caregiver's own words to the person they're asking
    about, so that caller overrides this to "caregiver".
    """
    now_local = datetime.now(ZoneInfo(user.timezone))
    today = now_local.strftime("%A, %d %B %Y")

    patient_block = (
        f"<patient>name: {user.display_name or 'the patient'}, "
        f"preferred language: {user.preferred_language}, timezone: {user.timezone}</patient>"
    )
    today_block = f"<today>{today}</today>"
    medications_block = _format_medications(medications or [])
    schedule_block = _format_schedule(events or [], user.timezone)
    emails_block = _format_emails(emails or [])

    lines = []
    for message in history[-MAX_HISTORY_TURNS:]:
        if not message.body:
            continue
        speaker = speaker_label if message.direction == "inbound" else "assistant"
        lines.append(f"{speaker}: {message.body}")
    conversation_block = "<conversation>\n" + "\n".join(lines) + "\n</conversation>"

    return "\n".join(
        [
            patient_block,
            today_block,
            medications_block,
            schedule_block,
            emails_block,
            conversation_block,
        ]
    )


def _format_medications(medications: list[Medication]) -> str:
    if not medications:
        return "<medications>No medications on file.</medications>"
    lines = [f"{m.name}: {m.dose_text} - {m.instruction_en}" for m in medications]
    return "<medications>\n" + "\n".join(lines) + "\n</medications>"


def _format_emails(emails: list[EmailCache]) -> str:
    if not emails:
        return "<recent_emails>No important emails right now.</recent_emails>"
    lines = [f"{e.from_name or e.from_addr or 'unknown sender'}: {e.summary_en}" for e in emails]
    return "<recent_emails>\n" + "\n".join(lines) + "\n</recent_emails>"


def _format_schedule(events: list[CalendarEvent], tz_name: str) -> str:
    if not events:
        return "<schedule>No events scheduled today or tomorrow.</schedule>"
    lines = [_format_event_line(event, tz_name) for event in events]
    return "<schedule>\n" + "\n".join(lines) + "\n</schedule>"


def _format_event_line(event: CalendarEvent, tz_name: str) -> str:
    """`when` is pre-rendered in the persona's own register - "tomorrow at 2 in
    the afternoon", not "Wed 05 Aug 14:00". The model is forbidden to say
    clock times, so handing it only the clock form made it drop the day and
    time from the reply rather than convert them (§07 §7.6)."""
    when = calendar_service.render_when(
        event.start_at, is_all_day=event.is_all_day, tz_name=tz_name, language="en"
    )
    if event.is_all_day:
        when = f"{when} (all day)"
    else:
        until = calendar_service.render_time_of_day(event.end_at, tz_name=tz_name, language="en")
        when = f"{when}, until {until}"

    # Explicit marker rather than relying on the model to compare the
    # rendered time against "now" - it has no reliable clock, and reading
    # "earlier today at 1:45 in the afternoon" as past is exactly the
    # inference it silently got wrong before (stating a finished
    # appointment in the present tense). The persona rule keys off this
    # token, not off the phrasing.
    prefix = "[already happened] " if calendar_service.has_finished(event) else ""
    parts = [f"{prefix}{when}: {event.summary}"]
    if event.location:
        parts.append(f"at {event.location}")
    names = [
        a.get("display_name") or a.get("email")
        for a in (event.attendees or [])
        if a.get("display_name") or a.get("email")
    ]
    if names:
        parts.append(f"with {', '.join(names)}")
    return " ".join(parts)
