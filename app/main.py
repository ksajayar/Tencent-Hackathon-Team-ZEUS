from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.internal import router as internal_router
from app.api.oauth import router as oauth_router
from app.api.webhooks import router as webhooks_router
from app.core.logging import configure_logging, get_logger
from app.jobs import scheduler

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("app_startup")
    scheduler.start()
    yield
    scheduler.shutdown()
    logger.info("app_shutdown")


app = FastAPI(title="Dementia Assistant", lifespan=lifespan)
app.include_router(health_router)
app.include_router(webhooks_router)
app.include_router(oauth_router)
app.include_router(internal_router)
