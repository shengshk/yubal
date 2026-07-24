"""Schemas for on-demand track metadata scrape (edit-tags modal)."""

from pydantic import BaseModel, Field


class MetadataSearchRequest(BaseModel):
    """Optional override for the default ``artist title`` query."""

    query: str | None = Field(default=None, max_length=200)


class MetadataCandidate(BaseModel):
    rank: int
    candidate_video_id: str
    title: str
    artist: str
    album: str | None = None
    album_id: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: int | None = None
    title_score: float | None = None
    artist_score: float | None = None


class MetadataSearchResponse(BaseModel):
    query: str
    default_query: str
    candidates: list[MetadataCandidate] = Field(default_factory=list)


class MetadataResolveRequest(BaseModel):
    candidate_video_id: str = Field(min_length=1, max_length=32)
    fetch_lyrics: bool = True


class MetadataSuggestion(BaseModel):
    """Fully enriched suggestion ready to apply into the edit form."""

    candidate_video_id: str
    title: str
    artist: str
    album_artist: str
    album: str
    year: str | None = None
    track_number: int | None = None
    total_tracks: int | None = None
    cover_url: str | None = None
    lyrics: str | None = None
    lyrics_source: str | None = None
    match_result: str = "matched"
    source: str = "album"
