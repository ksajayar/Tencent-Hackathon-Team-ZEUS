"""M10: caregiver commands - documents.patient_id, safe_zones.address

Revision ID: 20260803_090000
Revises: 20260726_150000
Create Date: 2026-08-03 09:00:00

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260803_090000"
down_revision: Union[str, None] = "20260726_150000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable: existing patient-upload paths (pipelines/document.py,
    # pipelines/image.py) don't set this yet - a caregiver-uploaded document
    # (§17 set bloodwork) is the first writer that needs to know which
    # patient it belongs to, since a caregiver's own message chain isn't the
    # patient's. Backfilling the old rows is a later, separate concern.
    op.add_column(
        "documents",
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("idx_documents_patient_kind", "documents", ["patient_id", "doc_kind"])

    # §17 set bloodwork accepts plain typed text as well as a photo or PDF -
    # a pasted lab result has no media_file behind it at all, so the
    # media_id FK (previously NOT NULL and UNIQUE, one document per upload)
    # has to allow null. UNIQUE still holds for non-null values.
    op.alter_column("documents", "media_id", nullable=True)

    # §17 set address: the human-readable address for the kind='home' zone.
    # A caregiver who supplies only text, no location pin, gets an answer to
    # "where's my home" but no geofence (no geocoder exists in this app to
    # turn text into coordinates) - so center_lat/center_lon/radius_m have to
    # allow null too, rather than being filled with a placeholder that would
    # sit in the table looking like real, meaningful data. geo.match_safe_zone
    # skips any zone with a null center. See docs/17 §6.3.
    op.add_column("safe_zones", sa.Column("address", sa.Text(), nullable=True))
    op.alter_column("safe_zones", "center_lat", nullable=True)
    op.alter_column("safe_zones", "center_lon", nullable=True)
    op.alter_column("safe_zones", "radius_m", nullable=True)


def downgrade() -> None:
    # All three fail if any row actually has a null value written by set
    # address without a pin - inherent to reversing nullable->not-null
    # against real data, not specific to this migration.
    op.alter_column("safe_zones", "radius_m", nullable=False)
    op.alter_column("safe_zones", "center_lon", nullable=False)
    op.alter_column("safe_zones", "center_lat", nullable=False)
    op.drop_column("safe_zones", "address")
    op.alter_column("documents", "media_id", nullable=False)
    op.drop_index("idx_documents_patient_kind", table_name="documents")
    op.drop_column("documents", "patient_id")
