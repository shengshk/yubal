"""Record completed external YTM and metadata attempts.

Revision ID: ff5a6b7c8d9e
Revises: fe4f5a6b7c8d
Create Date: 2026-08-01 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ff5a6b7c8d9e"
down_revision: str | Sequence[str] | None = "fe4f5a6b7c8d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("external_raw_tracks") as batch:
        batch.add_column(sa.Column("ytm_attempted_at", sa.DateTime()))
        batch.add_column(sa.Column("meta_attempted_at", sa.DateTime()))
        batch.create_index(
            "ix_external_raw_tracks_ytm_attempted_at",
            ["ytm_attempted_at"],
        )
        batch.create_index(
            "ix_external_raw_tracks_meta_attempted_at",
            ["meta_attempted_at"],
        )

    # Preserve known historical decisions. Only genuinely untouched rows stay
    # null and enter the new one-pass media scan.
    op.execute(
        sa.text(
            "UPDATE external_raw_tracks SET ytm_attempted_at = updated_at "
            "WHERE video_id IS NOT NULL "
            "OR match_status IN ('pending', 'matched', 'rejected') "
            "OR match_fail_count > 0"
        )
    )
    op.execute(
        sa.text(
            "UPDATE external_raw_tracks "
            "SET meta_attempted_at = COALESCE(meta_verified_at, updated_at) "
            "WHERE meta_status IN ('verified', 'rejected') OR meta_fail_count > 0"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("external_raw_tracks") as batch:
        batch.drop_index("ix_external_raw_tracks_meta_attempted_at")
        batch.drop_index("ix_external_raw_tracks_ytm_attempted_at")
        batch.drop_column("meta_attempted_at")
        batch.drop_column("ytm_attempted_at")
