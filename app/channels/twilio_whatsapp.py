import asyncio
import json

from twilio.request_validator import RequestValidator
from twilio.rest import Client

from app.core.config import settings


class TwilioWhatsAppProvider:
    def __init__(self) -> None:
        self._client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
        self._validator = RequestValidator(settings.twilio_auth_token)

    def validate_signature(self, url: str, params: dict, signature: str) -> bool:
        return self._validator.validate(url, params, signature)

    async def send_text(self, to: str, body: str) -> str:
        to_addr = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
        message = await asyncio.to_thread(
            self._client.messages.create,
            from_=settings.twilio_whatsapp_from,
            to=to_addr,
            body=body,
        )
        return message.sid

    async def send_media(self, to: str, media_url: str, mime_type: str) -> str:
        raise NotImplementedError("Media send lands in M6 (voice) / M8 (vision & documents)")

    async def send_template(self, to: str, template_sid: str, variables: dict[str, str]) -> str:
        """§03 §3.4: the sandbox's fixed pre-approved templates, sent by
        Content API SID (starts 'HX') rather than free-form Body."""
        to_addr = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
        message = await asyncio.to_thread(
            self._client.messages.create,
            from_=settings.twilio_whatsapp_from,
            to=to_addr,
            content_sid=template_sid,
            content_variables=json.dumps(variables),
        )
        return message.sid

    async def list_content_templates(self) -> list[dict]:
        """Content API templates available on this account, so the sandbox's
        'Appointment reminder' ContentSid can be found via /internal/debug
        instead of hunting through the Twilio console."""

        def _list():
            return self._client.content.v1.contents.list(limit=20)

        items = await asyncio.to_thread(_list)
        return [{"sid": c.sid, "friendly_name": c.friendly_name} for c in items]


provider = TwilioWhatsAppProvider()
