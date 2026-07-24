"""Indexed audio files in the local preselect library (A)."""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class PreselectTrack(SQLModel, table=True):
    """One row per audio file under the preselect root (path is PK)."""

    __tablename__ = "preselect_tracks"

    rel_path: str = Field(primary_key=True, max_length=1200)
    mtime_ns: int = Field(default=0)
    size: int = Field(default=0)
    inode: int | None = Field(default=None)

    codec: str = Field(default="", max_length=32)
    sample_rate: int | None = Field(default=None)
    bit_depth: int | None = Field(default=None)
    channels: int | None = Field(default=None)
    duration_ms: int | None = Field(default=None)

    title: str = Field(default="", max_length=500)
    artists: str = Field(default="", max_length=500)
    album: str = Field(default="", max_length=500)
    album_artist: str = Field(default="", max_length=500)
    track_number: int | None = Field(default=None)
    disc_number: int | None = Field(default=None)
    year: str | None = Field(default=None, max_length=16)

    title_norm: str = Field(default="", max_length=500, index=True)
    artist_norm: str = Field(default="", max_length=500, index=True)
    album_norm: str = Field(default="", max_length=500)

    has_lyrics: bool = Field(default=False)
    lyrics_embedded: bool = Field(default=False)
    has_cover: bool = Field(default=False)
    cover_embedded: bool = Field(default=False)

    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
