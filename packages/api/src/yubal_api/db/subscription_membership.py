"""Per-subscription remote membership and synchronization snapshots."""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import Column, Index, String, UniqueConstraint
from sqlmodel import Field, SQLModel


class MembershipStatus(StrEnum):
    ACTIVE = "active"
    OFFLINE = "offline"
    # User-forbidden sync: keep list row, never auto-download until unblocked.
    BLOCKED = "blocked"


class SnapshotStatus(StrEnum):
    RUNNING = "running"
    TRUSTED_COMPLETE = "trusted_complete"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SubscriptionTrack(SQLModel, table=True):
    """One logical reference from a subscription to a remote video."""

    __tablename__ = "subscription_tracks"
    __table_args__ = (
        UniqueConstraint(
            "subscription_id",
            "video_id",
            name="uq_subscription_tracks_subscription_video",
        ),
        Index(
            "ix_subscription_tracks_status_missing",
            "membership_status",
            "missing_since",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    subscription_id: UUID = Field(
        foreign_key="subscriptions.id",
        index=True,
    )
    video_id: str = Field(index=True, max_length=32)
    catalog_video_id: str = Field(index=True, max_length=32)
    title: str = Field(default="", max_length=500)
    artist: str = Field(default="", max_length=500)
    album_artist: str = Field(default="", max_length=500)
    position: int | None = Field(default=None)
    membership_status: MembershipStatus = Field(
        default=MembershipStatus.ACTIVE,
        sa_column=Column(
            String(20),
            nullable=False,
            server_default="active",
            index=True,
        ),
    )
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    missing_since: datetime | None = Field(default=None, index=True)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SubscriptionSyncSnapshot(SQLModel, table=True):
    """Audit row and safety gate for one manual or scheduled synchronization."""

    __tablename__ = "subscription_sync_snapshots"
    __table_args__ = (
        Index(
            "ix_subscription_snapshots_subscription_finished",
            "subscription_id",
            "finished_at",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    subscription_id: UUID = Field(
        foreign_key="subscriptions.id",
        index=True,
    )
    job_id: str | None = Field(default=None, index=True, max_length=64)
    status: SnapshotStatus = Field(
        default=SnapshotStatus.RUNNING,
        sa_column=Column(
            String(24),
            nullable=False,
            server_default="running",
            index=True,
        ),
    )
    authoritative: bool = Field(default=False, index=True)
    source_track_count: int = Field(default=0)
    unavailable_count: int = Field(default=0)
    limited_by_max_items: bool = Field(default=False)
    error_message: str | None = Field(default=None, max_length=2000)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = Field(default=None)
