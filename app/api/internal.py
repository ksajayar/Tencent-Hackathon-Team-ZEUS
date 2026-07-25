from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.security import require_admin_token
from app.db.models.calendar import CalendarEvent
from app.db.models.google import OAuthToken
from app.jobs.calendar_sync import sync_all_calendars

router = APIRouter(prefix="/internal")


@router.post("/sync/calendar", dependencies=[Depends(require_admin_token)])
async def trigger_calendar_sync() -> dict:
    """Force a Calendar sync now (§09) - lets you test M4 without waiting on
    the 15-minute job or the demo Google account's real event timing."""
    results = await sync_all_calendars()
    return {"status": "ok", "results": results}


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
