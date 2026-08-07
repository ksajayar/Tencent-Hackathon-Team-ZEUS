"""DEMO_MODE (see README): a judge-facing provisioning path that bypasses the
caregiver onboarding/consent chain entirely. Two reserved `User` rows anchor
it, identified by a `wa_id` that can never match Twilio's real
`whatsapp:+<E.164>` format, so neither can ever collide with - or be reached
by - a real inbound number:

  DEMO_TEMPLATE_WA_ID - the canonical dataset lives here, as real verified
    rows (medications, safe zones, a bloodwork document, an appointment, an
    emergency contact). `is_active=False` keeps it out of every existing
    bulk-patient query (app/jobs/location_checkin.py, app/services/seed.py),
    which is the "never leak into a real run" guarantee the spec asks for -
    no extra schema column needed.
  DEMO_CAREGIVER_WA_ID - a real, reachable caregiver User. Target of
    `verified_by` for every cloned medication (same role seed.py's
    CAREGIVER_WA_ID sentinel plays), and the sole recipient of simulated SOS
    alerts in demo mode (app/safety/sos.py::_trigger_demo).

`ensure_demo_template()` runs once at startup (app/main.py, gated on
settings.demo_mode) and is idempotent - upserts by name/doc_kind, same style
as app/services/seed.py. `clone_template_for_patient()` runs once per
newly-provisioned demo patient (app/api/webhooks.py) and copies the
template's *current* rows into fresh, patient-owned rows - "clone, don't
share", so concurrent judges never collide or edit each other's data.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models.calendar import CalendarEvent
from app.db.models.contact import Contact
from app.db.models.document import Document
from app.db.models.location import SafeZone
from app.db.models.medication import Medication
from app.db.models.user import User
from app.services import caregiver as caregiver_service
from app.services import documents as documents_service
from app.services.medications import sync_medication_reminders

logger = get_logger(__name__)

DEMO_TEMPLATE_WA_ID = "demo-template-patient"
DEMO_CAREGIVER_WA_ID = "demo-caregiver"

_TEMPLATE_DISPLAY_NAME = "Mei Ling"

# Bilingual, synthetic (CLAUDE.md demo data rule) - no real person's data.
_TEMPLATE_MEDICATIONS = [
    {
        "name": "Donepezil",
        "dose_text": "1 tablet (5mg)",
        "schedule_rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        "instruction_en": "Take one tablet after breakfast.",
        "instruction_zh": "早餐后服用一片。",
    },
    {
        "name": "Metformin",
        "dose_text": "1 tablet (500mg)",
        "schedule_rrule": "FREQ=DAILY;BYHOUR=8,20;BYMINUTE=0",
        "instruction_en": "Take one tablet twice a day, morning and evening.",
        "instruction_zh": "每天两次，早晚各服用一片。",
    },
    {
        "name": "Vitamin D3",
        "dose_text": "1 capsule (1000 IU)",
        "schedule_rrule": "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        "instruction_en": "Take one capsule after breakfast.",
        "instruction_zh": "早餐后服用一粒。",
    },
]

_TEMPLATE_SAFE_ZONES = [
    {
        "name": "Home",
        "address": "12 Tanjong Rhu Road, #08-03, Singapore 436918",
        "center_lat": 1.301500,
        "center_lon": 103.878200,
        "radius_m": 300,
        "kind": "home",
        "trigger_message_en": None,
        "trigger_message_zh": None,
    },
    {
        "name": "Neighbourhood shop",
        "address": None,
        "center_lat": 1.302400,
        "center_lon": 103.879100,
        "radius_m": 150,
        "kind": "shop",
        "trigger_message_en": "You wanted to buy milk.",
        "trigger_message_zh": "您之前说要买牛奶。",
    },
]

# Sourced from a real gemini_client.summarize_document() call on a fictional
# demo lab report (Harbour Wellness Clinic, explicitly labelled "FICTIONAL
# DEMO DOCUMENT" on its face - no real person's data, per CLAUDE.md's demo
# data rule). The source PDF states no blood type at all; "Blood Type: O+"
# is kept here as an added, clearly-synthetic fact so
# documents_service.extract_blood_type()'s regex still has an exact
# "Blood Type: X" / "血型：X型" phrase to match, same as before this content
# was swapped in. `_TEMPLATE_BLOODWORK_EXTRACTED_TEXT` below is left
# untouched by that addition - it's the genuine OCR verbatim, and the real
# document never mentions a blood type.
_TEMPLATE_BLOODWORK_SUMMARY_EN = (
    "Bloodwork from Harbour Wellness Clinic, dated 2 August 2026. Blood Type: O+. "
    "All results - blood count, glucose, and cholesterol - are within the normal "
    "range. No action needed before your next check-up."
)
_TEMPLATE_BLOODWORK_SUMMARY_ZH = (
    "海港健康诊所的验血报告，日期为2026年8月2日。血型：O型。"
    "血常规、血糖和胆固醇等各项结果均在正常范围内。下次体检前无需采取行动。"
)

# Verbatim extracted_text from that same real OCR call - untouched, no blood
# type inserted (the source document genuinely doesn't state one).
_TEMPLATE_BLOODWORK_EXTRACTED_TEXT = (
    "Harbour Wellness Clinic · Laboratory\n"
    "Haematology & Biochemistry\n"
    "Report no: LAB-DEMO-2026-0802\n"
    "Full Blood Count & Biochemistry Panel\n"
    "PATIENT Mei Ling\n"
    "PATIENT ID PT-DEMO-4471\n"
    "COLLECTION DATE 2 Aug 2026, 08:15\n"
    "REPORTED 2 Aug 2026, 17:40\n"
    "REFERRING CLINICIAN Dr Sarah Lim\n"
    "TEST RESULT UNITS REFERENCE RANGE FLAG\n"
    "Haemoglobin 13.4 g/dL 12.0 – 16.0 Normal\n"
    "White cell count 6.8 x10⁹/L 4.0 – 11.0 Normal\n"
    "Platelets 278 x10⁹/L 150 – 400 Normal\n"
    "Glucose (fasting) 5.0 mmol/L 3.9 – 5.5 Normal\n"
    "Creatinine 71 µmol/L 45 – 90 Normal\n"
    "ALT 22 U/L < 35 Normal\n"
    "Total cholesterol 4.8 mmol/L < 5.2 Normal\n"
    "Verified by: Dr Sarah Lim\n"
    "Laboratory Director · Harbour Wellness Clinic Laboratory"
)

_TEMPLATE_APPOINTMENT_SUMMARY = "Memory clinic follow-up"
_TEMPLATE_APPOINTMENT_LOCATION = "Singapore General Hospital, Memory Clinic"

_TEMPLATE_CONTACT_NAME = "Amy Tan"
_TEMPLATE_CONTACT_PHONE = "+10000099999"  # synthetic, deliberately non-routable


def _next_appointment_window(tz_name: str) -> tuple[datetime, datetime]:
    """3 days out, 10:00-11:00 local time - recomputed every time a template
    or clone is (re)seeded (not frozen at first-seed time), so a demo running
    for days never shows a stale, past "next appointment"."""
    tz = ZoneInfo(tz_name)
    local_now = datetime.now(UTC).astimezone(tz)
    local_start = (local_now + timedelta(days=3)).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    start_at = local_start.astimezone(UTC)
    return start_at, start_at + timedelta(hours=1)


async def _get_or_create_template_patient(session: AsyncSession) -> User:
    result = await session.execute(select(User).where(User.wa_id == DEMO_TEMPLATE_WA_ID))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            wa_id=DEMO_TEMPLATE_WA_ID,
            phone_e164="+10000000000",
            display_name=_TEMPLATE_DISPLAY_NAME,
            role="patient",
            is_active=False,
        )
        session.add(user)
        await session.flush()
    return user


async def get_demo_caregiver(session: AsyncSession) -> User | None:
    result = await session.execute(select(User).where(User.wa_id == DEMO_CAREGIVER_WA_ID))
    return result.scalar_one_or_none()


async def _get_or_create_demo_caregiver(session: AsyncSession) -> User:
    caregiver = await get_demo_caregiver(session)
    if caregiver is None:
        caregiver = User(
            wa_id=DEMO_CAREGIVER_WA_ID,
            phone_e164=settings.demo_emergency_contact_phone or "+10000000001",
            display_name="Demo caregiver",
            role="caregiver",
            is_active=True,
        )
        session.add(caregiver)
        await session.flush()
    return caregiver


# --- template upsert (from the literals above, onto the template patient) ---


async def _upsert_template_medications(
    session: AsyncSession, template: User, caregiver: User
) -> None:
    existing_result = await session.execute(
        select(Medication).where(Medication.patient_id == template.id)
    )
    existing = {m.name: m for m in existing_result.scalars().all()}
    for spec in _TEMPLATE_MEDICATIONS:
        row = existing.get(spec["name"])
        if row is None:
            session.add(
                Medication(
                    patient_id=template.id,
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
    await session.flush()


async def _upsert_template_safe_zones(session: AsyncSession, template: User) -> None:
    existing_result = await session.execute(select(SafeZone).where(SafeZone.user_id == template.id))
    existing = {z.name: z for z in existing_result.scalars().all()}
    for spec in _TEMPLATE_SAFE_ZONES:
        zone = existing.get(spec["name"])
        if zone is None:
            session.add(SafeZone(user_id=template.id, active=True, **spec))
        else:
            zone.address = spec["address"]
            zone.center_lat = spec["center_lat"]
            zone.center_lon = spec["center_lon"]
            zone.radius_m = spec["radius_m"]
            zone.kind = spec["kind"]
            zone.trigger_message_en = spec["trigger_message_en"]
            zone.trigger_message_zh = spec["trigger_message_zh"]
            zone.active = True
    await session.flush()


async def _upsert_template_bloodwork(session: AsyncSession, template: User) -> None:
    result = await session.execute(
        select(Document).where(
            Document.patient_id == template.id, Document.doc_kind == "blood_work"
        )
    )
    document = result.scalars().first()
    if document is None:
        await documents_service.create_document(
            session,
            doc_kind="blood_work",
            extracted_text=_TEMPLATE_BLOODWORK_EXTRACTED_TEXT,
            summary_en=_TEMPLATE_BLOODWORK_SUMMARY_EN,
            summary_zh=_TEMPLATE_BLOODWORK_SUMMARY_ZH,
            was_scanned=False,
            patient_id=template.id,
        )
    else:
        document.extracted_text = _TEMPLATE_BLOODWORK_EXTRACTED_TEXT
        document.summary_en = _TEMPLATE_BLOODWORK_SUMMARY_EN
        document.summary_zh = _TEMPLATE_BLOODWORK_SUMMARY_ZH
        await session.flush()


async def _upsert_template_appointment(session: AsyncSession, template: User) -> None:
    start_at, end_at = _next_appointment_window(template.timezone)
    result = await session.execute(
        select(CalendarEvent).where(
            CalendarEvent.user_id == template.id,
            CalendarEvent.summary == _TEMPLATE_APPOINTMENT_SUMMARY,
        )
    )
    event = result.scalars().first()
    if event is None:
        await caregiver_service.create_local_calendar_event(
            session,
            patient_id=template.id,
            summary=_TEMPLATE_APPOINTMENT_SUMMARY,
            location=_TEMPLATE_APPOINTMENT_LOCATION,
            start_at=start_at,
            end_at=end_at,
        )
    else:
        event.start_at = start_at
        event.end_at = end_at
        event.location = _TEMPLATE_APPOINTMENT_LOCATION
        await session.flush()


async def _upsert_template_emergency_contact(session: AsyncSession, template: User) -> None:
    result = await session.execute(
        select(Contact).where(
            Contact.user_id == template.id, Contact.phone_e164 == _TEMPLATE_CONTACT_PHONE
        )
    )
    contact = result.scalars().first()
    if contact is None:
        session.add(
            Contact(
                user_id=template.id,
                display_name=_TEMPLATE_CONTACT_NAME,
                phone_e164=_TEMPLATE_CONTACT_PHONE,
                # Deliberately not "caregiver" - this is informational
                # template data, never meant to be mistaken for a real
                # caregiver link by find_linked_patient().
                relationship="daughter",
                is_emergency=True,
                priority=1,
                source="manual",
            )
        )
        await session.flush()
    else:
        contact.is_emergency = True
        contact.priority = contact.priority or 1


async def ensure_demo_template(session: AsyncSession) -> None:
    """Idempotent - safe to call on every startup (app/main.py). Upserts by
    name/doc_kind, same style as app/services/seed.py, onto the reserved
    template patient only - never onto a real patient."""
    template = await _get_or_create_template_patient(session)
    caregiver = await _get_or_create_demo_caregiver(session)
    await _upsert_template_medications(session, template, caregiver)
    await _upsert_template_safe_zones(session, template)
    await _upsert_template_bloodwork(session, template)
    await _upsert_template_appointment(session, template)
    await _upsert_template_emergency_contact(session, template)
    await session.flush()
    await sync_medication_reminders(session)
    logger.info(
        "demo_template_ensured",
        template_patient_id=str(template.id),
        demo_caregiver_id=str(caregiver.id),
    )


# --- clone (from the template's live rows, onto a newly-provisioned patient) ---


async def _clone_medications(
    session: AsyncSession, template: User, patient: User, caregiver: User
) -> int:
    existing_names = {
        m.name
        for m in (
            await session.execute(select(Medication).where(Medication.patient_id == patient.id))
        )
        .scalars()
        .all()
    }
    template_meds = (
        (await session.execute(select(Medication).where(Medication.patient_id == template.id)))
        .scalars()
        .all()
    )
    cloned = 0
    for row in template_meds:
        if row.name in existing_names:
            continue
        await caregiver_service.create_medication_direct(
            session,
            patient_id=patient.id,
            caregiver_id=caregiver.id,
            name=row.name,
            dose_text=row.dose_text,
            schedule_rrule=row.schedule_rrule,
            instruction_en=row.instruction_en,
            instruction_zh=row.instruction_zh,
        )
        cloned += 1
    return cloned


async def _clone_safe_zones(session: AsyncSession, template: User, patient: User) -> int:
    existing_names = {
        z.name
        for z in (await session.execute(select(SafeZone).where(SafeZone.user_id == patient.id)))
        .scalars()
        .all()
    }
    template_zones = (
        (await session.execute(select(SafeZone).where(SafeZone.user_id == template.id)))
        .scalars()
        .all()
    )
    cloned = 0
    for zone in template_zones:
        if zone.name in existing_names:
            continue
        session.add(
            SafeZone(
                user_id=patient.id,
                name=zone.name,
                address=zone.address,
                center_lat=zone.center_lat,
                center_lon=zone.center_lon,
                radius_m=zone.radius_m,
                kind=zone.kind,
                trigger_message_en=zone.trigger_message_en,
                trigger_message_zh=zone.trigger_message_zh,
                active=True,
            )
        )
        cloned += 1
    return cloned


async def _clone_bloodwork(session: AsyncSession, template: User, patient: User) -> bool:
    already = (
        (
            await session.execute(
                select(Document).where(
                    Document.patient_id == patient.id, Document.doc_kind == "blood_work"
                )
            )
        )
        .scalars()
        .first()
    )
    if already is not None:
        return False
    template_doc = (
        (
            await session.execute(
                select(Document).where(
                    Document.patient_id == template.id, Document.doc_kind == "blood_work"
                )
            )
        )
        .scalars()
        .first()
    )
    if template_doc is None:
        return False
    await documents_service.create_document(
        session,
        doc_kind="blood_work",
        extracted_text=template_doc.extracted_text,
        summary_en=template_doc.summary_en,
        summary_zh=template_doc.summary_zh,
        was_scanned=False,
        patient_id=patient.id,
    )
    return True


async def _clone_appointment(session: AsyncSession, template: User, patient: User) -> bool:
    already = (
        (await session.execute(select(CalendarEvent).where(CalendarEvent.user_id == patient.id)))
        .scalars()
        .first()
    )
    if already is not None:
        return False
    template_event = (
        (
            await session.execute(
                select(CalendarEvent)
                .where(CalendarEvent.user_id == template.id)
                .order_by(CalendarEvent.start_at)
            )
        )
        .scalars()
        .first()
    )
    if template_event is None:
        return False
    start_at, end_at = _next_appointment_window(patient.timezone)
    await caregiver_service.create_local_calendar_event(
        session,
        patient_id=patient.id,
        summary=template_event.summary,
        location=template_event.location,
        start_at=start_at,
        end_at=end_at,
    )
    return True


async def _clone_emergency_contacts(session: AsyncSession, template: User, patient: User) -> int:
    existing_names = {
        c.display_name
        for c in (await session.execute(select(Contact).where(Contact.user_id == patient.id)))
        .scalars()
        .all()
    }
    template_contacts = (
        (
            await session.execute(
                select(Contact).where(
                    Contact.user_id == template.id, Contact.is_emergency.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    cloned = 0
    for contact in template_contacts:
        if contact.display_name in existing_names:
            continue
        session.add(
            Contact(
                user_id=patient.id,
                display_name=contact.display_name,
                phone_e164=contact.phone_e164,
                relationship=contact.relationship,
                is_emergency=True,
                priority=contact.priority,
                source="manual",
            )
        )
        cloned += 1
    return cloned


async def clone_template_for_patient(session: AsyncSession, patient: User) -> None:
    """Called once, on a brand-new number's first inbound message while
    DEMO_MODE is on (app/api/webhooks.py). Copies the template's rows into
    fresh, patient-owned rows - "clone, don't share": every new number gets
    its own copy so concurrent judges never collide or edit each other's
    data. Google connection is deliberately NOT cloned here - that stays a
    live step (app/pipelines/demo.py sends a real OAuth link afterwards)."""
    template = await _get_or_create_template_patient(session)
    caregiver = await _get_or_create_demo_caregiver(session)

    if patient.display_name is None:
        patient.display_name = template.display_name

    medications = await _clone_medications(session, template, patient, caregiver)
    safe_zones = await _clone_safe_zones(session, template, patient)
    bloodwork = await _clone_bloodwork(session, template, patient)
    appointment = await _clone_appointment(session, template, patient)
    contacts = await _clone_emergency_contacts(session, template, patient)
    await session.flush()
    reminders_touched = await sync_medication_reminders(session)

    logger.info(
        "demo_patient_provisioned",
        user_id=str(patient.id),
        medications=medications,
        safe_zones=safe_zones,
        bloodwork=bloodwork,
        appointment=appointment,
        contacts=contacts,
        reminders_touched=reminders_touched,
    )
