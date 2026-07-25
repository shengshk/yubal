"""Add wanted_tracks table for wishlist playlist

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-07-25 08:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d7e8f9a0b1c2"
down_revision: str | Sequence[str] | None = "c6d7e8f9a0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wanted_tracks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("artists", sa.String(length=500), nullable=False),
        sa.Column("album", sa.String(length=500), nullable=False),
        sa.Column("title_norm", sa.String(length=500), nullable=False),
        sa.Column("artist_norm", sa.String(length=500), nullable=False),
        sa.Column("album_norm", sa.String(length=500), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("thumbnail_url", sa.String(length=2048), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("relative_path", sa.String(length=1200), nullable=True),
        sa.Column("video_id", sa.String(length=32), nullable=True),
        sa.Column("match_fail_count", sa.Integer(), nullable=False),
        sa.Column("match_next_eligible_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_wanted_tracks_title_norm", "wanted_tracks", ["title_norm"])
    op.create_index("ix_wanted_tracks_artist_norm", "wanted_tracks", ["artist_norm"])
    op.create_index("ix_wanted_tracks_album_norm", "wanted_tracks", ["album_norm"])
    op.create_index("ix_wanted_tracks_source", "wanted_tracks", ["source"])
    op.create_index(
        "ix_wanted_tracks_relative_path", "wanted_tracks", ["relative_path"]
    )
    op.create_index("ix_wanted_tracks_video_id", "wanted_tracks", ["video_id"])


def downgrade() -> None:
    op.drop_index("ix_wanted_tracks_video_id", table_name="wanted_tracks")
    op.drop_index("ix_wanted_tracks_relative_path", table_name="wanted_tracks")
    op.drop_index("ix_wanted_tracks_source", table_name="wanted_tracks")
    op.drop_index("ix_wanted_tracks_album_norm", table_name="wanted_tracks")
    op.drop_index("ix_wanted_tracks_artist_norm", table_name="wanted_tracks")
    op.drop_index("ix_wanted_tracks_title_norm", table_name="wanted_tracks")
    op.drop_table("wanted_tracks")
