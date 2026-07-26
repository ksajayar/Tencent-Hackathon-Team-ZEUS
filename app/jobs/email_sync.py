from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models.google import OAuthToken
from app.db.session import async_session
from app.google.gmail import sync_user_gmail

logger = get_logger(__name__)


async def sync_all_gmail() -> list[dict]:
    """Every 15 minutes (§04 §4.2): classify only newly-seen messages per
    connected account. Returns a per-user result list so the internal
    trigger endpoint can surface errors without a log lookup."""
    async with async_session() as session:
        result = await session.execute(select(OAuthToken).where(OAuthToken.provider == "google"))
        rows = list(result.scalars().all())

        if not rows:
            logger.info("gmail_sync_job_run", user_count=0, new_count=0)
            return []

        total_new = 0
        results = []
        for row in rows:
            new_count, error = await sync_user_gmail(session, row)
            total_new += new_count
            results.append({"user_id": str(row.user_id), "new": new_count, "error": error})

        await session.commit()
        logger.info("gmail_sync_job_run", user_count=len(rows), new_count=total_new)
        return results
