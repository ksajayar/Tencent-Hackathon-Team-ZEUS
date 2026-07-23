from datetime import datetime
from zoneinfo import ZoneInfo

from app.db.models.message import Message
from app.db.models.user import User

# Cap the history window here rather than by token-counting; at 6 short WhatsApp
# turns this comfortably stays well under the ~2000 token budget for the whole
# context block, so a real tokenizer pass isn't worth the dependency yet.
MAX_HISTORY_TURNS = 6


def build_context(*, user: User, history: list[Message]) -> str:
    """Pure formatter: no DB access, so it's testable without a session.

    M2 scope only includes what M1's tables can supply: patient identity, today's
    date, and recent conversation turns. The medications/schedule/contacts/emails
    blocks described in the blueprint land as their tables do (M4/M5/M7).
    """
    now_local = datetime.now(ZoneInfo(user.timezone))
    today = now_local.strftime("%A, %d %B %Y")

    patient_block = (
        f"<patient>name: {user.display_name or 'the patient'}, "
        f"preferred language: {user.preferred_language}, timezone: {user.timezone}</patient>"
    )
    today_block = f"<today>{today}</today>"

    lines = []
    for message in history[-MAX_HISTORY_TURNS:]:
        if not message.body:
            continue
        speaker = "patient" if message.direction == "inbound" else "assistant"
        lines.append(f"{speaker}: {message.body}")
    conversation_block = "<conversation>\n" + "\n".join(lines) + "\n</conversation>"

    return "\n".join([patient_block, today_block, conversation_block])
