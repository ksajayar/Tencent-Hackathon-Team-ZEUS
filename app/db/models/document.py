import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Document(Base):
    """§06 §6.4 prescription/document path and §05 §5.4 PDF pipeline both land
    here. Extraction only - CLAUDE.md SAFETY-1: nothing here ever drives a
    medication reminder, that's medication_candidates' job."""

    __tablename__ = "documents"
    __table_args__ = (Index("idx_documents_patient_kind", "patient_id", "doc_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # §17: nullable - set bloodwork accepts plain typed text with no photo or
    # PDF behind it, so there is sometimes no media_files row to reference.
    # unique=True still holds for the non-null values (one document per
    # upload, unchanged for every other caller).
    media_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("media_files.id", ondelete="CASCADE"),
        unique=True,
    )
    # §17: nullable because the original patient-upload paths never set it -
    # only a caregiver upload (whose message chain belongs to the caregiver,
    # not the patient) actually needs this to find its way back.
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    doc_kind: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_text: Mapped[str | None] = mapped_column(Text)
    summary_en: Mapped[str | None] = mapped_column(Text)
    summary_zh: Mapped[str | None] = mapped_column(Text)
    was_scanned: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
