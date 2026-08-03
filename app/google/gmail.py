import asyncio
from datetime import UTC, datetime
from email.utils import parseaddr
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import gemini_client
from app.core.logging import get_logger
from app.db.models.email import EmailCache
from app.db.models.google import OAuthToken
from app.db.models.user import User
from app.google.tokens import build_credentials, refresh_token_row

logger = get_logger(__name__)

# §04 §4.2.
GMAIL_QUERY = "newer_than:3d -category:promotions -category:social"
MAX_RESULTS = 25

# Deterministic pre-filter: promotes anything from a matching sender domain to
# priority>=4 regardless of what the model says, so a model failure or
# misjudgement can't bury a hospital email.
_MEDICAL_DOMAIN_KEYWORDS = ["hospital", "clinic", "polyclinic", "medical", "healthcare"]
_MEDICAL_FLOOR_PRIORITY = 4


def _is_medical_domain(from_addr: str) -> bool:
    if not from_addr or "@" not in from_addr:
        return False
    domain = from_addr.rsplit("@", 1)[-1].lower()
    return any(keyword in domain for keyword in _MEDICAL_DOMAIN_KEYWORDS)


def _list_message_ids_sync(service) -> list[str]:
    response = (
        service.users()
        .messages()
        .list(userId="me", q=GMAIL_QUERY, maxResults=MAX_RESULTS)
        .execute()
    )
    return [item["id"] for item in response.get("messages", [])]


def _get_message_metadata_sync(service, message_id: str) -> dict:
    return (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="metadata", metadataHeaders=["From", "Subject"])
        .execute()
    )


async def _list_message_ids(
    token_row: OAuthToken, session: AsyncSession
) -> tuple[list, str | None]:
    """Returns (service_and_ids, error). On success, the first element is
    (service, message_ids) so the caller reuses the same service for the
    per-message fetches rather than rebuilding it 25 times."""
    credentials = build_credentials(token_row)

    def _build_service():
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)

    try:
        service = await asyncio.to_thread(_build_service)
        message_ids = await asyncio.to_thread(_list_message_ids_sync, service)
        return [service, message_ids], None
    except HttpError as exc:
        if exc.resp.status != 401:
            error = f"HttpError {exc.resp.status}: {str(exc)[:300]}"
            logger.error(
                "gmail_sync_http_error", user_id=str(token_row.user_id), status=exc.resp.status
            )
            return [], error
    except Exception as exc:
        logger.exception("gmail_sync_failed", user_id=str(token_row.user_id))
        return [], f"{exc.__class__.__name__}: {str(exc)[:300]}"

    # Lazy refresh on 401, once, then give up (§04 refresh table).
    if not await refresh_token_row(session, token_row):
        return [], "token_refresh_failed"
    try:
        service = await asyncio.to_thread(_build_service)
        message_ids = await asyncio.to_thread(_list_message_ids_sync, service)
        return [service, message_ids], None
    except Exception as exc:
        logger.error("gmail_sync_failed_after_refresh", user_id=str(token_row.user_id))
        return [], f"{exc.__class__.__name__} after refresh: {str(exc)[:300]}"


def _parse_metadata(data: dict) -> dict:
    headers = {h["name"]: h["value"] for h in data.get("payload", {}).get("headers", [])}
    from_name, from_addr = parseaddr(headers.get("From", ""))
    internal_date_ms = data.get("internalDate")
    received_at = (
        datetime.fromtimestamp(int(internal_date_ms) / 1000, tz=UTC) if internal_date_ms else None
    )
    return {
        "thread_id": data.get("threadId"),
        "from_addr": from_addr or None,
        "from_name": from_name or None,
        "subject": headers.get("Subject") or None,
        "snippet": data.get("snippet") or None,
        "received_at": received_at,
    }


async def sync_user_gmail(session: AsyncSession, token_row: OAuthToken) -> tuple[int, str | None]:
    """Upserts new messages into email_cache and classifies only the new ones
    in one batched Gemini call (§04 §4.2). Never raises - failures are logged
    and returned as an error string, matching sync_user_calendar's contract."""
    user = await session.get(User, token_row.user_id)
    if user is None:
        return 0, "user_not_found"

    result, error = await _list_message_ids(token_row, session)
    if error is not None:
        return 0, error
    service, message_ids = result
    if not message_ids:
        return 0, None

    existing = await session.execute(
        select(EmailCache.gmail_message_id).where(EmailCache.user_id == token_row.user_id)
    )
    existing_ids = {row[0] for row in existing.all()}
    new_ids = [mid for mid in message_ids if mid not in existing_ids]
    if not new_ids:
        return 0, None

    new_rows: list[EmailCache] = []
    classify_batch: list[dict] = []
    for message_id in new_ids:
        try:
            data = await asyncio.to_thread(_get_message_metadata_sync, service, message_id)
        except Exception:
            logger.warning(
                "gmail_message_fetch_failed", user_id=str(token_row.user_id), message_id=message_id
            )
            continue

        parsed = _parse_metadata(data)
        row = EmailCache(user_id=token_row.user_id, gmail_message_id=message_id, **parsed)
        session.add(row)
        new_rows.append(row)
        received_at = parsed["received_at"]
        classify_batch.append(
            {
                "id": message_id,
                "from": parsed["from_addr"],
                "subject": parsed["subject"],
                "snippet": parsed["snippet"],
                # In the patient's timezone, so "next Tuesday" in a snippet
                # resolves to the date they'd actually turn up on.
                "received": (
                    received_at.astimezone(ZoneInfo(user.timezone)).strftime("%a %d %b %Y %H:%M")
                    if received_at
                    else None
                ),
            }
        )

    if not new_rows:
        return 0, None

    await session.flush()

    classifications = await gemini_client.classify_emails(
        emails=classify_batch, pipeline="email.classify", user_id=token_row.user_id
    )
    by_id = {c["id"]: c for c in (classifications or [])}

    for row in new_rows:
        item = by_id.get(row.gmail_message_id)
        if item is not None:
            row.category = item["category"]
            row.priority = item["priority"]
            row.needs_action = item["needs_action"]
            row.summary_en = item["summary_en"]
            row.summary_zh = item["summary_zh"]
        else:
            # Classification failed - still cache the email; the domain
            # floor below is the only thing that can still raise its priority.
            row.category = "other"
            row.priority = 1
            row.needs_action = False

        if _is_medical_domain(row.from_addr or ""):
            row.category = "medical"
            row.priority = max(row.priority or 1, _MEDICAL_FLOOR_PRIORITY)

    await session.flush()
    return len(new_rows), None
