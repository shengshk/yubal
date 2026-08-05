"""Schemas for the wishlist / wanted playlist."""

from datetime import datetime

from pydantic import BaseModel, Field


class WantedAddRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    artists: str = Field(min_length=1, max_length=500)
    album: str = Field(default="", max_length=500)
    source: str = Field(default="manual", max_length=32)
    source_id: str = Field(default="", max_length=128)
    source_url: str | None = Field(default=None, max_length=2048)
    thumbnail_url: str | None = Field(default=None, max_length=2048)
    duration_seconds: int | None = None


class WantedTrackResponse(BaseModel):
    id: str
    display_index: str | None = None
    title: str
    artists: str
    album: str | None = None
    source: str
    source_id: str | None = None
    source_url: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: int | None = None
    relative_path: str | None = None
    has_file: bool = False
    video_id: str | None = None
    created_at: datetime
    updated_at: datetime


class WantedSummary(BaseModel):
    total_count: int
    local_heart_count: int
    recovery_count: int
    matched_file_count: int
    unmatched_count: int
    exclusive_count: int = 0
    shared_count: int = 0
    hardlink_count: int = 0
    enabled: bool
    auto_match_enabled: bool
    last_matched_at: datetime | None = None
    last_job_status: str | None = None


class WantedDeleteRequest(BaseModel):
    mode: str = Field(
        description="remove | wipe_list | to_raw_delete",
    )


class WantedPlaylistDeleteRequest(BaseModel):
    mode: str = Field(
        description="wipe_list | to_raw_delete",
    )
