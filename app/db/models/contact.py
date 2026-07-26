import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Contact(Base):
    """§07 §7.11, §07 §7.9. `is_emergency` rows are the entire SOS contact
    list (SAFETY-2) - no caregiver_links table exists yet (CLAUDE.md: no
    caregiver UI), so this is the only place an emergency contact lives."""

    __tablename__ = "contacts"
    __table_args__ = (
        CheckConstraint("source IN ('vcard','manual','google')", name="ck_contacts_source"),
        Index("idx_contacts_user_emergency", "user_id", "is_emergency", "priority"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    phone_e164: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    relationship: Mapped[str | None] = mapped_column(Text)
    is_emergency: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    priority: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
