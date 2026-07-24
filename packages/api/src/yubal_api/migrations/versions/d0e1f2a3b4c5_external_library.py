"""Add external library tables + dual-root columns on tracks/track_locations

Introduces external_playlists and external_raw_tracks for the dual-root
(Download + External) music library, and adds immutable/canonical tracking
to tracks plus storage_root to track_locations so a location can live under
either root. preselect_tracks is left in place for now (unused going
forward, dropped in a later cleanup pass).

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-07-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: str | Sequence[str] | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_playlists",
        sa.Column("dir_name", sa.String(length=255), nullable=False),
        sa.Column("allow_mutate", sa.Boolean(), nullable=False),
        sa.Column("show_raw", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("dir_name"),
    )

    op.create_table(
        "external_raw_tracks",
        sa.Column("rel_path", sa.String(length=1200), nullable=False),
        sa.Column("dir_name", sa.String(length=255), nullable=False),
        sa.Column("mtime_ns", sa.Integer(), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("inode", sa.Integer(), nullable=True),
        sa.Column("codec", sa.String(length=32), nullable=False),
        sa.Column("sample_rate", sa.Integer(), nullable=True),
        sa.Column("bit_depth", sa.Integer(), nullable=True),
        sa.Column("channels", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("artists", sa.String(length=500), nullable=False),
        sa.Column("album", sa.String(length=500), nullable=False),
        sa.Column("album_artist", sa.String(length=500), nullable=False),
        sa.Column("track_number", sa.Integer(), nullable=True),
        sa.Column("disc_number", sa.Integer(), nullable=True),
        sa.Column("year", sa.String(length=16), nullable=True),
        sa.Column("title_norm", sa.String(length=500), nullable=False),
        sa.Column("artist_norm", sa.String(length=500), nullable=False),
        sa.Column("album_norm", sa.String(length=500), nullable=False),
        sa.Column("has_lyrics", sa.Boolean(), nullable=False),
        sa.Column("lyrics_embedded", sa.Boolean(), nullable=False),
        sa.Column("has_cover", sa.Boolean(), nullable=False),
        sa.Column("cover_embedded", sa.Boolean(), nullable=False),
        sa.Column("video_id", sa.String(length=32), nullable=True),
        sa.Column("match_status", sa.String(length=16), nullable=False),
        sa.Column("match_confidence", sa.Float(), nullable=True),
        sa.Column("match_fail_count", sa.Integer(), nullable=False),
        sa.Column("scrape_fail_count", sa.Integer(), nullable=False),
        sa.Column("match_next_eligible_at", sa.DateTime(), nullable=True),
        sa.Column("scrape_next_eligible_at", sa.DateTime(), nullable=True),
        sa.Column("file_key", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("rel_path"),
    )
    with op.batch_alter_table("external_raw_tracks") as batch:
        batch.create_index(
            "ix_external_raw_tracks_dir_name", ["dir_name"], unique=False
        )
        batch.create_index(
            "ix_external_raw_tracks_title_norm", ["title_norm"], unique=False
        )
        batch.create_index(
            "ix_external_raw_tracks_artist_norm", ["artist_norm"], unique=False
        )
        batch.create_index(
            "ix_external_raw_tracks_video_id", ["video_id"], unique=False
        )
        batch.create_index(
            "ix_external_raw_tracks_match_status", ["match_status"], unique=False
        )

    with op.batch_alter_table("tracks") as batch:
        batch.add_column(
            sa.Column(
                "immutable", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch.add_column(
            sa.Column("canonical_storage", sa.String(length=16), nullable=True)
        )
        batch.add_column(
            sa.Column("canonical_rel", sa.String(length=1200), nullable=True)
        )

    with op.batch_alter_table("track_locations") as batch:
        batch.add_column(
            sa.Column(
                "storage_root",
                sa.String(length=16),
                nullable=False,
                server_default="download",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("track_locations") as batch:
        batch.drop_column("storage_root")

    with op.batch_alter_table("tracks") as batch:
        batch.drop_column("canonical_rel")
        batch.drop_column("canonical_storage")
        batch.drop_column("immutable")

    with op.batch_alter_table("external_raw_tracks") as batch:
        batch.drop_index("ix_external_raw_tracks_match_status")
        batch.drop_index("ix_external_raw_tracks_video_id")
        batch.drop_index("ix_external_raw_tracks_artist_norm")
        batch.drop_index("ix_external_raw_tracks_title_norm")
        batch.drop_index("ix_external_raw_tracks_dir_name")
    op.drop_table("external_raw_tracks")
    op.drop_table("external_playlists")
