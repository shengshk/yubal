"""Stable external playlist uid + track origin hukou

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-07-23 00:00:00.000000

"""

from collections.abc import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "a4b5c6d7e8f9"
down_revision: str | Sequence[str] | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("external_playlists") as batch:
        batch.add_column(
            sa.Column("playlist_uid", sa.String(length=36), nullable=True)
        )

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT dir_name FROM external_playlists")).fetchall()
    for (dir_name,) in rows:
        conn.execute(
            sa.text(
                "UPDATE external_playlists SET playlist_uid = :uid "
                "WHERE dir_name = :dir_name"
            ),
            {"uid": str(uuid4()), "dir_name": dir_name},
        )

    with op.batch_alter_table("external_playlists") as batch:
        batch.alter_column("playlist_uid", existing_type=sa.String(36), nullable=False)
        batch.create_index(
            "ix_external_playlists_playlist_uid",
            ["playlist_uid"],
            unique=True,
        )

    with op.batch_alter_table("tracks") as batch:
        batch.add_column(
            sa.Column("origin_playlist_uid", sa.String(length=36), nullable=True)
        )
        batch.create_index(
            "ix_tracks_origin_playlist_uid",
            ["origin_playlist_uid"],
        )

    # Backfill hukou from Organized/<dir_name> locations (first playlist wins).
    playlists = conn.execute(
        sa.text("SELECT dir_name, playlist_uid, allow_mutate FROM external_playlists")
    ).fetchall()
    for dir_name, playlist_uid, allow_mutate in playlists:
        save_folder = f"Organized/{dir_name}"
        video_ids = conn.execute(
            sa.text(
                "SELECT DISTINCT video_id FROM track_locations "
                "WHERE save_folder = :sf"
            ),
            {"sf": save_folder},
        ).fetchall()
        immutable = 0 if allow_mutate else 1
        for (video_id,) in video_ids:
            conn.execute(
                sa.text(
                    "UPDATE tracks SET origin_playlist_uid = :uid, immutable = :imm "
                    "WHERE video_id = :vid AND origin_playlist_uid IS NULL"
                ),
                {"uid": playlist_uid, "imm": immutable, "vid": video_id},
            )


def downgrade() -> None:
    with op.batch_alter_table("tracks") as batch:
        batch.drop_index("ix_tracks_origin_playlist_uid")
        batch.drop_column("origin_playlist_uid")

    with op.batch_alter_table("external_playlists") as batch:
        batch.drop_index("ix_external_playlists_playlist_uid")
        batch.drop_column("playlist_uid")
