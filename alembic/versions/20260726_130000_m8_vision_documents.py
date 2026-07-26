"""M8: documents, medication_candidates

Revision ID: 20260726_130000
Revises: 20260726_110000
Create Date: 2026-07-26 13:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260726_130000"
down_revision: Union[str, None] = "20260726_110000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "media_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_files.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column("doc_kind", sa.Text(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("summary_en", sa.Text(), nullable=True),
        sa.Column("summary_zh", sa.Text(), nullable=True),
        sa.Column("was_scanned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "medication_candidates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_media_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("media_files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("extracted", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column(
            "reviewed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('pending','approved','rejected')",
            name="ck_medication_candidates_status",
        ),
    )
    op.create_index(
        "idx_medication_candidates_patient_status",
        "medication_candidates",
        ["patient_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("medication_candidates")
    op.drop_table("documents")
