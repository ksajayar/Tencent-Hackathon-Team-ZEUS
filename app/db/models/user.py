import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("preferred_language IN ('en', 'zh-Hans')", name="ck_users_preferred_language"),
        CheckConstraint("role IN ('patient', 'caregiver')", name="ck_users_role"),
        Index("idx_users_last_inbound", "last_inbound_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    wa_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    phone_e164: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    preferred_language: Mapped[str] = mapped_column(Text, nullable=False, server_default="en")
    timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="Asia/Singapore")
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default="patient")
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
