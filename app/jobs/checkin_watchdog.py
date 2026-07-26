import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import outbound
from app.core.logging import get_logger
from app.db.models.location import LocationPing
from app.db.models.message import Message
from app.db.models.user import User
from app.db.session import async_session
from app.i18n.strings import CHECKIN_NO_RESPONSE_ALERT
from app.services.contacts import get_emergency_contacts

logger = get_logger(__name__)

# §07 §7.10: "no reply in 20 min -> notify caregiver: no response".
NO_RESPONSE_THRESHOLD = timedelta(minutes=20)


async def _has_ping_since(session: AsyncSession, user_id: uuid.UUID, since: datetime) -> bool:
    result = await session.execute(
        select(LocationPing.id)
        .where(LocationPing.user_id == user_id, LocationPing.created_at > since)
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def check_stale_checkins() -> None:
    """Every 5 minutes (§09): a check-in prompt (jobs/location_checkin.py)
    unanswered for 20+ minutes gets the emergency contacts notified once -
    `meta.alerted` guards against re-alerting on every later run of this job.
    """
    cutoff = datetime.now(UTC) - NO_RESPONSE_THRESHOLD
    async with async_session() as session:
        result = await session.execute(
            select(Message).where(
                Message.direction == "outbound",
                Message.created_at <= cutoff,
                Message.meta.contains({"checkin_prompt": True}),
            )
        )
        candidates = [m for m in result.scalars().all() if not m.meta.get("alerted")]
        if not candidates:
            return

        alerted = 0
        for message in candidates:
            user = await session.get(User, message.user_id)
            if user is None:
                continue
            if await _has_ping_since(session, user.id, message.created_at):
                continue  # they did share a location after this prompt

            contacts = await get_emergency_contacts(session, user.id)
            patient_name = user.display_name or "the patient"
            body = CHECKIN_NO_RESPONSE_ALERT.get(
                user.preferred_language, CHECKIN_NO_RESPONSE_ALERT["en"]
            ).format(patient_name=patient_name)
            for contact in contacts:
                if contact.phone_e164:
                    await outbound.send_urgent(contact.phone_e164, body)

            message.meta = {**message.meta, "alerted": True}
            alerted += 1

        await session.commit()
        if alerted:
            logger.warning("checkin_no_response_alerted", count=alerted)
