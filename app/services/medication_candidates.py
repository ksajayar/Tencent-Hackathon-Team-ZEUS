import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.medication import MedicationCandidate

# §06 §6.3: below this, don't even show the value - a confidently wrong drug
# name read aloud to someone who can't check it is the worst outcome here.
MIN_MEDICAL_FIELD_CONFIDENCE = 0.8


async def create_candidate(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    source_media_id: uuid.UUID,
    vision_result: dict,
) -> MedicationCandidate:
    """§05 §5.3 / §08: OCR and vision never write to `medications` (SAFETY-1)
    - this is the only table a pill-bottle or prescription photo can reach.
    Structured fields below the confidence floor are dropped rather than
    stored low-confidence, per §06 §6.3."""
    confidence = float(vision_result.get("confidence") or 0.0)
    structured = dict(vision_result.get("structured") or {})
    if confidence < MIN_MEDICAL_FIELD_CONFIDENCE:
        structured = {"drug_name": None, "dose": None, "frequency": None}

    candidate = MedicationCandidate(
        patient_id=patient_id,
        source_media_id=source_media_id,
        extracted={
            "kind": vision_result.get("kind"),
            "text_verbatim": vision_result.get("text_verbatim"),
            "script": vision_result.get("script"),
            "structured": structured,
        },
        confidence=confidence,
        status="pending",
    )
    session.add(candidate)
    await session.flush()
    return candidate
