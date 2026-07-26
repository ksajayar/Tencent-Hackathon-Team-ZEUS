import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import outbound
from app.core.logging import get_logger
from app.db.models.calendar import CalendarEvent
from app.db.models.google import OAuthToken
from app.db.models.user import User
from app.google.tokens import build_credentials, refresh_token_row
from app.i18n.strings import RESCHEDULED_EVENT
from app.services import calendar as calendar_service
from app.services import conversation as conversation_service

logger = get_logger(__name__)

# §04 §4.3: timeMin=now-1d, timeMax=now+14d.
SYNC_WINDOW_PAST = timedelta(days=1)
SYNC_WINDOW_FUTURE = timedelta(days=14)


def _content_hash(
    *, summary: str, start_at: datetime, end_at: datetime, location: str | None
) -> str:
    payload = json.dumps(
        {
            "summary": summary,
            "start": start_at.isoformat(),
            "end": end_at.isoformat(),
            "location": location,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _parse_when(value: dict) -> tuple[datetime, bool]:
    """Google returns all-day events as a bare {'date': 'YYYY-MM-DD'} and timed
    events as {'dateTime': RFC3339, 'timeZone': ...} - all-day needs an explicit
    branch or it renders as midnight (§04 §4.3)."""
    if "date" in value:
        naive = datetime.fromisoformat(value["date"])
        return naive.replace(tzinfo=UTC), True
    dt = datetime.fromisoformat(value["dateTime"])
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC), False


def _list_events_sync(credentials: Credentials, *, time_min: str, time_max: str) -> list[dict]:
    service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    events: list[dict] = []
    page_token = None
    while True:
        response = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,  # expands recurring events into instances (§04 §4.3)
                orderBy="startTime",
                maxResults=250,
                pageToken=page_token,
            )
            .execute()
        )
        events.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return events


async def _fetch_events(
    token_row: OAuthToken, session: AsyncSession
) -> tuple[list[dict] | None, str | None]:
    """Returns (None, error) - not ([], None) - on unrecoverable failure, so the
    caller can tell 'no events' apart from 'sync failed', and so the failure
    reason is available to callers without digging through logs."""
    credentials = build_credentials(token_row)
    now = datetime.now(UTC)
    time_min = (now - SYNC_WINDOW_PAST).isoformat()
    time_max = (now + SYNC_WINDOW_FUTURE).isoformat()

    try:
        items = await asyncio.to_thread(
            _list_events_sync, credentials, time_min=time_min, time_max=time_max
        )
        return items, None
    except HttpError as exc:
        if exc.resp.status != 401:
            error = f"HttpError {exc.resp.status}: {str(exc)[:300]}"
            logger.error(
                "calendar_sync_http_error", user_id=str(token_row.user_id), status=exc.resp.status
            )
            return None, error
    except Exception as exc:
        error = f"{exc.__class__.__name__}: {str(exc)[:300]}"
        logger.exception("calendar_sync_failed", user_id=str(token_row.user_id))
        return None, error

    # Lazy refresh on 401, once, then give up (§04 refresh table).
    if not await refresh_token_row(session, token_row):
        return None, "token_refresh_failed"
    credentials = build_credentials(token_row)
    try:
        items = await asyncio.to_thread(
            _list_events_sync, credentials, time_min=time_min, time_max=time_max
        )
        return items, None
    except Exception as exc:
        error = f"{exc.__class__.__name__} after refresh: {str(exc)[:300]}"
        logger.error("calendar_sync_failed_after_refresh", user_id=str(token_row.user_id))
        return None, error


async def _notify_reschedule(session: AsyncSession, user: User, event: CalendarEvent) -> None:
    conversation = await conversation_service.get_or_create_open_conversation(session, user)
    when = calendar_service.render_when(
        event.start_at,
        is_all_day=event.is_all_day,
        tz_name=user.timezone,
        language=user.preferred_language,
    )
    template = RESCHEDULED_EVENT.get(user.preferred_language, RESCHEDULED_EVENT["en"])
    body = template.format(summary=event.summary or "Untitled event", when=when)
    await outbound.send_text(session, user, conversation.id, body)


async def sync_user_calendar(
    session: AsyncSession, token_row: OAuthToken
) -> tuple[int, str | None]:
    """Fetches primary-calendar events for one user and upserts calendar_events
    on (user_id, google_event_id). Returns (changed_count, error). Never raises -
    failures are logged and returned as an error string, matching
    refresh_token_row's degrade-not-crash contract."""
    user = await session.get(User, token_row.user_id)
    if user is None:
        return 0, "user_not_found"

    items, error = await _fetch_events(token_row, session)
    if items is None:
        return 0, error

    result = await session.execute(
        select(CalendarEvent).where(CalendarEvent.user_id == token_row.user_id)
    )
    existing = {row.google_event_id: row for row in result.scalars().all()}

    now = datetime.now(UTC)
    changed = 0
    reschedules: list[CalendarEvent] = []

    for item in items:
        google_event_id = item.get("id")
        if not google_event_id or item.get("status") == "cancelled":
            continue

        start_at, is_all_day = _parse_when(item.get("start", {}))
        end_at, _ = _parse_when(item.get("end", item.get("start", {})))
        summary = item.get("summary") or "Untitled event"
        location = item.get("location")
        content_hash = _content_hash(
            summary=summary, start_at=start_at, end_at=end_at, location=location
        )
        attendees = [
            {"email": a.get("email"), "display_name": a.get("displayName")}
            for a in item.get("attendees", [])
        ] or None

        row = existing.get(google_event_id)
        if row is None:
            row = CalendarEvent(
                user_id=token_row.user_id,
                google_event_id=google_event_id,
                summary=summary,
                description=item.get("description"),
                location=location,
                start_at=start_at,
                end_at=end_at,
                is_all_day=is_all_day,
                attendees=attendees,
                content_hash=content_hash,
                synced_at=now,
            )
            session.add(row)
            changed += 1
        elif row.content_hash != content_hash:
            previous_start = row.start_at
            row.summary = summary
            row.description = item.get("description")
            row.location = location
            row.start_at = start_at
            row.end_at = end_at
            row.is_all_day = is_all_day
            row.attendees = attendees
            row.content_hash = content_hash
            row.synced_at = now
            changed += 1
            if previous_start != start_at:
                reschedules.append(row)
        else:
            row.synced_at = now

    await session.flush()

    for row in reschedules:
        await _notify_reschedule(session, user, row)

    today = datetime.now(ZoneInfo(user.timezone)).date()
    window_events = await calendar_service.get_schedule_window(
        session, token_row.user_id, tz_name=user.timezone
    )
    today_events = [
        e for e in window_events if e.start_at.astimezone(ZoneInfo(user.timezone)).date() == today
    ]
    for a, b in calendar_service.find_conflicts(today_events):
        logger.warning(
            "calendar_conflict_detected",
            user_id=str(token_row.user_id),
            event_a_id=str(a.id),
            event_b_id=str(b.id),
        )

    return changed, None
