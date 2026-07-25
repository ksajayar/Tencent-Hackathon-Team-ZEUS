from app.channels import outbound
from app.core.logging import get_logger
from app.db.session import async_session

logger = get_logger(__name__)


async def flush_outbound_queue() -> None:
    """Every 5s (§09): send anything parked whose window has reopened."""
    async with async_session() as session:
        sent = await outbound.flush_awaiting_window(session)
        await session.commit()
        if sent:
            logger.info("outbound_queue_flushed", sent=sent)
