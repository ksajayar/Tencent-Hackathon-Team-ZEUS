from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.models.reminder import Reminder
from app.db.session import async_session
from app.services.medications import find_unacked_reminder

logger = get_logger(__name__)

UNACK_THRESHOLD = timedelta(minutes=30)


async def check_unacked_reminders() -> None:
    """Every 10 minutes (§09): medication reminders unacked >30 min get a
    structured, alertable log line. No caregiver-notification channel exists
    yet (no caregiver_links table or verified caregiver contact), so logging
    is the 'optional caregiver notification' this milestone implements."""
    async with async_session() as session:
        result = await session.execute(
            select(Reminder.user_id)
            .where(Reminder.kind == "medication", Reminder.active.is_(True))
            .distinct()
        )
        user_ids = [row[0] for row in result.all()]
        if not user_ids:
            return

        cutoff = datetime.now(UTC) - UNACK_THRESHOLD
        flagged = 0
        for user_id in user_ids:
            unacked = await find_unacked_reminder(session, user_id)
            if unacked is not None and unacked.last_fired_at <= cutoff:
                flagged += 1
                logger.warning(
                    "medication_reminder_unacked",
                    reminder_id=str(unacked.id),
                    user_id=str(user_id),
                    last_fired_at=unacked.last_fired_at.isoformat(),
                )
        if flagged:
            logger.info("ack_watchdog_run", checked=len(user_ids), flagged=flagged)
