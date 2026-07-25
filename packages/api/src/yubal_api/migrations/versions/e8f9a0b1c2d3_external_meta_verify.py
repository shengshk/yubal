"""Add meta-verification columns on external_raw_tracks

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-07-25 10:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e8f9a0b1c2d3"
down_revision: str | Sequence[str] | None = "d7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("external_raw_tracks") as batch:
        batch.add_column(
            sa.Column(
                "meta_status",
                sa.String(length=16),
                nullable=False,
                server_default="pending",
            )
        )
        batch.add_column(sa.Column("meta_source", sa.String(length=32), nullable=True))
        batch.add_column(
            sa.Column("meta_source_id", sa.String(length=128), nullable=True)
        )
        batch.add_column(
            sa.Column("meta_source_url", sa.String(length=2048), nullable=True)
        )
        batch.add_column(sa.Column("meta_title", sa.String(length=500), nullable=True))
        batch.add_column(
            sa.Column("meta_artists", sa.String(length=500), nullable=True)
        )
        batch.add_column(sa.Column("meta_album", sa.String(length=500), nullable=True))
        batch.add_column(
            sa.Column("meta_thumbnail_url", sa.String(length=2048), nullable=True)
        )
        batch.add_column(
            sa.Column("meta_fingerprint", sa.String(length=600), nullable=True)
        )
        batch.add_column(sa.Column("meta_verified_at", sa.DateTime(), nullable=True))
        batch.add_column(
            sa.Column(
                "meta_fail_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(
            sa.Column("meta_next_eligible_at", sa.DateTime(), nullable=True)
        )
        batch.create_index(
            "ix_external_raw_tracks_meta_status",
            ["meta_status"],
        )


def downgrade() -> None:
    with op.batch_alter_table("external_raw_tracks") as batch:
        batch.drop_index("ix_external_raw_tracks_meta_status")
        batch.drop_column("meta_next_eligible_at")
        batch.drop_column("meta_fail_count")
        batch.drop_column("meta_verified_at")
        batch.drop_column("meta_fingerprint")
        batch.drop_column("meta_thumbnail_url")
        batch.drop_column("meta_album")
        batch.drop_column("meta_artists")
        batch.drop_column("meta_title")
        batch.drop_column("meta_source_url")
        batch.drop_column("meta_source_id")
        batch.drop_column("meta_source")
        batch.drop_column("meta_status")
