import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SosEvent(Base):
    """CLAUDE.md SAFETY-2: written by the deterministic regex path only,
    never by an LLM. `notified` records each contact tried and the outcome,
    for the 5-minute-per-contact rate limit and for the demo debug view.
    Never auto-resolved - `resolved_at` stays null until a human acts."""

    __tablename__ = "sos_events"
    __table_args__ = (Index("idx_sos_events_user_triggered", "user_id", "triggered_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    trigger_text: Mapped[str | None] = mapped_column(Text)
    location_ping_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("location_pings.id", ondelete="SET NULL")
    )
    notified: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
