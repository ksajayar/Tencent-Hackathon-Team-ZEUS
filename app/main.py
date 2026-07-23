from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.webhooks import router as webhooks_router
from app.core.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("app_startup")
    yield
    logger.info("app_shutdown")


app = FastAPI(title="Dementia Assistant", lifespan=lifespan)
app.include_router(health_router)
app.include_router(webhooks_router)
