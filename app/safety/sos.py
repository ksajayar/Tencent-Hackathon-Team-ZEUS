import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import outbound
from app.core.logging import get_logger
from app.db.models.location import LocationPing
from app.db.models.sos import SosEvent
from app.db.models.user import User
from app.i18n.strings import (
    SOS_ALERT_TO_CONTACT,
    SOS_CONFIRMATION,
    SOS_NO_CONTACT,
    SOS_SEND_FAILED,
)
from app.services.contacts import get_emergency_contacts

logger = get_logger(__name__)

# §07 §7.9: English matched on word boundaries so "helpful"/"helper" etc.
# don't false-positive; Chinese has no word boundaries to anchor on.
_SOS_RE = re.compile(r"\bhelp\b|\bsos\b|\bemergency\b|救命|紧急|帮我", re.IGNORECASE)

RATE_LIMIT_WINDOW = timedelta(minutes=5)  # per contact, never per user (§10.1)
LOCATION_MAX_AGE = timedelta(minutes=60)
FOLLOWUP_LOCATION_WINDOW = timedelta(minutes=10)


def is_sos_trigger(text: str) -> bool:
    return bool(_SOS_RE.search(text))


def _pick(bilingual: dict, language: str) -> str:
    return bilingual.get(language, bilingual["en"])


async def _recent_location(session: AsyncSession, user_id: uuid.UUID) -> LocationPing | None:
    result = await session.execute(
        select(LocationPing)
        .where(LocationPing.user_id == user_id)
        .order_by(LocationPing.created_at.desc())
        .limit(1)
    )
    ping = result.scalar_one_or_none()
    if ping is None or datetime.now(UTC) - ping.created_at > LOCATION_MAX_AGE:
        return None
    return ping


async def _recently_notified_contact_ids(session: AsyncSession, user_id: uuid.UUID) -> set[str]:
    cutoff = datetime.now(UTC) - RATE_LIMIT_WINDOW
    result = await session.execute(
        select(SosEvent).where(SosEvent.user_id == user_id, SosEvent.triggered_at > cutoff)
    )
    ids: set[str] = set()
    for event in result.scalars().all():
        for entry in event.notified or []:
            if entry.get("outcome") == "sent":
                ids.add(entry["contact_id"])
    return ids


def _location_sentence(ping: LocationPing | None, language: str) -> str:
    if ping is None:
        return ""
    place = ping.address or f"{ping.lat}, {ping.lon}"
    if language == "zh-Hans":
        return f"最近位置：{place}。"
    return f" Last known location: {place}."


# `relationship` is stored as an English key (contacts confirmed as the
# caregiver, and seed.py, both write "caregiver"), so the Chinese branch below
# would otherwise splice an English word into a Chinese sentence - "您的
# caregiver美玲". Falls through to the raw value for anything unmapped, which
# is no worse than before.
_RELATIONSHIP_ZH = {
    "caregiver": "照顾者",
    "daughter": "女儿",
    "son": "儿子",
    "wife": "太太",
    "husband": "先生",
}


def _contact_display(contact, language: str) -> str:
    if contact.relationship and language == "zh-Hans":
        relationship = _RELATIONSHIP_ZH.get(contact.relationship, contact.relationship)
        return f"您的{relationship}{contact.display_name}"
    if contact.relationship:
        return f"your {contact.relationship}, {contact.display_name}"
    return contact.display_name


async def find_pending_location_request(
    session: AsyncSession, user_id: uuid.UUID
) -> SosEvent | None:
    """§07 §7.9: 'also fires on a location pin sent with no accompanying text
    after a prior distress message' - a recent SOS event still missing a
    location means a bare pin that follows is part of the same emergency,
    not a fresh trigger requiring the word 'help' again."""
    cutoff = datetime.now(UTC) - FOLLOWUP_LOCATION_WINDOW
    result = await session.execute(
        select(SosEvent)
        .where(
            SosEvent.user_id == user_id,
            SosEvent.triggered_at > cutoff,
            SosEvent.location_ping_id.is_(None),
        )
        .order_by(SosEvent.triggered_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def trigger(
    session: AsyncSession, *, user: User, trigger_text: str, reply_language: str
) -> str:
    """CLAUDE.md SAFETY-2: no LLM anywhere in this path. Regex match already
    happened in the caller; from here it's DB lookup -> alert -> fixed calm
    reply. Always returns a reply string - SOS never goes silent (§07 §7.9).
    """
    location_ping = await _recent_location(session, user.id)
    contacts = await get_emergency_contacts(session, user.id)

    if not contacts:
        session.add(
            SosEvent(
                user_id=user.id,
                trigger_text=trigger_text[:500],
                location_ping_id=location_ping.id if location_ping else None,
                notified=[],
            )
        )
        await session.flush()
        logger.warning("sos_triggered_no_contacts", user_id=str(user.id))
        return _pick(SOS_NO_CONTACT, reply_language)

    already_notified = await _recently_notified_contact_ids(session, user.id)
    patient_name = user.display_name or ("患者" if reply_language == "zh-Hans" else "the patient")
    alert_body = _pick(SOS_ALERT_TO_CONTACT, reply_language).format(
        patient_name=patient_name,
        location=_location_sentence(location_ping, reply_language),
    )

    notified_entries = []
    for contact in contacts:
        contact_id = str(contact.id)
        if contact_id in already_notified:
            # §10.1: cap at one alert per contact per 5 minutes - they were
            # already reached moments ago, don't spam them again.
            notified_entries.append({"contact_id": contact_id, "outcome": "skipped_rate_limited"})
            continue
        if not contact.phone_e164:
            notified_entries.append({"contact_id": contact_id, "outcome": "no_phone"})
            continue
        sid = await outbound.send_urgent(contact.phone_e164, alert_body)
        notified_entries.append({"contact_id": contact_id, "outcome": "sent" if sid else "failed"})

    session.add(
        SosEvent(
            user_id=user.id,
            trigger_text=trigger_text[:500],
            location_ping_id=location_ping.id if location_ping else None,
            notified=notified_entries,
        )
    )
    await session.flush()

    reached_someone = any(
        entry["outcome"] in ("sent", "skipped_rate_limited") for entry in notified_entries
    )
    logger.warning(
        "sos_triggered",
        user_id=str(user.id),
        contacts_notified=sum(1 for e in notified_entries if e["outcome"] == "sent"),
        reached_someone=reached_someone,
    )

    if not reached_someone:
        # Every attempt genuinely failed this round - never claim help is
        # coming when it might not be (§07 §7.9 errors).
        return _pick(SOS_SEND_FAILED, reply_language)

    return _pick(SOS_CONFIRMATION, reply_language).format(
        name=_contact_display(contacts[0], reply_language)
    )
