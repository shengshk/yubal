"""Subscription request/response schemas."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from yubal_api.db.subscription import (
    OfflineCleanupAction,
    SubscriptionSyncMode,
    SubscriptionType,
)
from yubal_api.db.subscription_membership import MembershipStatus
from yubal_api.schemas.jobs import YouTubeMusicUrl
from yubal_api.schemas.types import UTCDateTime


class SubscriptionCreate(BaseModel):
    """Request to create a subscription."""

    url: YouTubeMusicUrl
    max_items: int | None = Field(default=None, ge=1, le=10000)


class SubscriptionUpdate(BaseModel):
    """Request to update a subscription."""

    enabled: bool | None = None
    save_folder: str | None = Field(default=None, min_length=1, max_length=400)
    max_items: int | None = Field(default=None, ge=1, le=10000)
    sync_jitter_seconds: int | None = Field(default=None, ge=0, le=600)
    sync_mode: SubscriptionSyncMode | None = None
    offline_marking_enabled: bool | None = None
    offline_cleanup_enabled: bool | None = None
    offline_cleanup_action: Literal["delete", "archive"] | None = None
    offline_cleanup_delay_hours: int | None = Field(
        default=None,
        ge=0,
        le=8760,
    )
    id_invalid_marking_enabled: bool | None = None
    id_invalid_cleanup_enabled: bool | None = None
    id_invalid_cleanup_action: OfflineCleanupAction | None = None
    id_invalid_cleanup_delay_hours: int | None = Field(
        default=None,
        ge=0,
        le=8760,
    )
    confirm_folder_move: bool = False


class SubscriptionDeleteRequest(BaseModel):
    """How to handle on-disk files when deleting a subscription."""

    file_action: Literal["keep", "delete", "move_to_direct"] = "keep"


class SubscriptionResponse(BaseModel):
    """Subscription response."""

    id: UUID
    type: SubscriptionType
    url: str = Field(json_schema_extra={"format": "uri"})
    name: str
    save_folder: str
    enabled: bool
    max_items: int | None
    sync_jitter_seconds: int = 600
    sync_mode: SubscriptionSyncMode = SubscriptionSyncMode.INCREMENTAL
    offline_marking_enabled: bool = True
    offline_cleanup_enabled: bool = False
    offline_cleanup_action: Literal["delete", "archive"] = "archive"
    offline_cleanup_delay_hours: int = 72
    id_invalid_marking_enabled: bool = True
    id_invalid_cleanup_enabled: bool = False
    id_invalid_cleanup_action: OfflineCleanupAction = OfflineCleanupAction.ARCHIVE
    id_invalid_cleanup_delay_hours: int = 72
    thumbnail_url: str | None = Field(default=None, json_schema_extra={"format": "uri"})
    created_at: UTCDateTime
    last_synced_at: UTCDateTime | None

    model_config = {"from_attributes": True}


class SubscriptionListResponse(BaseModel):
    """List of subscriptions response."""

    items: list[SubscriptionResponse]


class SyncResponse(BaseModel):
    """Response for sync operations."""

    job_ids: list[str]
    steps: list["SyncStepResult"] = Field(default_factory=list)


class LikedSongRatingRequest(BaseModel):
    """Requested remote thumbs-up state for an already identified song."""

    liked: bool


class SyncStepResult(BaseModel):
    """Immediate Sync All pipeline step result."""

    key: Literal[
        "health",
        "subscriptions",
        "direct",
        "enrichment",
        "external",
        "wanted",
        "hardlinks",
    ]
    status: Literal["complete", "queued", "started", "skipped", "failed"]
    count: int | None = None


class SubscriptionTrackResponse(BaseModel):
    """One subscription membership row."""

    id: UUID
    subscription_id: UUID
    video_id: str
    catalog_video_id: str
    title: str
    artist: str
    album_artist: str
    position: int | None = None
    membership_status: MembershipStatus
    first_seen_at: UTCDateTime
    last_seen_at: UTCDateTime
    missing_since: UTCDateTime | None = None
    updated_at: UTCDateTime

    model_config = {"from_attributes": True}


class SubscriptionTrackListResponse(BaseModel):
    items: list[SubscriptionTrackResponse]


class SubscriptionTrackDisposeRequest(BaseModel):
    action: Literal["delete", "archive", "to_wanted"] = "delete"


class SubscriptionTrackFileDeleteRequest(BaseModel):
    """Delete local file while keeping membership (optionally blacklist sync)."""

    block: bool = False


class SubscriptionTrackDisposeResponse(BaseModel):
    video_id: str
    action: str
    path: str | None = None
    kept_reason: str | None = None
    membership_status: MembershipStatus | None = None
