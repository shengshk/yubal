"""Lock external access mode after the first source mutation.

Revision ID: fd3e4f5a6b7c
Revises: fc2d3e4f5a6b
Create Date: 2026-07-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fd3e4f5a6b7c"
down_revision: str | Sequence[str] | None = "fc2d3e4f5a6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("external_playlists") as batch:
        batch.add_column(sa.Column("source_mutated_at", sa.DateTime()))
        batch.add_column(sa.Column("source_mutation_kind", sa.String(length=32)))
    # Backfill only hard evidence: an external_move location means Yubal
    # previously moved the original Raw audio into Organized. Hardlink-only
    # locations deliberately remain switchable.
    op.execute(
        sa.text(
            "UPDATE external_playlists "
            "SET source_mutated_at = CURRENT_TIMESTAMP, "
            "source_mutation_kind = 'audio_moved' "
            "WHERE playlist_uid IN ("
            "SELECT DISTINCT tracks.origin_playlist_uid "
            "FROM tracks "
            "JOIN track_locations "
            "ON track_locations.video_id = tracks.video_id "
            "WHERE track_locations.origin = 'external_move' "
            "AND tracks.origin_playlist_uid IS NOT NULL"
            ")"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("external_playlists") as batch:
        batch.drop_column("source_mutation_kind")
        batch.drop_column("source_mutated_at")
