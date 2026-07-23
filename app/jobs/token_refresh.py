from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models.google import OAuthToken
from app.db.session import async_session
from app.google.tokens import refresh_token_row

logger = get_logger(__name__)

REFRESH_WINDOW = timedelta(minutes=5)


async def refresh_google_tokens() -> None:
    """Every 10 minutes (§04): refresh anything expiring within 5 minutes."""
    async with async_session() as session:
        cutoff = datetime.now(UTC) + REFRESH_WINDOW
        result = await session.execute(select(OAuthToken).where(OAuthToken.expires_at < cutoff))
        rows = list(result.scalars().all())

        if not rows:
            return

        refreshed = 0
        failed = 0
        for row in rows:
            ok = await refresh_token_row(session, row)
            if ok:
                refreshed += 1
            else:
                failed += 1

        await session.commit()
        logger.info(
            "token_refresh_job_run", due_count=len(rows), refreshed=refreshed, failed=failed
        )
