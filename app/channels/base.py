from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class MediaRef:
    local_path: str
    mime_type: str
    sha256: str


@dataclass
class InboundMessage:
    wa_id: str
    phone_e164: str
    display_name: str | None
    message_sid: str
    kind: str  # text|audio|image|document|location|contact
    text: str | None
    media: MediaRef | None
    coords: tuple[float, float] | None
    received_at: datetime


class ChannelProvider(Protocol):
    async def send_text(self, to: str, body: str) -> str: ...

    async def send_media(self, to: str, media_url: str, mime_type: str) -> str: ...

    async def send_template(self, to: str, template_sid: str, variables: dict[str, str]) -> str: ...
