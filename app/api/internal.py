import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.outbound import WINDOW_HOURS
from app.channels.twilio_whatsapp import provider as twilio_provider
from app.core.config import settings
from app.core.deps import get_db
from app.core.security import require_admin_token
from app.db.models.calendar import CalendarEvent
from app.db.models.email import EmailCache
from app.db.models.google import OAuthToken
from app.db.models.media import MediaFile, Transcript
from app.db.models.medication import Medication
from app.db.models.message import Message
from app.db.models.outbound_queue import OutboundQueueEntry
from app.db.models.reminder import Reminder, ReminderAck
from app.db.models.user import User
from app.jobs.calendar_sync import sync_all_calendars
from app.jobs.email_sync import sync_all_gmail
from app.jobs.reminders import fire_one
from app.services.seed import seed_demo_data
from app.speech import tts

router = APIRouter(prefix="/internal")


@router.post("/sync/calendar", dependencies=[Depends(require_admin_token)])
async def trigger_calendar_sync() -> dict:
    """Force a Calendar sync now (§09) - lets you test M4 without waiting on
    the 15-minute job or the demo Google account's real event timing."""
    results = await sync_all_calendars()
    return {"status": "ok", "results": results}


@router.post("/sync/gmail", dependencies=[Depends(require_admin_token)])
async def trigger_gmail_sync() -> dict:
    """Force a Gmail sync now (§09) - lets you test M7 without waiting on
    the 15-minute job."""
    results = await sync_all_gmail()
    return {"status": "ok", "results": results}


@router.post("/seed", dependencies=[Depends(require_admin_token)])
async def trigger_seed(session: AsyncSession = Depends(get_db)) -> dict:
    """Re-seed demo data (§09) - idempotent, safe to call repeatedly."""
    result = await seed_demo_data(session)
    await session.commit()
    return {"status": "ok", **result}


@router.post("/tts/preview", dependencies=[Depends(require_admin_token)])
async def trigger_tts_preview(text: str, language: str = "en") -> FileResponse:
    """Synthesise text and return the OGG directly (§09) - verifies the full
    edge-tts + ffmpeg-transcode path in one call, without a real voice note."""
    filename = await tts.synthesize(text, language=language)
    path = Path(settings.tts_cache_root) / filename
    return FileResponse(path, media_type="audio/ogg", filename="preview.ogg")


@router.post("/reminders/fire/{reminder_id}", dependencies=[Depends(require_admin_token)])
async def trigger_reminder_fire(
    reminder_id: uuid.UUID, session: AsyncSession = Depends(get_db)
) -> dict:
    """Fire a reminder immediately (§09) - demo control. Bypasses the
    next_fire_at gate but otherwise behaves exactly like a real firing
    (renders, guards, sends, advances the RRULE)."""
    reminder = await session.get(Reminder, reminder_id)
    if reminder is None:
        raise HTTPException(status_code=404, detail="reminder not found")

    sent = await fire_one(session, reminder)
    await session.commit()
    return {"status": "ok", "sent": sent}


@router.post("/debug/close-window/{user_id}", dependencies=[Depends(require_admin_token)])
async def debug_close_window(user_id: uuid.UUID, session: AsyncSession = Depends(get_db)) -> dict:
    """Test utility only: backdates last_inbound_at so the 24h window (§03
    §3.4) reads as genuinely closed, to verify the template-fallback path
    without waiting a real day. Self-heals the moment the user messages the
    bot again - nothing sent to Twilio is faked, only this local timestamp."""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    user.last_inbound_at = datetime.now(UTC) - timedelta(hours=WINDOW_HOURS + 1)
    await session.commit()
    return {"status": "ok", "last_inbound_at": user.last_inbound_at.isoformat()}


@router.get("/debug/templates", dependencies=[Depends(require_admin_token)])
async def debug_templates() -> dict:
    """Twilio Content API templates on this account, so the sandbox's
    'Appointment reminder' ContentSid can be found without console-hunting.
    Also echoes the currently *running* configured_sid - not a secret (it's
    just a Content template id, already visible in your own Twilio console)
    - so you can confirm a Railway variable change actually redeployed rather
    than trusting the dashboard alone."""
    templates = await twilio_provider.list_content_templates()
    return {
        "templates": templates,
        "configured_sid": settings.twilio_appointment_template_sid,
    }


@router.get("/debug/calendar", dependencies=[Depends(require_admin_token)])
async def debug_calendar(session: AsyncSession = Depends(get_db)) -> dict:
    """Read-only snapshot of oauth_tokens + calendar_events (no token values -
    DATA-1). For diagnosing 'sync ran but nothing showed up' without log-diving."""
    token_rows = (await session.execute(select(OAuthToken))).scalars().all()
    event_rows = (
        (await session.execute(select(CalendarEvent).order_by(CalendarEvent.start_at)))
        .scalars()
        .all()
    )

    return {
        "oauth_tokens": [
            {
                "user_id": str(t.user_id),
                "provider": t.provider,
                "scopes": t.scopes,
                "expires_at": t.expires_at.isoformat() if t.expires_at else None,
                "last_refreshed_at": (
                    t.last_refreshed_at.isoformat() if t.last_refreshed_at else None
                ),
            }
            for t in token_rows
        ],
        "calendar_events": [
            {
                "user_id": str(e.user_id),
                "google_event_id": e.google_event_id,
                "summary": e.summary,
                "start_at": e.start_at.isoformat(),
                "end_at": e.end_at.isoformat(),
                "is_all_day": e.is_all_day,
                "synced_at": e.synced_at.isoformat(),
            }
            for e in event_rows
        ],
    }


