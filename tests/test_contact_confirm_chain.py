"""§11 priority test 6 - the two-step contact confirmation chain
(app/pipelines/text.py::_confirm_contact_question).

Q1 ("call them in an emergency?", asked by pipelines/contact.py) -> yes ->
Q2 ("caregiver as well?", asked by this function) -> yes -> promote to
caregiver. Both "no" branches must leave the *other* answer untouched: a
no to Q2 must not undo the emergency-contact grant from Q1, and a no to Q1
must never even ask Q2.

Needs a real Postgres session - meta lives on `messages.meta` and is read
back via the most-recently-created outbound message for the user, which is
a real query (app/services/conversation.py::get_last_outbound_message).
"""

import itertools
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db.models.contact import Contact
from app.db.models.message import Conversation, Message
from app.db.models.user import User
from app.i18n.strings import (
    CONTACT_ASK_CAREGIVER,
    CONTACT_CAREGIVER_NO,
    CONTACT_CAREGIVER_YES,
    CONTACT_EMERGENCY_NO,
)
from app.pipelines.text import _confirm_contact_question
from tests.conftest import unique_wa_id


async def _make_user(session) -> User:
    user = User(wa_id=unique_wa_id("patient"), phone_e164="+6590000001", display_name="Mary")
    session.add(user)
    await session.flush()
    return user


async def _make_contact(session, user: User, *, name: str = "Alice") -> Contact:
    contact = Contact(user_id=user.id, display_name=name, phone_e164="+6590000002", source="vcard")
    session.add(contact)
    await session.flush()
    return contact


_turn_counter = itertools.count()


async def _outbound_with_meta(session, user: User, meta: dict) -> Message:
    """Mirrors what app/channels/outbound.py::send_text persists - the
    caller under test only needs the meta shape on the latest outbound row,
    not a real Twilio send.

    `created_at` is set explicitly and made strictly increasing rather than
    left to the column's `server_default=now()`. Postgres `now()` is
    transaction-scoped, so two rows written in one test transaction get an
    identical timestamp, and `get_last_outbound_message`'s
    `ORDER BY created_at DESC LIMIT 1` becomes an undefined tie - which made
    Q2 of the chain resolve against Q1's stale meta. In production each turn
    is its own transaction (one inbound message -> one commit), so the
    timestamps genuinely differ; setting them explicitly here models that
    rather than papering over it.
    """
    conversation = Conversation(user_id=user.id)
    session.add(conversation)
    await session.flush()
    message = Message(
        conversation_id=conversation.id,
        user_id=user.id,
        direction="outbound",
        channel_sid=f"SM{uuid.uuid4().hex}",
        kind="text",
        body="...",
        meta=meta,
        created_at=datetime.now(UTC) + timedelta(seconds=next(_turn_counter)),
    )
    session.add(message)
    await session.flush()
    return message


@pytest.mark.asyncio
async def test_q1_yes_then_q2_yes_promotes_to_caregiver(db_session):
    user = await _make_user(db_session)
    contact = await _make_contact(db_session, user)
    await _outbound_with_meta(db_session, user, {"pending_emergency_contact_id": str(contact.id)})

    q1_reply = await _confirm_contact_question(db_session, user, "yes", "en")
    assert q1_reply is not None
    assert q1_reply.text == CONTACT_ASK_CAREGIVER["en"].format(name="Alice")
    assert contact.is_emergency is True
    assert contact.priority == 1
    assert q1_reply.meta == {"pending_caregiver_contact_id": str(contact.id)}

    await _outbound_with_meta(db_session, user, q1_reply.meta)

    q2_reply = await _confirm_contact_question(db_session, user, "yes", "en")
    assert q2_reply is not None
    assert q2_reply.text == CONTACT_CAREGIVER_YES["en"].format(name="Alice")
    assert contact.relationship == "caregiver"


@pytest.mark.asyncio
async def test_q1_no_declines_emergency_and_never_reaches_q2(db_session):
    user = await _make_user(db_session)
    contact = await _make_contact(db_session, user)
    await _outbound_with_meta(db_session, user, {"pending_emergency_contact_id": str(contact.id)})

    reply = await _confirm_contact_question(db_session, user, "no", "en")
    assert reply is not None
    assert reply.text == CONTACT_EMERGENCY_NO["en"].format(name="Alice")
    assert contact.is_emergency is False
    assert contact.relationship is None
    # A "no" to Q1 must not itself ask Q2.
    assert reply.meta is None


@pytest.mark.asyncio
async def test_q2_no_keeps_the_q1_grant_intact(db_session):
    user = await _make_user(db_session)
    contact = await _make_contact(db_session, user)
    await _outbound_with_meta(db_session, user, {"pending_emergency_contact_id": str(contact.id)})

    q1_reply = await _confirm_contact_question(db_session, user, "yes", "en")
    await _outbound_with_meta(db_session, user, q1_reply.meta)

    q2_reply = await _confirm_contact_question(db_session, user, "no", "en")
    assert q2_reply is not None
    assert q2_reply.text == CONTACT_CAREGIVER_NO["en"].format(name="Alice")
    # The "no" to Q2 must not undo the Q1 emergency-contact grant.
    assert contact.is_emergency is True
    assert contact.relationship is None


@pytest.mark.asyncio
async def test_returns_none_when_nothing_pending(db_session):
    user = await _make_user(db_session)
    await _outbound_with_meta(db_session, user, {})

    reply = await _confirm_contact_question(db_session, user, "yes", "en")
    assert reply is None


@pytest.mark.asyncio
async def test_returns_none_for_text_that_is_neither_yes_nor_no(db_session):
    user = await _make_user(db_session)
    contact = await _make_contact(db_session, user)
    await _outbound_with_meta(db_session, user, {"pending_emergency_contact_id": str(contact.id)})

    reply = await _confirm_contact_question(db_session, user, "maybe later", "en")
    assert reply is None


@pytest.mark.asyncio
async def test_chinese_language_replies(db_session):
    user = await _make_user(db_session)
    contact = await _make_contact(db_session, user)
    await _outbound_with_meta(db_session, user, {"pending_emergency_contact_id": str(contact.id)})

    reply = await _confirm_contact_question(db_session, user, "是的", "zh-Hans")
    assert reply is not None
    assert reply.text == CONTACT_ASK_CAREGIVER["zh-Hans"].format(name="Alice")
