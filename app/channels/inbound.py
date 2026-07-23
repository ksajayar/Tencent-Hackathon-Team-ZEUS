from datetime import UTC, datetime

from app.channels.base import InboundMessage


def _detect_kind(form: dict) -> str:
    if form.get("Latitude") and form.get("Longitude"):
        return "location"
    num_media = int(form.get("NumMedia", "0") or "0")
    if num_media > 0:
        content_type = form.get("MediaContentType0", "")
        if content_type.startswith("audio/"):
            return "audio"
        if content_type.startswith("image/"):
            return "image"
        if content_type == "application/pdf":
            return "document"
        if content_type == "text/vcard":
            return "contact"
        return "document"
    return "text"


def parse_inbound(form: dict) -> InboundMessage:
    """Normalize a Twilio webhook form payload into the internal shape.

    M1 scope: only 'text' carries a populated `text` field. Other kinds are
    still classified correctly for storage, but their pipelines (voice, vision,
    document, location, contact) land in later milestones.
    """
    kind = _detect_kind(form)
    wa_id = form.get("WaId", "")
    from_field = form.get("From", "")
    phone_e164 = from_field.removeprefix("whatsapp:")

    coords = None
    if kind == "location":
        lat = form.get("Latitude")
        lon = form.get("Longitude")
        if lat and lon:
            coords = (float(lat), float(lon))

    return InboundMessage(
        wa_id=wa_id,
        phone_e164=phone_e164,
        display_name=form.get("ProfileName"),
        message_sid=form.get("MessageSid", ""),
        kind=kind,
        text=form.get("Body") if kind == "text" else None,
        media=None,
        coords=coords,
        received_at=datetime.now(UTC),
    )
