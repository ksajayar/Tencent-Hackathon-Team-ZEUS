import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.base import InboundMessage
from app.db.models.message import Conversation, Message
from app.db.models.user import User

CONVERSATION_IDLE_DAYS = 7


async def get_or_create_user(
    session: AsyncSession, *, wa_id: str, phone_e164: str, display_name: str | None
) -> User:
    result = await session.execute(select(User).where(User.wa_id == wa_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(wa_id=wa_id, phone_e164=phone_e164, display_name=display_name)
        session.add(user)
        await session.flush()
    elif display_name and user.display_name != display_name:
        user.display_name = display_name
    return user


async def get_or_create_open_conversation(session: AsyncSession, user: User) -> Conversation:
    result = await session.execute(
        select(Conversation)
        .where(Conversation.user_id == user.id, Conversation.is_open.is_(True))
        .order_by(Conversation.last_message_at.desc())
    )
    conversation = result.scalars().first()

    if conversation is not None:
        idle_for = datetime.now(UTC) - conversation.last_message_at
        if idle_for > timedelta(days=CONVERSATION_IDLE_DAYS):
            conversation.is_open = False
            conversation = None

    if conversation is None:
        conversation = Conversation(user_id=user.id)
        session.add(conversation)
        await session.flush()

    return conversation


async def record_inbound_message(
    session: AsyncSession, *, user: User, conversation: Conversation, inbound: InboundMessage
) -> Message:
    message = Message(
        conversation_id=conversation.id,
        user_id=user.id,
        direction="inbound",
        channel_sid=inbound.message_sid,
        kind=inbound.kind,
        body=inbound.text,
        status="received",
    )
    session.add(message)
    user.last_inbound_at = inbound.received_at
    conversation.last_message_at = inbound.received_at
    await session.flush()
    return message


async def record_outbound_message(
    session: AsyncSession, *, user: User, conversation_id: uuid.UUID, channel_sid: str, body: str
) -> Message:
    message = Message(
        conversation_id=conversation_id,
        user_id=user.id,
        direction="outbound",
        channel_sid=channel_sid,
        kind="text",
        body=body,
        status="sent",
    )
    session.add(message)
    await session.flush()
    return message
