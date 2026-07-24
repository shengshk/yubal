"""Add lyrics_source (tracks) and last_sync_status columns

Records lyrics provenance so the edit modal can show a real source, and
persists the last sync outcome on subscriptions so it survives restarts.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-21 23:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tracks") as batch:
        batch.add_column(
            sa.Column("lyrics_source", sa.String(length=16), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("tracks") as batch:
        batch.drop_column("lyrics_source")
