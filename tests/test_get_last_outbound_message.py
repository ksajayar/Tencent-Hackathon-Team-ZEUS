"""Regression test for the voice-reply created_at tie.

A voice reply is two `messages` rows - text then audio (CHANNEL-2) - written
in the same transaction. Postgres `now()` is transaction-scoped, so both
rows can land on the identical `created_at`, making
`ORDER BY created_at DESC LIMIT 1` an undefined tie. Only the text row
carries `meta` (send_audio never does), so if the tie ever resolves to the
audio row, a caregiver mid multi-turn flow via voice would silently lose
their pending state - confirmed by hand against real Postgres: a plain
seq scan returned the text row, forcing an index scan returned the audio
row for the exact same query.

Fixed by excluding `kind == 'audio'` in get_last_outbound_message() -
"the last message I sent" means the logical (text) reply, not whichever
row the planner happens to return first among identical timestamps.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db.models.message import Conversation, Message
from app.db.models.user import User
from app.services.conversation import get_last_outbound_message
from tests.conftest import unique_wa_id


@pytest.mark.asyncio
async def test_last_outbound_skips_audio_row_sharing_the_text_rows_timestamp(db_session):
    user = User(wa_id=unique_wa_id("voice"), phone_e164="+6590000099", display_name="Mary")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id)
    db_session.add(conversation)
    await db_session.flush()

    tied_timestamp = datetime.now(UTC)
    pending_meta = {
        "pending_caregiver_flow": {"kind": "appointment", "step": "datetime", "data": {}}
    }

    # Same shape as text.py::handle for a voice reply: send_text (with meta)
    # then send_audio (no meta), same transaction - same created_at here on
    # purpose, to force the tie rather than rely on real clock timing.
    db_session.add(
        Message(
            conversation_id=conversation.id,
            user_id=user.id,
            direction="outbound",
            channel_sid=f"SM{uuid.uuid4().hex}",
            kind="text",
            body="When is the appointment?",
            meta=pending_meta,
            created_at=tied_timestamp,
        )
    )
    db_session.add(
        Message(
            conversation_id=conversation.id,
            user_id=user.id,
            direction="outbound",
            channel_sid=f"SM{uuid.uuid4().hex}",
            kind="audio",
            body="When is the appointment?",
            meta={},
            created_at=tied_timestamp,
        )
    )
    await db_session.flush()

    result = await get_last_outbound_message(db_session, user.id)

    assert result is not None
    assert result.kind == "text"
    assert result.meta.get("pending_caregiver_flow") == pending_meta["pending_caregiver_flow"]


@pytest.mark.asyncio
async def test_last_outbound_still_returns_most_recent_text_when_not_tied(db_session):
    user = User(wa_id=unique_wa_id("voice"), phone_e164="+6590000098", display_name="Mary")
    db_session.add(user)
    await db_session.flush()
    conversation = Conversation(user_id=user.id)
    db_session.add(conversation)
    await db_session.flush()

    older = datetime.now(UTC)
    newer = older + timedelta(seconds=5)

    db_session.add(
        Message(
            conversation_id=conversation.id,
            user_id=user.id,
            direction="outbound",
            channel_sid=f"SM{uuid.uuid4().hex}",
            kind="text",
            body="first reply",
            meta={},
            created_at=older,
        )
    )
    db_session.add(
        Message(
            conversation_id=conversation.id,
            user_id=user.id,
            direction="outbound",
            channel_sid=f"SM{uuid.uuid4().hex}",
            kind="text",
            body="second reply",
            meta={"pending_emergency_contact_id": "abc"},
            created_at=newer,
        )
    )
    await db_session.flush()

    result = await get_last_outbound_message(db_session, user.id)

    assert result is not None
    assert result.body == "second reply"
