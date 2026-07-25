"""Bind Liked Music subscriptions to one authenticated account.

Revision ID: 0a1b2c3d4e5f
Revises: f9a0b1c2d3e4
Create Date: 2026-07-25 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0a1b2c3d4e5f"
down_revision: str | Sequence[str] | None = "f9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("subscriptions") as batch:
        batch.add_column(
            sa.Column(
                "source_account_fingerprint",
                sa.String(length=64),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("subscriptions") as batch:
        batch.drop_column("source_account_fingerprint")
