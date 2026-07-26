import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LocationPing(Base):
    """§07 §7.10. One row per shared pin - WhatsApp gives a single lat/lon on
    request, not a stream (no background GPS), so this is pull-based by
    design. 30-day retention enforced by the nightly cleanup job (§08 §8.4,
    not built until M10)."""

    __tablename__ = "location_pings"
    __table_args__ = (Index("idx_location_pings_user_created", "user_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    lat: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    lon: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    accuracy_m: Mapped[int | None] = mapped_column(Integer)
    address: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="whatsapp_pin")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class SafeZone(Base):
    """§07 §7.10. `kind='shop'` zones carry their own trigger message so the
    pull-based check triggers a reminder without an AI call; `home`/`safe`
    log only; anywhere else alerts the emergency contacts."""

    __tablename__ = "safe_zones"
    __table_args__ = (CheckConstraint("kind IN ('home','safe','shop')", name="ck_safe_zones_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    center_lat: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    center_lon: Mapped[float] = mapped_column(Numeric(9, 6), nullable=False)
    radius_m: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_message_en: Mapped[str | None] = mapped_column(Text)
    trigger_message_zh: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
