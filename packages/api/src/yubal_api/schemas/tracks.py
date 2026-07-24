"""API schemas for track tag editing."""

from pydantic import BaseModel, Field


class TrackTagUpdate(BaseModel):
    """User-editable music tags (video_id is immutable)."""

    title: str | None = Field(default=None, min_length=1, max_length=500)
    artist: str | None = Field(default=None, min_length=1, max_length=500)
    artists: list[str] | None = None
    album_artist: str | None = Field(default=None, min_length=1, max_length=500)
    album_artists: list[str] | None = None
    album: str | None = Field(default=None, min_length=1, max_length=500)
    year: str | None = Field(default=None, max_length=16)
    track_number: int | None = Field(default=None, ge=1, le=999)
    # Scrape apply extras (optional; omitted fields are left alone).
    # Large ceiling accommodates ``data:`` URLs from browser cover uploads.
    cover_url: str | None = Field(default=None, max_length=12_000_000)
    refresh_cover: bool = False
    lyrics: str | None = Field(default=None, max_length=500_000)


class TrackLocationUpdate(BaseModel):
    save_folder: str
    old_relative_path: str
    new_relative_path: str


class TrackTagUpdateResponse(BaseModel):
    video_id: str
    title: str
    artist: str
    album_artist: str
    album: str
    year: str | None = None
    track_number: int | None = None
    cover_url: str | None = None
    lyrics_applied: bool = False
    cover_applied: bool = False
    locations: list[TrackLocationUpdate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
