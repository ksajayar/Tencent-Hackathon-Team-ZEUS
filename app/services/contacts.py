import re
import uuid

import vobject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models.contact import Contact

logger = get_logger(__name__)

_PHONE_CLEAN_RE = re.compile(r"[^\d+]")


def _normalize_phone(raw: str) -> str | None:
    cleaned = _PHONE_CLEAN_RE.sub("", raw)
    return cleaned or None


def parse_vcard(raw_text: str) -> dict | None:
    """§07 §7.11: vCard arrives as `text/vcard` media, parsed with `vobject`.
    Returns None on anything unparseable or nameless - a contact without a
    name isn't useful to save."""
    try:
        card = vobject.readOne(raw_text)
    except Exception:
        logger.warning("vcard_parse_failed")
        return None

    fn = getattr(card, "fn", None)
    display_name = fn.value.strip() if fn and fn.value else None
    if not display_name:
        return None

    phone = None
    if hasattr(card, "tel_list") and card.tel_list:
        phone = _normalize_phone(card.tel_list[0].value)

    email = None
    if hasattr(card, "email_list") and card.email_list:
        email = card.email_list[0].value.strip()

    return {"display_name": display_name, "phone_e164": phone, "email": email}


async def upsert_from_vcard(session: AsyncSession, *, user_id: uuid.UUID, parsed: dict) -> Contact:
    """Dedupe on (user_id, phone) when a phone is present, else on
    (user_id, display_name). Never touches `is_emergency` on an update - that
    flag is only ever set by the explicit yes/no confirmation (§07 §7.11)."""
    query = select(Contact).where(Contact.user_id == user_id)
    if parsed["phone_e164"]:
        query = query.where(Contact.phone_e164 == parsed["phone_e164"])
    else:
        query = query.where(Contact.display_name == parsed["display_name"])
    existing = (await session.execute(query)).scalars().first()

    if existing is not None:
        existing.display_name = parsed["display_name"]
        existing.email = parsed["email"] or existing.email
        await session.flush()
        return existing

    contact = Contact(
        user_id=user_id,
        display_name=parsed["display_name"],
        phone_e164=parsed["phone_e164"],
        email=parsed["email"],
        source="vcard",
    )
    session.add(contact)
    await session.flush()
    return contact


async def get_emergency_contacts(session: AsyncSession, user_id: uuid.UUID) -> list[Contact]:
    """§07 §7.9 step 2: emergency contacts, priority order. Used by both SOS
    and the outside-all-zones location alert."""
    result = await session.execute(
        select(Contact)
        .where(Contact.user_id == user_id, Contact.is_emergency.is_(True))
        .order_by(Contact.priority.asc().nulls_last())
    )
    return list(result.scalars().all())
