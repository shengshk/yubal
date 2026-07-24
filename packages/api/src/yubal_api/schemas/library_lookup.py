"""Schemas for Enter-key local library quick lookup (operation B)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LibraryLocationHit(BaseModel):
    """One playlist / folder the track belongs to (excluding Direct)."""

    kind: Literal["subscription", "external"]
    expand_key: str
    title: str
    enabled: bool | None = None


class TrackPresenceResponse(BaseModel):
    video_id: str
    title: str | None = None
    artist: str | None = None
    in_direct: bool = False
    locations: list[LibraryLocationHit] = Field(default_factory=list)


class PlaylistPresenceResponse(BaseModel):
    url: str
    subscription: LibraryLocationHit | None = None
    in_direct_url: bool = False


class TextMatchHit(BaseModel):
    video_id: str
    title: str
    artist: str
    in_direct: bool = False
    locations: list[LibraryLocationHit] = Field(default_factory=list)


class TextPresenceResponse(BaseModel):
    query: str
    matches: list[TextMatchHit] = Field(default_factory=list)
