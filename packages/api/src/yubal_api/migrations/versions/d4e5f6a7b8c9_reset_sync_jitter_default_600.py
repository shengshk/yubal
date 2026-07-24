"""Reset sync_jitter_seconds default to 600 (max magnitude)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-20 14:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # N is the max |offset|, not a fixed offset — default / reset to 600
    op.execute("UPDATE subscriptions SET sync_jitter_seconds = 600")
    with op.batch_alter_table("subscriptions") as batch:
        batch.alter_column(
            "sync_jitter_seconds",
            existing_type=sa.Integer(),
            server_default="600",
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("subscriptions") as batch:
        batch.alter_column(
            "sync_jitter_seconds",
            existing_type=sa.Integer(),
            server_default="0",
            existing_nullable=False,
        )
