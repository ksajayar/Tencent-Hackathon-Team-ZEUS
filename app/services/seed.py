from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.contact import Contact
from app.db.models.location import SafeZone
from app.db.models.medication import Medication
from app.db.models.user import User
from app.services.medications import sync_medication_reminders

logger = get_logger(__name__)

# Fictional Singapore coordinates for the demo account only (CLAUDE.md demo
# data rule). Home zone covers the shop zone too so the shop's more specific
# (smaller-radius) match wins per app/services/geo.py's zone-selection rule.
_DEMO_SAFE_ZONES = [
    {
        "name": "Home",
        "center_lat": 1.352100,
        "center_lon": 103.819800,
        "radius_m": 300,
        "kind": "home",
        "trigger_message_en": None,
        "trigger_message_zh": None,
    },
    {
        "name": "Neighbourhood shop",
        "center_lat": 1.353000,
        "center_lon": 103.821000,
        "radius_m": 150,
        "kind": "shop",
        "trigger_message_en": "You wanted to buy milk.",
        "trigger_message_zh": "您之前说要买牛奶。",
    },
]

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


async def _seed_safe_zones(session: AsyncSession, patient: User) -> int:
    existing_result = await session.execute(select(SafeZone).where(SafeZone.user_id == patient.id))
    existing = {z.name: z for z in existing_result.scalars().all()}

    touched = 0
    for spec in _DEMO_SAFE_ZONES:
        zone = existing.get(spec["name"])
        if zone is None:
            session.add(SafeZone(user_id=patient.id, active=True, **spec))
        else:
            zone.center_lat = spec["center_lat"]
            zone.center_lon = spec["center_lon"]
            zone.radius_m = spec["radius_m"]
            zone.kind = spec["kind"]
            zone.trigger_message_en = spec["trigger_message_en"]
            zone.trigger_message_zh = spec["trigger_message_zh"]
            zone.active = True
        touched += 1
    return touched


async def _seed_emergency_contact(session: AsyncSession, patient: User) -> bool:
    """§09 seed step: an optional real second phone so 'help alerts a second
    phone within seconds' (M9 Done-when) is testable without a vCard upload
    first. Skipped entirely when DEMO_EMERGENCY_CONTACT_PHONE is unset."""
    if not settings.demo_emergency_contact_phone:
        return False

    result = await session.execute(
        select(Contact).where(
            Contact.user_id == patient.id,
            Contact.phone_e164 == settings.demo_emergency_contact_phone,
        )
    )
    contact = result.scalar_one_or_none()
    if contact is None:
        session.add(
            Contact(
                user_id=patient.id,
                display_name="Caregiver (seed)",
                phone_e164=settings.demo_emergency_contact_phone,
                relationship="caregiver",
                is_emergency=True,
                priority=1,
                source="manual",
            )
        )
    else:
        contact.is_emergency = True
        contact.priority = contact.priority or 1
    return True


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
    safe_zones_touched = 0
    emergency_contact_seeded = False
    for patient in patients:
        safe_zones_touched += await _seed_safe_zones(session, patient)
        emergency_contact_seeded = await _seed_emergency_contact(session, patient) or (
            emergency_contact_seeded
        )
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
        safe_zones=safe_zones_touched,
        emergency_contact_seeded=emergency_contact_seeded,
    )
    return {
        "patients": len(patients),
        "medications": medication_count,
        "reminders_touched": reminders_touched,
        "safe_zones": safe_zones_touched,
        "emergency_contact_seeded": emergency_contact_seeded,
    }
