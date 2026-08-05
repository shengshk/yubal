"""Add lightweight pending-folder inventory.

Revision ID: fc2d3e4f5a6b
Revises: fb1c2d3e4f5a
Create Date: 2026-07-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fc2d3e4f5a6b"
down_revision: str | Sequence[str] | None = "fb1c2d3e4f5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("external_playlists") as batch:
        batch.add_column(sa.Column("discovered_audio_count", sa.Integer()))
        batch.add_column(sa.Column("discovered_cover_rel", sa.String(length=1200)))
        batch.add_column(sa.Column("inventory_scanned_at", sa.DateTime()))


def downgrade() -> None:
    with op.batch_alter_table("external_playlists") as batch:
        batch.drop_column("inventory_scanned_at")
        batch.drop_column("discovered_cover_rel")
        batch.drop_column("discovered_audio_count")
