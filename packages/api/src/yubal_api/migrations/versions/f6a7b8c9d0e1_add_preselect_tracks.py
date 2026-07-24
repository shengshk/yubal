"""Add preselect_tracks + track_locations.origin

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-20 16:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "preselect_tracks",
        sa.Column("rel_path", sa.String(length=1200), nullable=False),
        sa.Column("mtime_ns", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("inode", sa.BigInteger(), nullable=True),
        sa.Column("codec", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("sample_rate", sa.Integer(), nullable=True),
        sa.Column("bit_depth", sa.Integer(), nullable=True),
        sa.Column("channels", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("artists", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("album", sa.String(length=500), nullable=False, server_default=""),
        sa.Column(
            "album_artist", sa.String(length=500), nullable=False, server_default=""
        ),
        sa.Column("track_number", sa.Integer(), nullable=True),
        sa.Column("disc_number", sa.Integer(), nullable=True),
        sa.Column("year", sa.String(length=16), nullable=True),
        sa.Column(
            "title_norm", sa.String(length=500), nullable=False, server_default=""
        ),
        sa.Column(
            "artist_norm", sa.String(length=500), nullable=False, server_default=""
        ),
        sa.Column(
            "album_norm", sa.String(length=500), nullable=False, server_default=""
        ),
        sa.Column(
            "has_lyrics", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "lyrics_embedded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "has_cover", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "cover_embedded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("rel_path"),
    )
    op.create_index(
        "ix_preselect_tracks_title_norm", "preselect_tracks", ["title_norm"]
    )
    op.create_index(
        "ix_preselect_tracks_artist_norm", "preselect_tracks", ["artist_norm"]
    )
    op.create_index(
        "ix_preselect_tracks_artist_title",
        "preselect_tracks",
        ["artist_norm", "title_norm"],
    )

    with op.batch_alter_table("track_locations") as batch:
        batch.add_column(
            sa.Column(
                "origin",
                sa.String(length=32),
                nullable=False,
                server_default="download",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("track_locations") as batch:
        batch.drop_column("origin")
    op.drop_index("ix_preselect_tracks_artist_title", table_name="preselect_tracks")
    op.drop_index("ix_preselect_tracks_artist_norm", table_name="preselect_tracks")
    op.drop_index("ix_preselect_tracks_title_norm", table_name="preselect_tracks")
    op.drop_table("preselect_tracks")
