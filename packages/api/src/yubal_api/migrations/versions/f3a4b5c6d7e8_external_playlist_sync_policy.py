"""Add sync policy + last-sync columns on external_playlists

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-07-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: str | Sequence[str] | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("external_playlists") as batch:
        batch.add_column(
            sa.Column(
                "enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "max_items",
                sa.Integer(),
                nullable=False,
                server_default="50",
            )
        )
        batch.add_column(
            sa.Column(
                "sync_jitter_seconds",
                sa.Integer(),
                nullable=False,
                server_default="600",
            )
        )
        batch.add_column(
            sa.Column(
                "offline_marking_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(sa.Column("last_synced_at", sa.DateTime(), nullable=True))
        batch.add_column(
            sa.Column("last_sync_status", sa.String(length=32), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("external_playlists") as batch:
        batch.drop_column("last_sync_status")
        batch.drop_column("last_synced_at")
        batch.drop_column("offline_marking_enabled")
        batch.drop_column("sync_jitter_seconds")
        batch.drop_column("max_items")
        batch.drop_column("enabled")
