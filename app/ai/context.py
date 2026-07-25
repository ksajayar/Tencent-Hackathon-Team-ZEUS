from datetime import datetime
from zoneinfo import ZoneInfo

from app.db.models.calendar import CalendarEvent
from app.db.models.message import Message
from app.db.models.user import User

# Cap the history window here rather than by token-counting; at 6 short WhatsApp
# turns this comfortably stays well under the ~2000 token budget for the whole
# context block, so a real tokenizer pass isn't worth the dependency yet.
MAX_HISTORY_TURNS = 6


def build_context(
    *, user: User, history: list[Message], events: list[CalendarEvent] | None = None
) -> str:
    """Pure formatter: no DB access, so it's testable without a session.

    The medications/contacts/emails blocks described in the blueprint land as
    their tables do (M5/M7). M4 adds <schedule> (§05 §5.1).
    """
    now_local = datetime.now(ZoneInfo(user.timezone))
    today = now_local.strftime("%A, %d %B %Y")

    patient_block = (
        f"<patient>name: {user.display_name or 'the patient'}, "
        f"preferred language: {user.preferred_language}, timezone: {user.timezone}</patient>"
    )
    today_block = f"<today>{today}</today>"
    schedule_block = _format_schedule(events or [], user.timezone)

    lines = []
    for message in history[-MAX_HISTORY_TURNS:]:
        if not message.body:
            continue
        speaker = "patient" if message.direction == "inbound" else "assistant"
        lines.append(f"{speaker}: {message.body}")
    conversation_block = "<conversation>\n" + "\n".join(lines) + "\n</conversation>"

    return "\n".join([patient_block, today_block, schedule_block, conversation_block])


def _format_schedule(events: list[CalendarEvent], tz_name: str) -> str:
    if not events:
        return "<schedule>No events scheduled today or tomorrow.</schedule>"
    tz = ZoneInfo(tz_name)
    lines = [_format_event_line(event, tz) for event in events]
    return "<schedule>\n" + "\n".join(lines) + "\n</schedule>"


def _format_event_line(event: CalendarEvent, tz: ZoneInfo) -> str:
    local_start = event.start_at.astimezone(tz)
    if event.is_all_day:
        when = local_start.strftime("%a %d %b") + " (all day)"
    else:
        local_end = event.end_at.astimezone(tz)
        when = f"{local_start.strftime('%a %d %b %H:%M')}-{local_end.strftime('%H:%M')}"

    parts = [f"{when}: {event.summary}"]
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
