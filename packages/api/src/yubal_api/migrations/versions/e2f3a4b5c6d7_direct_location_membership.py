"""Add membership_status / missing_since on track_locations (Direct list)

Direct playlists keep catalog locations as the recovery list. When a file is
deleted with keep-list, the location stays; when YTM is gone, status becomes
offline so auto-recover will not retry forever.

Revision ID: e2f3a4b5c6d7
Revises: d0e1f2a3b4c5
Create Date: 2026-07-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2f3a4b5c6d7"
down_revision: str | Sequence[str] | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("track_locations") as batch:
        batch.add_column(
            sa.Column(
                "membership_status",
                sa.String(length=20),
                nullable=False,
                server_default="active",
            )
        )
        batch.add_column(sa.Column("missing_since", sa.DateTime(), nullable=True))
        batch.create_index(
            "ix_track_locations_membership_status",
            ["membership_status"],
        )


def downgrade() -> None:
    with op.batch_alter_table("track_locations") as batch:
        batch.drop_index("ix_track_locations_membership_status")
        batch.drop_column("missing_since")
        batch.drop_column("membership_status")
