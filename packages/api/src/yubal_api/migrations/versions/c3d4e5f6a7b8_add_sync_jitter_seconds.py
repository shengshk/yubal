"""Add sync_jitter_seconds to subscriptions

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-20 14:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column(
            "sync_jitter_seconds",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    # Backfill: each row gets a distinct-ish random 0..600
    op.execute(
        """
        UPDATE subscriptions
        SET sync_jitter_seconds = abs(random() % 601)
        WHERE sync_jitter_seconds = 0
        """
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "sync_jitter_seconds")