@router.get("/debug/reminders", dependencies=[Depends(require_admin_token)])
async def debug_reminders(session: AsyncSession = Depends(get_db)) -> dict:
    """Read-only snapshot of medications/reminders/acks/outbound_queue, for
    diagnosing M5 without log-diving (same reasoning as /debug/calendar)."""
    medications = (await session.execute(select(Medication))).scalars().all()
    reminders = (
        (await session.execute(select(Reminder).order_by(Reminder.next_fire_at))).scalars().all()
    )
    acks = (
        (await session.execute(select(ReminderAck).order_by(ReminderAck.acked_at.desc())))
        .scalars()
        .all()
    )
    queue = (
        (await session.execute(select(OutboundQueueEntry).order_by(OutboundQueueEntry.created_at)))
        .scalars()
        .all()
    )

    return {
        "medications": [
            {
                "id": str(m.id),
                "patient_id": str(m.patient_id),
                "name": m.name,
                "dose_text": m.dose_text,
                "schedule_rrule": m.schedule_rrule,
                "active": m.active,
                "verified_by": str(m.verified_by) if m.verified_by else None,
            }
            for m in medications
        ],
        "reminders": [
            {
                "id": str(r.id),
                "user_id": str(r.user_id),
                "kind": r.kind,
                "source": r.source,
                "title_en": r.title_en,
                "active": r.active,
                "next_fire_at": r.next_fire_at.isoformat() if r.next_fire_at else None,
                "last_fired_at": r.last_fired_at.isoformat() if r.last_fired_at else None,
            }
            for r in reminders
        ],
        "reminder_acks": [
            {
                "reminder_id": str(a.reminder_id),
                "user_id": str(a.user_id),
                "acked_at": a.acked_at.isoformat(),
            }
            for a in acks
        ],
        "outbound_queue": [
            {
                "id": str(q.id),
                "user_id": str(q.user_id),
                "status": q.status,
                "attempts": q.attempts,
                "last_error": q.last_error,
                "scheduled_for": q.scheduled_for.isoformat(),
            }
            for q in queue
        ],
    }


@router.get("/debug/voice", dependencies=[Depends(require_admin_token)])
async def debug_voice(session: AsyncSession = Depends(get_db)) -> dict:
    """Read-only snapshot of media_files/transcripts + recent audio-kind
    messages, for verifying M6 (transcript accuracy, language detection,
    inbound+outbound pairing) without log-diving. Transcript text is
    deliberately included here (unlike structured logs, DATA-2) - it's the
    one thing that actually needs eyeballing to confirm STT worked."""
    media_files = (
        (await session.execute(select(MediaFile).order_by(MediaFile.created_at.desc())))
        .scalars()
        .all()
    )
    transcripts = (
        (await session.execute(select(Transcript).order_by(Transcript.created_at.desc())))
        .scalars()
        .all()
    )
    messages = (
        (
            await session.execute(
                select(Message)
                .where(Message.kind == "audio")
                .order_by(Message.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )

    return {
        "media_files": [
            {
                "id": str(m.id),
                "message_id": str(m.message_id),
                "mime_type": m.mime_type,
                "size_bytes": m.size_bytes,
                "duration_ms": m.duration_ms,
                "sha256": m.sha256,
            }
            for m in media_files
        ],
        "transcripts": [
            {
                "media_id": str(t.media_id),
                "text": t.text_content,
                "language": t.language,
                "confidence": float(t.confidence) if t.confidence is not None else None,
            }
            for t in transcripts
        ],
        "audio_messages": [
            {
                "id": str(msg.id),
                "direction": msg.direction,
                "body": msg.body,
                "detected_language": msg.detected_language,
                "status": msg.status,
                "created_at": msg.created_at.isoformat(),
            }
            for msg in messages
        ],
    }


@router.get("/debug/gmail", dependencies=[Depends(require_admin_token)])
async def debug_gmail(session: AsyncSession = Depends(get_db)) -> dict:
    """Read-only snapshot of email_cache, for verifying M7 (classification,
    priority, the medical-domain floor) without log-diving. Summaries are
    already meant to be surfaced to the patient over WhatsApp, so showing
    them here isn't a new exposure - unlike a voice transcript, this isn't
    borderline (see /debug/voice's note on DATA-2)."""
    emails = (
        (
            await session.execute(
                select(EmailCache).order_by(
                    EmailCache.priority.desc(), EmailCache.received_at.desc()
                )
            )
        )
        .scalars()
        .all()
    )

    return {
        "emails": [
            {
                "id": str(e.id),
                "user_id": str(e.user_id),
                "from_addr": e.from_addr,
                "from_name": e.from_name,
                "subject": e.subject,
                "category": e.category,
                "priority": e.priority,
                "needs_action": e.needs_action,
                "summary_en": e.summary_en,
                "summary_zh": e.summary_zh,
                "received_at": e.received_at.isoformat() if e.received_at else None,
            }
            for e in emails
        ],
    }
