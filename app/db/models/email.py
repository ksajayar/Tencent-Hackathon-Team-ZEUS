import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EmailCache(Base):
    """§04 §4.2: snippets and pre-computed summaries only - never full bodies
    (§07 §7.3 security note). No column exists to store one even by mistake."""

    __tablename__ = "email_cache"
    __table_args__ = (
        CheckConstraint("priority BETWEEN 1 AND 5", name="ck_email_cache_priority"),
        Index(
            "idx_email_cache_user_priority",
            "user_id",
            text("priority DESC"),
            text("received_at DESC"),
        ),
        Index("uq_email_cache_user_gmail_id", "user_id", "gmail_message_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    gmail_message_id: Mapped[str] = mapped_column(Text, nullable=False)
    thread_id: Mapped[str | None] = mapped_column(Text)
    from_addr: Mapped[str | None] = mapped_column(Text)
    from_name: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    category: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int | None] = mapped_column(SmallInteger)
    needs_action: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    summary_en: Mapped[str | None] = mapped_column(Text)
    summary_zh: Mapped[str | None] = mapped_column(Text)
    is_read_to_user: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
