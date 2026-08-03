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

`stub_medication_schedule_parse` is autouse for the identical reason, on
the identical failure mode - a test driving `set medication` (any
`_caregiver_says(..., "twice a day, before meals")`-shaped turn) without
stubbing gemini_client.parse_medication_schedule() would make a real
Gemini call with the dummy .env key. Defaults to a fixed once-a-day-morning
schedule regardless of input text; tests needing a specific schedule or
the "unparseable" re-prompt path call the returned setter to override it.
"""

import sys
import types
import uuid

# MUST come before any `app.` import that reaches app/channels/media.py.
#
# `python-magic` is a ctypes binding to the native libmagic library, which
# exists in the container (the Dockerfile apt-installs libmagic1) but not on
# a bare Windows dev box. On Windows its import doesn't fail - it HANGS
# indefinitely searching for the DLL (confirmed by faulthandler: stuck in
# magic/compat.py at import). Since app/pipelines/contact.py imports
# app/channels/media.py, that made every test module touching the contact
# pipeline uncollectable on Windows, with no error - just a hung pytest.
#
# Only `magic.from_buffer` is used (media.py:41, to sniff a downloaded
# file's MIME type), and no test here exercises real media downloads, so a
# stub is enough.
#
# Gated on platform rather than a try/except around the import: the failure
# mode is a HANG, not an exception, so try/except would never get the
# chance to catch anything. On Linux (CI, the container) libmagic is really
# present, so this is skipped and the real package is used untouched.
if sys.platform == "win32" and "magic" not in sys.modules:
    _magic_stub = types.ModuleType("magic")
    _magic_stub.from_buffer = lambda content, mime=False: "application/octet-stream"
    sys.modules["magic"] = _magic_stub

import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

import app.db.session as _db_session_module  # noqa: E402
from app.ai import gemini_client  # noqa: E402
from app.channels import twilio_whatsapp  # noqa: E402
from app.core.config import settings  # noqa: E402

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


@pytest_asyncio.fixture(autouse=True)
def stub_medication_schedule_parse(monkeypatch):
    """See module docstring. Default result: once a day, in the morning -
    deliberately a plausible, boring answer regardless of what free text
    was actually typed, since most tests aren't exercising this parse step
    specifically. Call the returned setter to change what the next parse
    call returns (a plain dict for success, or None to simulate an
    unparseable description and exercise the re-prompt path)."""
    result = {
        "rrule": "FREQ=DAILY;BYHOUR=8;BYMINUTE=0",
        "label_en": "once a day, in the morning",
        "label_zh": "每天一次，早上",
    }

    async def _fake_parse(text: str, *, pipeline: str, model=None, user_id=None):
        return result

    monkeypatch.setattr(gemini_client, "parse_medication_schedule", _fake_parse)

    def _set_result(new_result: dict | None) -> None:
        nonlocal result
        result = new_result

    return _set_result


def unique_wa_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"
