from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models.medication import Medication
from app.db.models.user import User
from app.services.medications import sync_medication_reminders

logger = get_logger(__name__)

# Never messaged over WhatsApp - only exists as the medications.verified_by
# target. No caregiver web dashboard or notification channel exists (CLAUDE.md
# "what not to build"), so this is a placeholder identity, not a live contact.
CAREGIVER_WA_ID = "seed-caregiver"

_DEMO_MEDICATIONS = [
    {
        "name": "Donepezil",
        "dose_text": "1 tablet",
        "schedule_rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        "instruction_en": "Take one tablet after breakfast.",
        "instruction_zh": "早餐后服用一片。",
    },
    {
        "name": "Metformin",
        "dose_text": "1 tablet",
        "schedule_rrule": "FREQ=DAILY;BYHOUR=19;BYMINUTE=0",
        "instruction_en": "Take one tablet after dinner.",
        "instruction_zh": "晚餐后服用一片。",
    },
]


async def _get_or_create_caregiver(session: AsyncSession) -> User:
    result = await session.execute(select(User).where(User.wa_id == CAREGIVER_WA_ID))
    caregiver = result.scalar_one_or_none()
    if caregiver is None:
        caregiver = User(
            wa_id=CAREGIVER_WA_ID,
            phone_e164="+10000000000",
            display_name="Caregiver (seed)",
            role="caregiver",
        )
        session.add(caregiver)
        await session.flush()
    return caregiver


async def seed_demo_data(session: AsyncSession) -> dict:
    """CLAUDE.md demo data rule: synthetic patient, synthetic medications.
    Idempotent - upserts by (patient_id, name), safe to call repeatedly
    (§08 §8.5). Seeds every existing patient user (single-tenant demo scope -
    CLAUDE.md: no multi-tenant isolation), then reconciles reminders
    immediately so a forced sync/fire can be tested right away rather than
    waiting for the next scheduled reconciliation pass.
    """
    patients_result = await session.execute(select(User).where(User.role == "patient"))
    patients = list(patients_result.scalars().all())
    if not patients:
        logger.warning("seed_no_patients_found")
        return {"patients": 0, "medications": 0, "reminders_touched": 0}

    caregiver = await _get_or_create_caregiver(session)

    medication_count = 0
    for patient in patients:
        existing_result = await session.execute(
            select(Medication).where(Medication.patient_id == patient.id)
        )
        existing = {m.name: m for m in existing_result.scalars().all()}

        for spec in _DEMO_MEDICATIONS:
            row = existing.get(spec["name"])
            if row is None:
                session.add(
                    Medication(
                        patient_id=patient.id,
                        verified_by=caregiver.id,
                        verified_at=datetime.now(UTC),
                        active=True,
                        **spec,
                    )
                )
            else:
                row.dose_text = spec["dose_text"]
                row.schedule_rrule = spec["schedule_rrule"]
                row.instruction_en = spec["instruction_en"]
                row.instruction_zh = spec["instruction_zh"]
                row.verified_by = caregiver.id
                row.verified_at = datetime.now(UTC)
                row.active = True
            medication_count += 1

    await session.flush()
    reminders_touched = await sync_medication_reminders(session)

    logger.info(
        "seed_demo_data_run",
        patients=len(patients),
        medications=medication_count,
        reminders_touched=reminders_touched,
    )
    return {
        "patients": len(patients),
        "medications": medication_count,
        "reminders_touched": reminders_touched,
    }
