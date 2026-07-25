import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.twilio_whatsapp import provider as twilio_provider
from app.core.config import settings
from app.core.deps import get_db
from app.core.security import require_admin_token
from app.db.models.calendar import CalendarEvent
from app.db.models.google import OAuthToken
from app.db.models.medication import Medication
from app.db.models.outbound_queue import OutboundQueueEntry
from app.db.models.reminder import Reminder, ReminderAck
from app.jobs.calendar_sync import sync_all_calendars
from app.jobs.reminders import fire_one
from app.services.seed import seed_demo_data

router = APIRouter(prefix="/internal")


@router.post("/sync/calendar", dependencies=[Depends(require_admin_token)])
async def trigger_calendar_sync() -> dict:
    """Force a Calendar sync now (§09) - lets you test M4 without waiting on
    the 15-minute job or the demo Google account's real event timing."""
    results = await sync_all_calendars()
    return {"status": "ok", "results": results}


@router.post("/seed", dependencies=[Depends(require_admin_token)])
async def trigger_seed(session: AsyncSession = Depends(get_db)) -> dict:
    """Re-seed demo data (§09) - idempotent, safe to call repeatedly."""
    result = await seed_demo_data(session)
    await session.commit()
    return {"status": "ok", **result}


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
