import asyncio

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
        raise NotImplementedError("Template send lands in M5 (reminders)")


provider = TwilioWhatsAppProvider()
