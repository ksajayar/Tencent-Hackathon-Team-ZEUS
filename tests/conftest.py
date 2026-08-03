"""Test configuration.

The one piece of real machinery this replaces is the database engine's
connection pool, and it has to happen here, at conftest import time, before
any test module imports a pipeline.

Why: `app/db/session.py` builds its engine once at import with a normal
pooled engine, and pipeline modules bind the sessionmaker eagerly
(`from app.db.session import async_session`). A pooled asyncpg connection
is bound to the event loop that opened it, and pytest-asyncio gives each
test function a fresh loop - so the second test to run inherits a
connection whose loop is already closed and dies with "Event loop is
closed" / "got result for unknown protocol state". NullPool opens and
closes a connection per checkout, keeping every connection inside the loop
that created it.

This changes pooling only. Every query, transaction and commit under test
is the real thing.

The second piece here, `stub_twilio_send`, is `autouse=True` for the same
reason: it must be impossible for a test file to forget it. Without it,
any test that drives a pipeline through to an actual outbound send (any
`text_pipeline.handle()` / `contact_pipeline.handle()` call) makes a REAL
network call to Twilio's API with whatever dummy credentials are in `.env`
- which doesn't fail fast, it hangs on connect/retry for minutes per call
(found this session: a test file that predated this fixture existing here
ran real Twilio calls for 11 minutes, ~600s of CPU, before being killed).
Central and autouse so no future test file can reintroduce that by simply
not knowing to import a per-file stub.
"""

import uuid

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.db.session as _db_session_module
from app.channels import twilio_whatsapp
from app.core.config import settings

_test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
_test_sessionmaker = async_sessionmaker(_test_engine, expire_on_commit=False)

_db_session_module.engine = _test_engine
_db_session_module.async_session = _test_sessionmaker


@pytest_asyncio.fixture
async def db_session():
    """Real Postgres session (docs/11.3: integration tests run against a real
    DB, not a mock). Nothing here calls commit(), so closing without
    committing discards every change and no manual cleanup is needed."""
    async with _test_sessionmaker() as session:
        yield session


@pytest_asyncio.fixture(autouse=True)
def stub_twilio_send(monkeypatch):
    """Stub the one method that actually puts bytes on the wire to Twilio.
    Every other call in outbound.py - the window check, the throttle,
    persisting the `messages` row and its `meta` - runs for real, since
    that's what carries the multi-turn flow state most of these tests
    exist to verify. Returns the list of (to, body) pairs actually "sent",
    for tests that need to assert who received what."""
    sent: list[tuple[str, str]] = []

    async def _fake_send_text(to: str, body: str) -> str:
        sent.append((to, body))
        return f"SM{uuid.uuid4().hex}"

    monkeypatch.setattr(twilio_whatsapp.provider, "send_text", _fake_send_text)
    return sent


def unique_wa_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"
