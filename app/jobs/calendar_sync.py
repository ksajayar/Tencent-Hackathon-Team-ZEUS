from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models.google import OAuthToken
from app.db.session import async_session
from app.google.calendar import sync_user_calendar

logger = get_logger(__name__)


async def sync_all_calendars() -> None:
    """Every 15 minutes (§04 §4.3): re-list each connected user's primary
    calendar and upsert calendar_events."""
    async with async_session() as session:
        result = await session.execute(select(OAuthToken).where(OAuthToken.provider == "google"))
        rows = list(result.scalars().all())

        if not rows:
            logger.info("calendar_sync_job_run", user_count=0, changed=0)
            return

        total_changed = 0
        for row in rows:
            total_changed += await sync_user_calendar(session, row)

        await session.commit()
        logger.info("calendar_sync_job_run", user_count=len(rows), changed=total_changed)
