import uuid

from app.channels import outbound
from app.core.logging import get_logger
from app.db.models.location import LocationPing
from app.db.models.user import User
from app.db.session import async_session
from app.i18n.strings import (
    LOCATION_ACK_HOME,
    LOCATION_ACK_NO_CONTACT,
    LOCATION_ACK_OUTSIDE_ZONE,
    SAFE_ZONE_ALERT_TO_CONTACT,
)
from app.safety import sos
from app.services import geo
from app.services.contacts import get_emergency_contacts

logger = get_logger(__name__)


def _pick(bilingual: dict, language: str) -> str:
    return bilingual.get(language, bilingual["en"])


async def handle(
    *, user_id: uuid.UUID, conversation_id: uuid.UUID, message_id: uuid.UUID, lat: float, lon: float
) -> None:
    """§07 §7.10: pull-based - WhatsApp gives one pin on request, not a
    background stream. Write the ping, then react: a distress message still
    waiting on a location takes priority (SAFETY-2's follow-up case); else
    shop zone -> its trigger message, home/safe zone -> log only, outside
    every zone -> alert emergency contacts. No AI call anywhere here - this
    is distances and lookups, not reasoning."""
    async with async_session() as session:
        user = await session.get(User, user_id)
        if user is None:
            logger.warning("location_pipeline_user_missing", user_id=str(user_id))
            return
        reply_language = user.preferred_language

        location_ping = LocationPing(user_id=user_id, lat=lat, lon=lon)
        session.add(location_ping)
        await session.flush()

        pending_sos = await sos.find_pending_location_request(session, user_id)
        if pending_sos is not None:
            reply = await sos.trigger(
                session,
                user=user,
                trigger_text=pending_sos.trigger_text or "",
                reply_language=reply_language,
            )
            await outbound.send_text(session, user, conversation_id, reply)
            await session.commit()
            return

        zone = await geo.match_safe_zone(session, user_id=user_id, lat=lat, lon=lon)

        if zone is not None and zone.kind == "shop":
            message = _pick(
                {"en": zone.trigger_message_en, "zh-Hans": zone.trigger_message_zh}, reply_language
            ) or _pick(LOCATION_ACK_HOME, reply_language)
            logger.info("location_shop_zone_triggered", user_id=str(user_id), zone_id=str(zone.id))
            await outbound.send_text(session, user, conversation_id, message)
            await session.commit()
            return

        if zone is not None:
            # home/safe: log only, small reassurance reply, no caregiver alert.
            await outbound.send_text(
                session, user, conversation_id, _pick(LOCATION_ACK_HOME, reply_language)
            )
            await session.commit()
            return

        contacts = await get_emergency_contacts(session, user_id)
        if not contacts:
            await outbound.send_text(
                session, user, conversation_id, _pick(LOCATION_ACK_NO_CONTACT, reply_language)
            )
            await session.commit()
            return

        await _alert_outside_zone(user, contacts, location_ping, reply_language)
        await outbound.send_text(
            session, user, conversation_id, _pick(LOCATION_ACK_OUTSIDE_ZONE, reply_language)
        )
        await session.commit()


async def _alert_outside_zone(
    user: User, contacts: list, location_ping: LocationPing, reply_language: str
) -> None:
    patient_name = user.display_name or ("患者" if reply_language == "zh-Hans" else "the patient")
    place = location_ping.address or f"{location_ping.lat}, {location_ping.lon}"
    body = _pick(SAFE_ZONE_ALERT_TO_CONTACT, reply_language).format(
        patient_name=patient_name, place=place
    )
    for contact in contacts:
        if contact.phone_e164:
            await outbound.send_urgent(contact.phone_e164, body)
