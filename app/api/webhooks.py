from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.inbound import parse_inbound
from app.channels.twilio_whatsapp import provider
from app.core.config import settings
from app.core.deps import get_db
from app.core.logging import get_logger
from app.db.models.message import Message
from app.pipelines.router import route_inbound
from app.services import conversation as conversation_service
from app.services import demo as demo_service

router = APIRouter()
logger = get_logger(__name__)


def _empty_twiml() -> Response:
    return Response(content="<Response/>", media_type="text/xml")


def _webhook_url(path: str) -> str:
    # Railway terminates TLS at the edge and reports http internally, so the
    # signed URL must come from PUBLIC_BASE_URL, never request.url (§10.1).
    return f"{settings.public_base_url.rstrip('/')}{path}"


@router.post("/webhooks/twilio")
async def twilio_inbound(
    request: Request,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),
):
    form = dict((await request.form()).multi_items())
    signature = request.headers.get("X-Twilio-Signature", "")
    url = _webhook_url("/webhooks/twilio")

    if not provider.validate_signature(url, form, signature):
        logger.warning("twilio_signature_invalid")
        return Response(status_code=403)

    try:
        inbound = parse_inbound(form)

        user, is_new_user = await conversation_service.get_or_create_user(
            session,
            wa_id=inbound.wa_id,
            phone_e164=inbound.phone_e164,
            display_name=inbound.display_name,
        )
        # DEMO_MODE requirement 3: an unknown number gets auto-provisioned and
        # the seed template cloned onto it, no caregiver step required. Pure
        # DB writes here (no network/AI call), so this stays inside
        # WEBHOOK-1's fast-ack budget; the welcome + Google-connect-link
        # sends happen afterwards, in the background task (router.py).
        is_demo_patient = settings.demo_mode and user.role == "patient"
        is_demo_new_patient = is_demo_patient and is_new_user
        # A number that was already in `users` when DEMO_MODE was switched on
        # is not `is_new_user`, so it never got a clone and answers every
        # record-backed question with "nothing on file" - the state every
        # number used to test before the flag went on is now in. Backfilled
        # here on their next message rather than by deleting and re-creating
        # the user. Deliberately does NOT set is_demo_new_patient: the
        # welcome + Google-link sends are for a genuinely new number, and
        # replaying them mid-conversation (possibly after that patient has
        # already connected Google) would read as the bot resetting itself.
        needs_backfill = (
            is_demo_patient
            and not is_new_user
            and await demo_service.is_unprovisioned_demo_patient(session, user)
        )
        if is_demo_new_patient or needs_backfill:
            await demo_service.clone_template_for_patient(session, user)

        conversation = await conversation_service.get_or_create_open_conversation(session, user)
        message = await conversation_service.record_inbound_message(
            session, user=user, conversation=conversation, inbound=inbound
        )
        await session.commit()
    except IntegrityError:
        # Duplicate MessageSid: Twilio retried a message we already have.
        # That's idempotency working, not an error (§10.3).
        await session.rollback()
        logger.debug("twilio_duplicate_message", message_sid=form.get("MessageSid"))
        return _empty_twiml()
    except Exception:
        await session.rollback()
        logger.exception("twilio_inbound_processing_failed")
        return _empty_twiml()

    background_tasks.add_task(
        route_inbound,
        user_id=user.id,
        conversation_id=conversation.id,
        message_id=message.id,
        inbound=inbound,
        is_demo_new_patient=is_demo_new_patient,
    )

    return _empty_twiml()


@router.post("/webhooks/twilio/status")
async def twilio_status(request: Request, session: AsyncSession = Depends(get_db)):
    form = dict((await request.form()).multi_items())
    signature = request.headers.get("X-Twilio-Signature", "")
    url = _webhook_url("/webhooks/twilio/status")

    if not provider.validate_signature(url, form, signature):
        logger.warning("twilio_status_signature_invalid")
        return Response(status_code=403)

    message_sid = form.get("MessageSid")
    message_status = form.get("MessageStatus")
    error_code = form.get("ErrorCode")

    if message_sid:
        result = await session.execute(select(Message).where(Message.channel_sid == message_sid))
        message = result.scalar_one_or_none()
        if message is not None:
            message.status = message_status or message.status
            message.error_code = error_code
            await session.commit()

    logger.info("twilio_status_callback", message_sid=message_sid, status=message_status)
    return Response(status_code=200)
