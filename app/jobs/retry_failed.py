from app.channels import outbound
from app.core.logging import get_logger
from app.db.session import async_session

logger = get_logger(__name__)


async def retry_failed_sends() -> None:
    """Every 5 minutes (§09): give up-to-budget failed sends another chance."""
    async with async_session() as session:
        requeued = await outbound.retry_failed_sends(session)
        await session.commit()
        if requeued:
            logger.info("outbound_retry_requeued", requeued=requeued)
