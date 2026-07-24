"""Sync ledger API schemas."""

from uuid import UUID

from pydantic import BaseModel, Field

from yubal_api.db.sync_ledger import LedgerKind
from yubal_api.schemas.types import UTCDateTime


class SyncLedgerResponse(BaseModel):
    """One sync-center ledger row."""

    id: UUID
    key: str
    kind: LedgerKind
    subscription_id: UUID | None = None
    save_folder: str
    title: str
    thumbnail_url: str | None = Field(default=None, json_schema_extra={"format": "uri"})
    content_kind: str
    url: str | None = None
    total_count: int
    synced_count: int
    real_download_count: int
    hardlink_count: int
    failed_count: int
    skipped_ugc: int
    skipped_region: int
    skipped_other: int
    last_job_id: str | None = None
    last_job_status: str | None = None
    last_synced_at: UTCDateTime | None = None
    updated_at: UTCDateTime
    # Direct recover policy (populated for kind=direct from preferences).
    enabled: bool | None = None
    max_items: int | None = None
    sync_jitter_seconds: int | None = None
    offline_marking_enabled: bool | None = None
    offline_cleanup_enabled: bool | None = None
    offline_cleanup_action: str | None = None
    offline_cleanup_delay_hours: int | None = None
    offline_count: int | None = None
    blocked_count: int | None = None

    model_config = {"from_attributes": True}


class SyncLedgerListResponse(BaseModel):
    """List of ledger rows."""

    items: list[SyncLedgerResponse]


class SyncTrackItem(BaseModel):
    """One track under a save folder (for Sync Center expand)."""

    index: int
    title: str
    artist: str | None = None
    album_artist: str | None = None
    display_label: str | None = None
    exists: bool = True
    storage: str = "real"  # real | hardlink | missing
    relative_path: str = ""
    video_id: str | None = None
    cover_url: str | None = None
    album: str | None = None
    year: str | None = None
    track_number: int | None = None
    # Derived quality tier (draft | complete | premium); None when unknown.
    tier: str | None = None
    has_embedded_cover: bool = False
    has_lyrics: bool = False
    has_synced_lyrics: bool = False
    # Provenance of the embedded cover (apple | ytm | embedded); None if unknown.
    cover_source: str | None = None
    # Direct list membership (active | offline); null for subscription disks scans.
    membership_status: str | None = None


class SyncTrackListResponse(BaseModel):
    """Tracks listed for a playlist / Direct folder."""

    save_folder: str
    total: int
    items: list[SyncTrackItem]
