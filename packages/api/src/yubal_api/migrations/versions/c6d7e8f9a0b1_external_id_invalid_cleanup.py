"""Add ID-invalid cleanup policy on external_playlists

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-07-23 07:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c6d7e8f9a0b1"
down_revision: str | Sequence[str] | None = "b5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("external_playlists") as batch:
        batch.add_column(
            sa.Column(
                "offline_cleanup_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "offline_cleanup_action",
                sa.String(length=16),
                nullable=False,
                server_default="archive",
            )
        )
        batch.add_column(
            sa.Column(
                "offline_cleanup_delay_hours",
                sa.Integer(),
                nullable=False,
                server_default="72",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("external_playlists") as batch:
        batch.drop_column("offline_cleanup_delay_hours")
        batch.drop_column("offline_cleanup_action")
        batch.drop_column("offline_cleanup_enabled")
