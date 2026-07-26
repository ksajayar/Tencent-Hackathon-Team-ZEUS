import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.email import EmailCache

IMPORTANT_LOOKBACK = timedelta(days=3)
MIN_IMPORTANT_PRIORITY = 4
IMPORTANT_LIMIT = 3


async def get_important_emails(session: AsyncSession, user_id: uuid.UUID) -> list[EmailCache]:
    """§04 §4.2 'any important emails?': priority>=4, last 3 days, top 3 by
    priority then recency. No AI call here - the summaries were pre-computed
    at sync time, which is what makes repeat-asking feel instant."""
    cutoff = datetime.now(UTC) - IMPORTANT_LOOKBACK
    result = await session.execute(
        select(EmailCache)
        .where(
            EmailCache.user_id == user_id,
            EmailCache.priority >= MIN_IMPORTANT_PRIORITY,
            EmailCache.received_at > cutoff,
        )
        .order_by(EmailCache.priority.desc(), EmailCache.received_at.desc())
        .limit(IMPORTANT_LIMIT)
    )
    return list(result.scalars().all())


def render_important_emails(emails: list[EmailCache], language: str) -> str:
    """Deterministic, no LLM: renders the pre-computed summary_en/zh directly."""
    unknown_sender = "未知发件人" if language == "zh-Hans" else "someone"
    lines = []
    for email in emails:
        summary = (email.summary_zh if language == "zh-Hans" else email.summary_en) or ""
        sender = email.from_name or email.from_addr or unknown_sender
        lines.append(f"{sender}: {summary}")
    return "\n".join(lines)
