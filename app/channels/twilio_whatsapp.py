import asyncio
import json

import requests
from twilio.request_validator import RequestValidator
from twilio.rest import Client

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

TYPING_INDICATOR_URL = "https://messaging.twilio.com/v3/Indicators/Typing.json"


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
        """CHANNEL-2: media-only send, never combined with Body - a Body
        alongside MediaUrl is silently dropped by Twilio (§03 §3.4)."""
        to_addr = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
        message = await asyncio.to_thread(
            self._client.messages.create,
            from_=settings.twilio_whatsapp_from,
            to=to_addr,
            media_url=[media_url],
        )
        return message.sid

    async def send_template(self, to: str, template_sid: str, variables: dict[str, str]) -> str:
        """§03 §3.4: an approved template, sent by Content API SID (starts
        'HX') rather than free-form Body - required outside the 24h window
        regardless of which number this account uses."""
        to_addr = to if to.startswith("whatsapp:") else f"whatsapp:{to}"
        message = await asyncio.to_thread(
            self._client.messages.create,
            from_=settings.twilio_whatsapp_from,
            to=to_addr,
            content_sid=template_sid,
            content_variables=json.dumps(variables),
        )
        return message.sid

    async def send_typing_indicator(self, message_sid: str) -> None:
        """Shows WhatsApp's 'typing...' indicator (Twilio public beta) while
        the background worker generates a reply. References the inbound
        message being answered; disappears when that reply is delivered or
        after 25s, whichever is first. Best-effort - never worth failing or
        delaying a reply over, so failures are logged and swallowed."""
        try:
            response = await asyncio.to_thread(
                requests.post,
                TYPING_INDICATOR_URL,
                auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                json={"channel": "whatsapp", "messageId": message_sid},
                timeout=5,
            )
        except Exception:
            logger.warning("typing_indicator_request_failed", message_sid=message_sid)
            return

        if response.status_code >= 300:
            logger.warning(
                "typing_indicator_rejected",
                message_sid=message_sid,
                status_code=response.status_code,
                response_body=response.text[:300],
            )
        else:
            logger.info("typing_indicator_sent", message_sid=message_sid)

    async def list_content_templates(self) -> list[dict]:
        """Content API templates available on this account, so the approved
        'Appointment reminder' ContentSid can be found via /internal/debug
        instead of hunting through the Twilio console."""

        def _list():
            return self._client.content.v1.contents.list(limit=20)

        items = await asyncio.to_thread(_list)
        return [{"sid": c.sid, "friendly_name": c.friendly_name} for c in items]


provider = TwilioWhatsAppProvider()
