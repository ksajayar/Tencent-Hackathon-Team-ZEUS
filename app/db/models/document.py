import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Document(Base):
    """§06 §6.4 prescription/document path and §05 §5.4 PDF pipeline both land
    here. Extraction only - CLAUDE.md SAFETY-1: nothing here ever drives a
    medication reminder, that's medication_candidates' job."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_files.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    doc_kind: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_text: Mapped[str | None] = mapped_column(Text)
    summary_en: Mapped[str | None] = mapped_column(Text)
    summary_zh: Mapped[str | None] = mapped_column(Text)
    was_scanned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
