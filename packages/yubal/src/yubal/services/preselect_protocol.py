"""Protocol for local preselect-library lookup (A → B before yt-dlp)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from yubal.models.track import TrackMetadata

PreselectPlaceMode = Literal["link", "copy"]


@dataclass(frozen=True, slots=True)
class PreselectHit:
    """A matched file in the preselect library ready to place into B."""

    source_path: Path
    mode: PreselectPlaceMode


class PreselectSource(Protocol):
    """Lookup best preselect file for a YTM track (no disk walk)."""

    def lookup(self, track: TrackMetadata) -> PreselectHit | None:
        """Return a hit or None when no confident match."""
        ...
