"""Add tracks and track_locations catalog tables

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-20 18:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tracks",
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("artist", sa.String(length=500), nullable=False),
        sa.Column("album_artist", sa.String(length=500), nullable=False),
        sa.Column("album", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("track_number", sa.Integer(), nullable=True),
        sa.Column("year", sa.String(length=16), nullable=True),
        sa.Column("cover_url", sa.String(length=2048), nullable=True),
        sa.Column("lyrics", sa.Text(), nullable=True),
        sa.Column(
            "has_embedded_cover",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "has_lyrics_embedded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "has_lyrics_sidecar",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("video_id"),
    )
    op.create_table(
        "track_locations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("save_folder", sa.String(length=200), nullable=False),
        sa.Column("relative_path", sa.String(length=1000), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["video_id"], ["tracks.video_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_track_locations_video_id", "track_locations", ["video_id"]
    )
    op.create_index(
        "ix_track_locations_save_folder", "track_locations", ["save_folder"]
    )


def downgrade() -> None:
    op.drop_index("ix_track_locations_save_folder", table_name="track_locations")
    op.drop_index("ix_track_locations_video_id", table_name="track_locations")
    op.drop_table("track_locations")
    op.drop_table("tracks")
