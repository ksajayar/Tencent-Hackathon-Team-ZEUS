"""M7: email_cache

Revision ID: 20260726_110000
Revises: 20260726_090000
Create Date: 2026-07-26 11:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260726_110000"
down_revision: Union[str, None] = "20260726_090000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_cache",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("gmail_message_id", sa.Text(), nullable=False),
        sa.Column("thread_id", sa.Text(), nullable=True),
        sa.Column("from_addr", sa.Text(), nullable=True),
        sa.Column("from_name", sa.Text(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("priority", sa.SmallInteger(), nullable=True),
        sa.Column("needs_action", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("summary_en", sa.Text(), nullable=True),
        sa.Column("summary_zh", sa.Text(), nullable=True),
        sa.Column("is_read_to_user", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("priority BETWEEN 1 AND 5", name="ck_email_cache_priority"),
    )
    op.create_index(
        "idx_email_cache_user_priority",
        "email_cache",
        ["user_id", sa.text("priority DESC"), sa.text("received_at DESC")],
    )
    op.create_index(
        "uq_email_cache_user_gmail_id",
        "email_cache",
        ["user_id", "gmail_message_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("email_cache")
