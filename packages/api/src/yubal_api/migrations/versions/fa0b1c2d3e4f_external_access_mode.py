"""Add explicit activation mode for newly discovered external folders.

Revision ID: fa0b1c2d3e4f
Revises: 0a1b2c3d4e5f
Create Date: 2026-07-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fa0b1c2d3e4f"
down_revision: str | Sequence[str] | None = "0a1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("external_playlists") as batch:
        batch.add_column(
            sa.Column(
                "access_mode",
                sa.String(length=16),
                nullable=False,
                server_default="pending",
            )
        )
    # Existing folders already had a deliberate read/write policy. Preserve it
    # instead of unexpectedly placing a live library back into setup mode.
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE external_playlists "
            "SET access_mode = CASE "
            "WHEN allow_mutate THEN 'managed' ELSE 'readonly' END"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("external_playlists") as batch:
        batch.drop_column("access_mode")
