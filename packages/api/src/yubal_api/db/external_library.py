"""External music library: raw-file index + per-playlist settings."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel

# Match lifecycle for one raw file under External/Raw/<dir_name>/...
MATCH_UNMATCHED = "unmatched"
MATCH_PENDING = "pending"
MATCH_MATCHED = "matched"
MATCH_REJECTED = "rejected"


class ExternalPlaylist(SQLModel, table=True):
    """One top-level directory under External/Raw (a user's existing playlist)."""

    __tablename__ = "external_playlists"

    dir_name: str = Field(primary_key=True, max_length=255)
    # Stable hukou id — survives folder rename detection gaps; stamped onto tracks.
    playlist_uid: str = Field(
        default_factory=lambda: str(uuid4()),
        max_length=36,
        index=True,
        unique=True,
    )
    # False = read-only mount: no tag edits; missing critical tags skip match.
    allow_mutate: bool = Field(default=False)
    show_raw: bool = Field(default=True)
    # Junk is a subset of unmatched; only meaningful when show_raw is True.
    show_junk: bool = Field(default=True)
    # Sync policy (aligned with Direct / subscription cards).
    enabled: bool = Field(default=False)
    max_items: int = Field(default=50)
    sync_jitter_seconds: int = Field(default=600)
    offline_marking_enabled: bool = Field(default=True)
    # ID-invalid auto-clean (aligned with subscription offline cleanup UX;
    # archive = move to Raw/Delete).
    offline_cleanup_enabled: bool = Field(default=False)
    offline_cleanup_action: str = Field(default="archive", max_length=16)
    offline_cleanup_delay_hours: int = Field(default=72)
    last_synced_at: datetime | None = Field(default=None)
    last_sync_status: str | None = Field(default=None, max_length=32)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExternalRawTrack(SQLModel, table=True):
    """One indexed audio file under External/Raw (path relative to Raw/)."""

    __tablename__ = "external_raw_tracks"

    rel_path: str = Field(primary_key=True, max_length=1200)
    dir_name: str = Field(index=True, max_length=255)

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

    # Match lifecycle against YouTube Music.
    video_id: str | None = Field(default=None, max_length=32, index=True)
    match_status: str = Field(default=MATCH_UNMATCHED, max_length=16, index=True)
    match_confidence: float | None = Field(default=None)

    match_fail_count: int = Field(default=0)
    scrape_fail_count: int = Field(default=0)
    match_next_eligible_at: datetime | None = Field(default=None)
    scrape_next_eligible_at: datetime | None = Field(default=None)

    # Identity guard for scan/dedupe (device+inode signature, best-effort).
    file_key: str | None = Field(default=None, max_length=64)

    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
