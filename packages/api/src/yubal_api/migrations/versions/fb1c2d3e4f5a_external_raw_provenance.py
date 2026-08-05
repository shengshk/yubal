"""Require provenance for external raw files.

Revision ID: fb1c2d3e4f5a
Revises: fa0b1c2d3e4f
Create Date: 2026-07-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "fb1c2d3e4f5a"
down_revision: str | Sequence[str] | None = "fa0b1c2d3e4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("external_raw_tracks") as batch:
        batch.add_column(
            sa.Column(
                "origin_kind",
                sa.String(length=32),
                nullable=False,
                server_default="",
            )
        )
        batch.add_column(
            sa.Column(
                "origin_ref",
                sa.String(length=128),
                nullable=False,
                server_default="",
            )
        )

    conn = op.get_bind()
    playlists = dict(
        conn.execute(
            sa.text("SELECT dir_name, playlist_uid FROM external_playlists")
        ).fetchall()
    )
    # Raw files still in their own playlist have an unambiguous source.
    # Raw/Default is the documented manual archive ingress; other folders are
    # external playlists. Historical Raw/Delete entries did not retain their
    # source and are deliberately discarded instead of being relabelled.
    for dir_name, playlist_uid in playlists.items():
        if dir_name == "delete":
            continue
        origin_kind = "manual" if dir_name == "default" else "external"
        origin_ref = "archive" if dir_name == "default" else playlist_uid
        conn.execute(
            sa.text(
                "UPDATE external_raw_tracks SET origin_kind = :kind, "
                "origin_ref = :origin_ref WHERE dir_name = :dir_name"
            ),
            {
                "kind": origin_kind,
                "origin_ref": origin_ref,
                "dir_name": dir_name,
            },
        )
    conn.execute(
        sa.text(
            "DELETE FROM external_raw_tracks "
            "WHERE origin_kind = '' OR origin_ref = ''"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("external_raw_tracks") as batch:
        batch.drop_column("origin_ref")
        batch.drop_column("origin_kind")
