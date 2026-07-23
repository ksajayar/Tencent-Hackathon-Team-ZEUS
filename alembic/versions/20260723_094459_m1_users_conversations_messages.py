"""M1: users, conversations, messages

Revision ID: 20260723_094459
Revises:
Create Date: 2026-07-23 09:44:59

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260723_094459"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("wa_id", sa.Text(), nullable=False),
        sa.Column("phone_e164", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("preferred_language", sa.Text(), nullable=False, server_default="en"),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="Asia/Singapore"),
        sa.Column("role", sa.Text(), nullable=False, server_default="patient"),
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("preferred_language IN ('en', 'zh-Hans')", name="ck_users_preferred_language"),
        sa.CheckConstraint("role IN ('patient', 'caregiver')", name="ck_users_role"),
        sa.UniqueConstraint("wa_id", name="users_wa_id_key"),
    )
    op.create_index("idx_users_last_inbound", "users", ["last_inbound_at"])

    op.create_table(
        "conversations",
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
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column(
            "last_message_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("is_open", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("idx_conversations_user_open", "conversations", ["user_id", "is_open"])

    op.create_table(
        "messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("channel_sid", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("detected_language", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="received"),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("meta", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("direction IN ('inbound', 'outbound')", name="ck_messages_direction"),
        sa.CheckConstraint(
            "kind IN ('text', 'audio', 'image', 'document', 'location', 'contact')",
            name="ck_messages_kind",
        ),
        sa.CheckConstraint(
            "detected_language IS NULL OR detected_language IN ('en', 'zh-Hans', 'mixed')",
            name="ck_messages_detected_language",
        ),
        sa.CheckConstraint(
            "status IN ('received', 'queued', 'sent', 'delivered', 'read', 'failed')",
            name="ck_messages_status",
        ),
        sa.UniqueConstraint("channel_sid", name="messages_channel_sid_key"),
    )
    op.create_index("idx_messages_user_created", "messages", ["user_id", "created_at"])
    op.create_index(
        "idx_messages_status_pending",
        "messages",
        ["status"],
        postgresql_where=sa.text("status IN ('queued', 'failed')"),
    )


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("users")
