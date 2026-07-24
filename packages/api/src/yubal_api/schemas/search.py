"""Schemas for the ephemeral online-search result card."""

from datetime import datetime

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)


class SearchTrackResponse(BaseModel):
    rank: int
    video_id: str
    title: str
    artist: str
    album: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: int | None = None
    matched: bool = False
    local_path: str | None = None
    preview_cached: bool = False


class SearchSnapshotResponse(BaseModel):
    query: str
    searched_at: datetime
    expires_at: datetime
    total_count: int
    matched_count: int
    cached_count: int = 0
    tracks: list[SearchTrackResponse]


class SearchPreviewResponse(BaseModel):
    video_id: str
    url: str


class SearchDownloadResponse(BaseModel):
    video_id: str
    local_path: str
    snapshot: SearchSnapshotResponse


class SearchLyricsResponse(BaseModel):
    available: bool
    content: str | None = None
    source: str | None = None
