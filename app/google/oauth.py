import secrets
import uuid
from datetime import UTC, datetime, timedelta

from google_auth_oauthlib.flow import Flow
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.google import OAuthState

STATE_TTL_MINUTES = 10

# Read-only, minimum viable (§02, §04). No code path in this system can send,
# delete, or modify anything in a Google account.
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def _build_flow(*, state: str | None = None) -> Flow:
    client_config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [settings.google_redirect_uri],
        }
    }
    return Flow.from_client_config(
        client_config, scopes=SCOPES, state=state, redirect_uri=settings.google_redirect_uri
    )


def generate_state() -> str:
    return secrets.token_urlsafe(32)


def build_authorization_url(state: str) -> str:
    flow = _build_flow(state=state)
    url, _ = flow.authorization_url(
        access_type="offline",  # required, or no refresh token
        prompt="consent",  # required, or Google skips the refresh token on re-auth
        include_granted_scopes="true",
        state=state,
    )
    return url


def exchange_code(*, code: str, state: str):
    """Synchronous - google-auth-oauthlib uses `requests` under the hood.
    Callers must wrap this in asyncio.to_thread() (§02: never block the
    event loop on a sync SDK call)."""
    flow = _build_flow(state=state)
    flow.fetch_token(code=code)
    return flow.credentials


async def create_connect_link(session: AsyncSession, user_id: uuid.UUID) -> str:
    """Single-use state (10 min TTL, bound to user_id) + a short link to our
    own /oauth/google/start, which redirects to Google. Keeps the WhatsApp
    message short rather than embedding Google's full auth URL."""
    state = generate_state()
    session.add(
        OAuthState(
            state=state,
            user_id=user_id,
            expires_at=datetime.now(UTC) + timedelta(minutes=STATE_TTL_MINUTES),
        )
    )
    await session.flush()
    return f"{settings.public_base_url.rstrip('/')}/oauth/google/start?state={state}"
