import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.calendar import CalendarEvent
from app.db.models.location import SafeZone
from app.db.models.medication import Medication, MedicationCandidate


async def get_home_zone(session: AsyncSession, patient_id: uuid.UUID) -> SafeZone | None:
    result = await session.execute(
        select(SafeZone).where(SafeZone.user_id == patient_id, SafeZone.kind == "home")
    )
    return result.scalars().first()


async def set_home_address(session: AsyncSession, patient_id: uuid.UUID, address: str) -> SafeZone:
    """§17 set address. Updates the existing home zone (seed.py already
    creates one for the demo patient) if present, else creates a
    coordinate-less one - see docs/17 §6.3 and migration m10 for why
    center_lat/center_lon/radius_m had to become nullable for this to be
    possible without a placeholder value sitting in the table."""
    zone = await get_home_zone(session, patient_id)
    if zone is not None:
        zone.address = address
        await session.flush()
        return zone
    zone = SafeZone(user_id=patient_id, name="Home", address=address, kind="home", active=True)
    session.add(zone)
    await session.flush()
    return zone


async def create_local_calendar_event(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    summary: str,
    location: str | None,
    start_at: datetime,
    end_at: datetime,
) -> CalendarEvent:
    """§17 set appointment, decision D1-b: a locally-created event, never a
    real Google Calendar write - app/google/oauth.py's read-only scope
    stays true, no OAuth re-consent needed. `google_event_id` and
    `content_hash` are random, unique placeholders (never real Google
    values) purely to satisfy NOT NULL/UNIQUE - sync_user_calendar() only
    ever adds or updates rows by matching a real Google event id, and never
    deletes a row absent from Google's list, so this row is invisible to
    every future sync rather than at risk from it."""
    now = datetime.now(UTC)
    event = CalendarEvent(
        user_id=patient_id,
        google_event_id=f"local:{uuid.uuid4()}",
        summary=summary,
        location=location,
        start_at=start_at,
        end_at=end_at,
        is_all_day=False,
        content_hash=f"local:{uuid.uuid4()}",
        synced_at=now,
    )
    session.add(event)
    await session.flush()
    return event


async def get_pending_candidates(
    session: AsyncSession, patient_id: uuid.UUID
) -> list[MedicationCandidate]:
    """§17 "check candidates" / CLAUDE.md SAFETY-1 clause 3: oldest first,
    so a caregiver working through a backlog clears it in the order photos
    were actually taken."""
    result = await session.execute(
        select(MedicationCandidate)
        .where(
            MedicationCandidate.patient_id == patient_id,
            MedicationCandidate.status == "pending",
        )
        .order_by(MedicationCandidate.created_at.asc())
    )
    return list(result.scalars().all())


async def reject_candidate(
    session: AsyncSession, candidate: MedicationCandidate, caregiver_id: uuid.UUID
) -> None:
    candidate.status = "rejected"
    candidate.reviewed_by = caregiver_id
    candidate.reviewed_at = datetime.now(UTC)
    await session.flush()


async def promote_candidate_to_medication(
    session: AsyncSession,
    candidate: MedicationCandidate,
    *,
    caregiver_id: uuid.UUID,
    name: str,
    dose_text: str,
    schedule_rrule: str,
    instruction_en: str,
    instruction_zh: str,
) -> Medication:
    """CLAUDE.md SAFETY-1 clause 3: the only place in the codebase that turns
    a medication_candidate into a real medications row - previously nothing
    did (verified this session: no code anywhere wrote to `reviewed_by` or
    moved `status` off 'pending'). Sets `verified_by` so
    get_active_medications() and the reminder scheduler actually see the
    row (SAFETY-1 clause 1's gate), and marks the source candidate
    'approved' + reviewed so it stops appearing in future candidate
    listings."""
    now = datetime.now(UTC)
    medication = Medication(
        patient_id=candidate.patient_id,
        name=name,
        dose_text=dose_text,
        schedule_rrule=schedule_rrule,
        instruction_en=instruction_en,
        instruction_zh=instruction_zh,
        verified_by=caregiver_id,
        verified_at=now,
        active=True,
    )
    session.add(medication)
    candidate.status = "approved"
    candidate.reviewed_by = caregiver_id
    candidate.reviewed_at = now
    await session.flush()
    return medication


async def create_medication_direct(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    caregiver_id: uuid.UUID,
    name: str,
    dose_text: str,
    schedule_rrule: str,
    instruction_en: str,
    instruction_zh: str,
) -> Medication:
    """set medication with no candidate behind it - the caregiver typed
    everything directly rather than reviewing a photo. Same
    verified_by/verified_at gating as promote_candidate_to_medication."""
    medication = Medication(
        patient_id=patient_id,
        name=name,
        dose_text=dose_text,
        schedule_rrule=schedule_rrule,
        instruction_en=instruction_en,
        instruction_zh=instruction_zh,
        verified_by=caregiver_id,
        verified_at=datetime.now(UTC),
        active=True,
    )
    session.add(medication)
    await session.flush()
    return medication
