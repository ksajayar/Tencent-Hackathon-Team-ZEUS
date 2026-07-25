from fastapi import APIRouter, Depends

from app.core.security import require_admin_token
from app.jobs.calendar_sync import sync_all_calendars

router = APIRouter(prefix="/internal")


@router.post("/sync/calendar", dependencies=[Depends(require_admin_token)])
async def trigger_calendar_sync() -> dict:
    """Force a Calendar sync now (§09) - lets you test M4 without waiting on
    the 15-minute job or the demo Google account's real event timing."""
    await sync_all_calendars()
    return {"status": "ok"}
