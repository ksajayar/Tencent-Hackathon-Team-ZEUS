from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# SQLAlchemyJobStore on Postgres so jobs survive a redeploy. Single replica is
# mandatory (Dockerfile CMD already pins --workers 1) - two instances would
# double-fire every job.
scheduler = AsyncIOScheduler(
    jobstores={"default": SQLAlchemyJobStore(url=settings.sync_database_url)},
    executors={"default": AsyncIOExecutor()},
    job_defaults={
        "coalesce": True,  # after downtime, fire once, not N times
        "max_instances": 1,
        "misfire_grace_time": 300,  # a deploy during a job window shouldn't drop it
    },
    timezone="UTC",
)


def start() -> None:
    from app.jobs.ack_watchdog import check_unacked_reminders
    from app.jobs.calendar_sync import sync_all_calendars
    from app.jobs.medication_sync import sync_all_medication_reminders
    from app.jobs.outbound_flush import flush_outbound_queue
    from app.jobs.reminders import fire_due_reminders
    from app.jobs.retry_failed import retry_failed_sends
    from app.jobs.token_refresh import refresh_google_tokens

    scheduler.add_job(
        refresh_google_tokens,
        "interval",
        minutes=10,
        id="refresh_google_tokens",
        replace_existing=True,
    )
    scheduler.add_job(
        sync_all_calendars,
        "interval",
        minutes=15,
        id="sync_calendar",
        replace_existing=True,
    )
    scheduler.add_job(
        sync_all_medication_reminders,
        "interval",
        minutes=10,
        id="sync_medication_reminders",
        replace_existing=True,
    )
    scheduler.add_job(
        fire_due_reminders,
        "interval",
        seconds=60,
        id="fire_due_reminders",
        replace_existing=True,
    )
    scheduler.add_job(
        flush_outbound_queue,
        "interval",
        seconds=5,
        id="flush_outbound_queue",
        replace_existing=True,
    )
    scheduler.add_job(
        retry_failed_sends,
        "interval",
        minutes=5,
        id="retry_failed_sends",
        replace_existing=True,
    )
    scheduler.add_job(
        check_unacked_reminders,
        "interval",
        minutes=10,
        id="ack_watchdog",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("scheduler_started")


def shutdown() -> None:
    scheduler.shutdown(wait=False)
    logger.info("scheduler_stopped")
