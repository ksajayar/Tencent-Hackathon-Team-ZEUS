import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Medication(Base):
    """SAFETY-1 source of truth. verified_by must be non-null for a row to
    drive a reminder - unverified rows are invisible to the scheduler."""

    __tablename__ = "medications"
    __table_args__ = (
        Index(
            "idx_medications_active_verified",
            "patient_id",
            postgresql_where=text("active AND verified_by IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    dose_text: Mapped[str] = mapped_column(Text, nullable=False)
    schedule_rrule: Mapped[str] = mapped_column(Text, nullable=False)
    instruction_en: Mapped[str] = mapped_column(Text, nullable=False)
    instruction_zh: Mapped[str] = mapped_column(Text, nullable=False)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class MedicationCandidate(Base):
    """§06 §6.4 pill-bottle/prescription vision output. Never medications
    (SAFETY-1) - a caregiver action (not built: no caregiver UI, CLAUDE.md
    'what not to build') would be the only thing allowed to promote a row."""

    __tablename__ = "medication_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected')", name="ck_medication_candidates_status"
        ),
        Index("idx_medication_candidates_patient_status", "patient_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    source_media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False
    )
    extracted: Mapped[dict] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
