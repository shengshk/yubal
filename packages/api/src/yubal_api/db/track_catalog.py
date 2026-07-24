"""Canonical track facts + per-folder hardlink locations."""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import Column, String
from sqlmodel import Field, SQLModel


class LocationMembershipStatus(StrEnum):
    ACTIVE = "active"
    OFFLINE = "offline"
    # Direct: user banned auto-recover (禁止回补). Not a global download ban.
    BLOCKED = "blocked"


class TrackRecord(SQLModel, table=True):
    """One row per YouTube Music video_id (shared across hardlink paths)."""

    __tablename__ = "tracks"

    video_id: str = Field(primary_key=True, max_length=32)
    title: str = Field(max_length=500)
    artist: str = Field(max_length=500)
    album_artist: str = Field(max_length=500)
    album: str = Field(default="", max_length=500)
    track_number: int | None = Field(default=None)
    year: str | None = Field(default=None, max_length=16)
    cover_url: str | None = Field(default=None, max_length=2048)
    lyrics: str | None = Field(default=None)
    has_embedded_cover: bool = Field(default=False)
    has_lyrics_embedded: bool = Field(default=False)
    has_lyrics_sidecar: bool = Field(default=False)
    # Resolved cover provenance (apple | ytm | embedded); display / debugging.
    # Premium tier is derived from comparison freshness + optional excellence px.
    cover_source: str | None = Field(default=None, max_length=16)
    # Provider that produced the lyrics (lrclib | ytm | qq | manual | embedded).
    lyrics_source: str | None = Field(default=None, max_length=16)
    # Last successful/attempted library enrichment pass and its error (if any).
    last_enriched_at: datetime | None = Field(default=None)
    last_enrich_error: str | None = Field(default=None, max_length=2000)
    # True when the canonical file lives on a read-only external source
    # (allow_mutate=False playlist); retag/enrichment must refuse writes.
    immutable: bool = Field(default=False)
    # External playlist hukou (playlist_uid). Follows the track + hardlinks for life
    # until the origin playlist is unregistered or the track is liberated.
    origin_playlist_uid: str | None = Field(
        default=None, max_length=36, index=True
    )
    # Storage root ("download" | "external") + relative path of the single
    # canonical physical file other locations should hardlink from.
    canonical_storage: str | None = Field(default=None, max_length=16)
    canonical_rel: str | None = Field(default=None, max_length=1200)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TrackLocation(SQLModel, table=True):
    """One hardlink / copy path under a save folder."""

    __tablename__ = "track_locations"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    video_id: str = Field(index=True, max_length=32, foreign_key="tracks.video_id")
    save_folder: str = Field(index=True, max_length=200)
    relative_path: str = Field(max_length=1000)
    origin: str = Field(default="download", max_length=32)
    # Which library root this location lives under ("download" | "external").
    storage_root: str = Field(default="download", max_length=16)
    # Direct list membership: active = recoverable; offline = YTM gone;
    # blocked = user banned auto-recover. Subscription folders use their own
    # membership table for block/offline instead.
    membership_status: LocationMembershipStatus = Field(
        default=LocationMembershipStatus.ACTIVE,
        sa_column=Column(
            String(20),
            nullable=False,
            server_default="active",
            index=True,
        ),
    )
    missing_since: datetime | None = Field(default=None, index=True)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
