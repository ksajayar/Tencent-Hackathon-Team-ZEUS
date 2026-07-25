from app.core.logging import get_logger
from app.db.session import async_session
from app.services.medications import sync_medication_reminders

logger = get_logger(__name__)


async def sync_all_medication_reminders() -> None:
    """Every 10 minutes: reconcile reminders from medications so a newly
    seeded/verified/deactivated medication is picked up without a manual
    trigger."""
    async with async_session() as session:
        touched = await sync_medication_reminders(session)
        await session.commit()
        if touched:
            logger.info("medication_reminder_sync_run", touched=touched)
