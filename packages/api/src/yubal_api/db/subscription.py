"""Database models."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import TypedDict
from uuid import UUID, uuid4

from sqlalchemy import Column, String
from sqlmodel import Field, SQLModel


class SubscriptionType(StrEnum):
    """Type of content subscription."""

    PLAYLIST = "playlist"
    # ARTIST = "artist"  # future


class SubscriptionSyncMode(StrEnum):
    """How a subscription handles tracks removed from the remote playlist."""

    INCREMENTAL = "incremental"
    MIRROR = "mirror"


class OfflineCleanupAction(StrEnum):
    """Automatic action for incremental tracks that remain offline."""

    DELETE = "delete"
    ARCHIVE = "archive"


class SubscriptionFields(TypedDict, total=False):
    """Partial update fields for a subscription."""

    enabled: bool
    name: str
    thumbnail_url: str | None
    last_synced_at: datetime
    save_folder: str
    max_items: int | None
    sync_jitter_seconds: int
    sync_mode: SubscriptionSyncMode
    offline_marking_enabled: bool
    offline_cleanup_enabled: bool
    offline_cleanup_action: OfflineCleanupAction
    offline_cleanup_delay_hours: int


class Subscription(SQLModel, table=True):
    """A subscription to sync content from YouTube Music."""

    __tablename__ = "subscriptions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    type: SubscriptionType = Field(index=True)
    url: str = Field(unique=True, index=True)
    name: str = Field(max_length=200)
    save_folder: str | None = Field(default=None, max_length=200)
    enabled: bool = Field(default=True)
    max_items: int | None = Field(default=None, ge=1, le=10000)
    # Max |offset| in seconds: each fire uses random in [-N, +N] (clamped to cron interval)
    sync_jitter_seconds: int = Field(default=600, ge=0, le=600)
    thumbnail_url: str | None = Field(default=None, max_length=2048)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_synced_at: datetime | None = Field(default=None)
    sync_mode: SubscriptionSyncMode = Field(
        default=SubscriptionSyncMode.INCREMENTAL,
        sa_column=Column(
            String(20),
            nullable=False,
            server_default="incremental",
            index=True,
        ),
    )
    offline_marking_enabled: bool = Field(default=True)
    offline_cleanup_enabled: bool = Field(default=False)
    offline_cleanup_action: OfflineCleanupAction = Field(
        default=OfflineCleanupAction.ARCHIVE,
        sa_column=Column(
            String(20),
            nullable=False,
            server_default="archive",
        ),
    )
    offline_cleanup_delay_hours: int = Field(default=72, ge=0, le=8760)
