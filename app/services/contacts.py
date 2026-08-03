import re
import uuid

import vobject
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models.contact import Contact
from app.db.models.user import User

logger = get_logger(__name__)

_PHONE_CLEAN_RE = re.compile(r"[^\d+]")

# CLAUDE.md fixes the app to one timezone (Asia/Singapore) and SOS already
# hardcodes the Singapore emergency number (995) - _caregiver_key below
# follows that same fixed-country assumption for numbers with no country
# code, rather than pulling in a full E.164 library for one country.
_SG_COUNTRY_CODE = "65"
_SG_LOCAL_LENGTH = 8


def _normalize_phone(raw: str) -> str | None:
    cleaned = _PHONE_CLEAN_RE.sub("", raw)
    return cleaned or None


def _caregiver_key(raw: str | None) -> str | None:
    """Comparison-only phone key for matching a caregiver's inbound number
    against the number saved on their `contacts` row (§17 role detection).

    _normalize_phone() only strips punctuation - a vCard saved as "9123 4567"
    and Twilio's From header "+6591234567" would never compare equal without
    this. Assumes Singapore for any number that isn't already carrying a
    country code (heuristic: standard 8-digit local length) - wrong for a
    caregiver dialling in from outside Singapore without one, but this app
    has no other supported country to disambiguate against."""
    digits = _normalize_phone(raw or "")
    if not digits:
        return None
    stripped = digits.lstrip("+")
    if len(stripped) == _SG_LOCAL_LENGTH:
        stripped = _SG_COUNTRY_CODE + stripped
    return stripped


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


async def find_linked_patient(session: AsyncSession, caregiver_phone_e164: str) -> User | None:
    """§17 caregiver role detection. There is no `caregiver_links` table - a
    `contacts` row with `relationship='caregiver'` (set by the two-step
    contact-confirmation chain in text.py) IS the link, and `contact.user_id`
    is the patient. Matches by phone since that's the only thing a caregiver's
    own inbound message and a saved contact card share.

    Table is tiny for a demo, so a full scan per call is fine - there's no
    other trigger point for "the caregiver just messaged for the first time"
    to cache this against."""
    key = _caregiver_key(caregiver_phone_e164)
    if key is None:
        return None
    result = await session.execute(select(Contact).where(Contact.relationship == "caregiver"))
    for contact in result.scalars().all():
        if _caregiver_key(contact.phone_e164) == key:
            return await session.get(User, contact.user_id)
    return None


async def find_caregiver_user(session: AsyncSession, patient_id: uuid.UUID) -> User | None:
    """The reverse of find_linked_patient: given a patient, find their
    caregiver's own `users` row (only exists once that caregiver has
    messaged at least once - see §17 role detection). Used by image.py to
    notify a caregiver when a new medication_candidate is created from the
    patient's own photo (CLAUDE.md SAFETY-1 clause 3's review step needs
    someone actually told a candidate exists - previously nothing was)."""
    result = await session.execute(
        select(Contact).where(Contact.user_id == patient_id, Contact.relationship == "caregiver")
    )
    contact = result.scalars().first()
    if contact is None or not contact.phone_e164:
        return None
    key = _caregiver_key(contact.phone_e164)
    if key is None:
        return None
    users_result = await session.execute(select(User).where(User.role == "caregiver"))
    for user in users_result.scalars().all():
        if _caregiver_key(user.phone_e164) == key:
            return user
    return None
