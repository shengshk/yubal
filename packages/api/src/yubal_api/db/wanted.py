"""Wishlist / wanted playlist: tagged tracks without a confirmed ytmid."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


class WantedTrack(SQLModel, table=True):
    """One wishlist entry (tag-complete; video_id only after YTM match → migrate)."""

    __tablename__ = "wanted_tracks"

    id: UUID = Field(default_factory=uuid4, primary_key=True)

    title: str = Field(default="", max_length=500)
    artists: str = Field(default="", max_length=500)
    album: str = Field(default="", max_length=500)

    title_norm: str = Field(default="", max_length=500, index=True)
    artist_norm: str = Field(default="", max_length=500, index=True)
    album_norm: str = Field(default="", max_length=500, index=True)

    # musicbrainz | qq | discogs | lastfm | id_invalid | manual
    source: str = Field(default="manual", max_length=32, index=True)
    source_id: str = Field(default="", max_length=128)
    source_url: str | None = Field(default=None, max_length=2048)
    thumbnail_url: str | None = Field(default=None, max_length=2048)
    duration_seconds: int | None = Field(default=None)

    # Relative path under /data/wanted when a local file is hardlinked in.
    relative_path: str | None = Field(default=None, max_length=1200, index=True)

    # Filled only transiently before migrate-to-Direct; normally NULL.
    video_id: str | None = Field(default=None, max_length=32, index=True)

    match_fail_count: int = Field(default=0)
    match_next_eligible_at: datetime | None = Field(default=None)

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
