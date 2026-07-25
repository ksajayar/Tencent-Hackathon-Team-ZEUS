from app.core.logging import get_logger
from app.db.models.medication import Medication

logger = get_logger(__name__)


def enforce(text: str, medications: list[Medication], *, fallback: str) -> str:
    """SAFETY-1: every outbound message that mentions a medication must contain
    only drug names verbatim from the source rows. Reminder and medication-
    query text is always template-rendered from these exact rows - never LLM-
    generated - so this is a defensive assertion against a future template
    bug, not a hallucination detector. Mismatch -> discard, send the fallback.
    """
    for medication in medications:
        if medication.name and medication.name not in text:
            logger.error("medication_guard_rejected", medication_id=str(medication.id))
            return fallback
    return text
