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
    from app.jobs.token_refresh import refresh_google_tokens

    scheduler.add_job(
        refresh_google_tokens,
        "interval",
        minutes=10,
        id="refresh_google_tokens",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("scheduler_started")


def shutdown() -> None:
    scheduler.shutdown(wait=False)
    logger.info("scheduler_stopped")
