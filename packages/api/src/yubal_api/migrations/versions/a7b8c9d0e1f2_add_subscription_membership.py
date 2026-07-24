"""Add subscription sync policy, membership and trusted snapshots.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("subscriptions") as batch:
        batch.add_column(
            sa.Column(
                "sync_mode",
                sa.String(length=20),
                nullable=False,
                server_default="incremental",
            )
        )
        batch.add_column(
            sa.Column(
                "offline_marking_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(
            sa.Column(
                "offline_cleanup_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column(
                "offline_cleanup_action",
                sa.String(length=20),
                nullable=False,
                server_default="archive",
            )
        )
        batch.add_column(
            sa.Column(
                "offline_cleanup_delay_hours",
                sa.Integer(),
                nullable=False,
                server_default="72",
            )
        )
        batch.create_index("ix_subscriptions_sync_mode", ["sync_mode"])

    op.create_table(
        "subscription_tracks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("video_id", sa.String(length=32), nullable=False),
        sa.Column("catalog_video_id", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("artist", sa.String(length=500), nullable=False, server_default=""),
        sa.Column(
            "album_artist",
            sa.String(length=500),
            nullable=False,
            server_default="",
        ),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column(
            "membership_status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
        ),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("missing_since", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "subscription_id",
            "video_id",
            name="uq_subscription_tracks_subscription_video",
        ),
    )
    op.create_index(
        "ix_subscription_tracks_subscription_id",
        "subscription_tracks",
        ["subscription_id"],
    )
    op.create_index(
        "ix_subscription_tracks_video_id",
        "subscription_tracks",
        ["video_id"],
    )
    op.create_index(
        "ix_subscription_tracks_catalog_video_id",
        "subscription_tracks",
        ["catalog_video_id"],
    )
    op.create_index(
        "ix_subscription_tracks_membership_status",
        "subscription_tracks",
        ["membership_status"],
    )
    op.create_index(
        "ix_subscription_tracks_missing_since",
        "subscription_tracks",
        ["missing_since"],
    )
    op.create_index(
        "ix_subscription_tracks_status_missing",
        "subscription_tracks",
        ["membership_status", "missing_since"],
    )

    op.create_table(
        "subscription_sync_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default="running",
        ),
        sa.Column(
            "authoritative",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "source_track_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "unavailable_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "limited_by_max_items",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_subscription_sync_snapshots_subscription_id",
        "subscription_sync_snapshots",
        ["subscription_id"],
    )
    op.create_index(
        "ix_subscription_sync_snapshots_job_id",
        "subscription_sync_snapshots",
        ["job_id"],
    )
    op.create_index(
        "ix_subscription_sync_snapshots_status",
        "subscription_sync_snapshots",
        ["status"],
    )
    op.create_index(
        "ix_subscription_sync_snapshots_authoritative",
        "subscription_sync_snapshots",
        ["authoritative"],
    )
    op.create_index(
        "ix_subscription_snapshots_subscription_finished",
        "subscription_sync_snapshots",
        ["subscription_id", "finished_at"],
    )


def downgrade() -> None:
    op.drop_table("subscription_sync_snapshots")
    op.drop_table("subscription_tracks")
    with op.batch_alter_table("subscriptions") as batch:
        batch.drop_index("ix_subscriptions_sync_mode")
        batch.drop_column("offline_cleanup_delay_hours")
        batch.drop_column("offline_cleanup_action")
        batch.drop_column("offline_cleanup_enabled")
        batch.drop_column("offline_marking_enabled")
        batch.drop_column("sync_mode")
