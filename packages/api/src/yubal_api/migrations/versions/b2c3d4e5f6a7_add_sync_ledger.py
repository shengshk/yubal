"""Add sync_ledger table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-19 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_ledger",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.VARCHAR(length=80), nullable=False),
        sa.Column("kind", sa.VARCHAR(length=32), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=True),
        sa.Column("save_folder", sa.VARCHAR(length=200), nullable=False),
        sa.Column("title", sa.VARCHAR(length=200), nullable=False),
        sa.Column("thumbnail_url", sa.VARCHAR(length=2048), nullable=True),
        sa.Column("content_kind", sa.VARCHAR(length=32), nullable=False),
        sa.Column("url", sa.VARCHAR(length=2048), nullable=True),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("synced_count", sa.Integer(), nullable=False),
        sa.Column("real_download_count", sa.Integer(), nullable=False),
        sa.Column("hardlink_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("skipped_ugc", sa.Integer(), nullable=False),
        sa.Column("skipped_region", sa.Integer(), nullable=False),
        sa.Column("skipped_other", sa.Integer(), nullable=False),
        sa.Column("last_job_id", sa.VARCHAR(length=64), nullable=True),
        sa.Column("last_job_status", sa.VARCHAR(length=32), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_index("ix_sync_ledger_key", "sync_ledger", ["key"])
    op.create_index("ix_sync_ledger_kind", "sync_ledger", ["kind"])
    op.create_index(
        "ix_sync_ledger_subscription_id", "sync_ledger", ["subscription_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_sync_ledger_subscription_id", table_name="sync_ledger")
    op.drop_index("ix_sync_ledger_kind", table_name="sync_ledger")
    op.drop_index("ix_sync_ledger_key", table_name="sync_ledger")
    op.drop_table("sync_ledger")
