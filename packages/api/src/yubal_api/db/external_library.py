"""External music library: raw-file index + per-playlist settings."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlmodel import Field, SQLModel

# Match lifecycle for one raw file under External/Raw/<dir_name>/...
MATCH_UNMATCHED = "unmatched"
MATCH_PENDING = "pending"
MATCH_MATCHED = "matched"
MATCH_REJECTED = "rejected"

# Meta-lane (Wanted sources) verification lifecycle.
META_PENDING = "pending"
META_VERIFIED = "verified"
META_REJECTED = "rejected"

EXTERNAL_ACCESS_PENDING = "pending"
EXTERNAL_ACCESS_READONLY = "readonly"
EXTERNAL_ACCESS_MANAGED = "managed"
EXTERNAL_ACCESS_MODES = frozenset(
    {
        EXTERNAL_ACCESS_PENDING,
        EXTERNAL_ACCESS_READONLY,
        EXTERNAL_ACCESS_MANAGED,
    }
)


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
    # New folders are shown immediately but must be deliberately classified
    # before any expensive scan/match pass starts.
    access_mode: str = Field(default=EXTERNAL_ACCESS_PENDING, max_length=16)
    # Access mode remains switchable until Yubal first changes original source
    # content. Hardlinks and newly created sidecars do not count as mutations.
    source_mutated_at: datetime | None = Field(default=None)
    source_mutation_kind: str | None = Field(default=None, max_length=32)
    # Lightweight filename-only inventory for pending folders.  This lets the
    # UI show honest counts and a representative cover before tag scanning.
    discovered_audio_count: int | None = Field(default=None)
    discovered_cover_rel: str | None = Field(default=None, max_length=1200)
    inventory_scanned_at: datetime | None = Field(default=None)
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


class ExternalFileInventory(SQLModel, table=True):
    """Cheap filesystem inventory, deliberately separate from parsed tags."""

    __tablename__ = "external_file_inventory"

    rel_path: str = Field(primary_key=True, max_length=1200)
    dir_name: str = Field(index=True, max_length=255)
    mtime_ns: int = Field(default=0)
    size: int = Field(default=0)
    inode: int | None = Field(default=None)
    # False means the path is known, but its audio tags still need indexing.
    metadata_indexed: bool = Field(default=False, index=True)
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    changed_at: datetime = Field(default_factory=lambda: datetime.now(UTC), index=True)


class ExternalRawTrack(SQLModel, table=True):
    """One indexed audio file under External/Raw (path relative to Raw/)."""

    __tablename__ = "external_raw_tracks"

    rel_path: str = Field(primary_key=True, max_length=1200)
    dir_name: str = Field(index=True, max_length=255)
    # Immutable provenance.  A raw file may later be moved to Raw/Delete, but
    # its permission must still be decided by this original source instead of
    # by the system archive folder it currently lives in.
    origin_kind: str = Field(default="", max_length=32)
    origin_ref: str = Field(default="", max_length=128)

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
    # Null means this exact file/tag state has never completed a real YTM
    # decision. Transport failures deliberately leave it null for later retry.
    ytm_attempted_at: datetime | None = Field(default=None, index=True)

    match_fail_count: int = Field(default=0)
    scrape_fail_count: int = Field(default=0)
    match_next_eligible_at: datetime | None = Field(default=None)
    scrape_next_eligible_at: datetime | None = Field(default=None)

    # Meta-lane verification (Wanted-enabled sources: MusicBrainz/QQ/…).
    # pending = never / stale; verified = hit; rejected = no hit after tries.
    meta_status: str = Field(default="pending", max_length=16, index=True)
    meta_source: str | None = Field(default=None, max_length=32)
    meta_source_id: str | None = Field(default=None, max_length=128)
    meta_source_url: str | None = Field(default=None, max_length=2048)
    meta_title: str | None = Field(default=None, max_length=500)
    meta_artists: str | None = Field(default=None, max_length=500)
    meta_album: str | None = Field(default=None, max_length=500)
    meta_thumbnail_url: str | None = Field(default=None, max_length=2048)
    # Fingerprint of local title|artists|album at verify time; tag edits invalidate.
    meta_fingerprint: str | None = Field(default=None, max_length=600)
    meta_verified_at: datetime | None = Field(default=None)
    # Same contract as ytm_attempted_at for the metadata-verification lane.
    meta_attempted_at: datetime | None = Field(default=None, index=True)
    meta_fail_count: int = Field(default=0)
    meta_next_eligible_at: datetime | None = Field(default=None)

    # Identity guard for scan/dedupe (device+inode signature, best-effort).
    file_key: str | None = Field(default=None, max_length=64)

    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
