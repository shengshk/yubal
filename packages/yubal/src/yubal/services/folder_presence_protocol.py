"""Protocol for per-save-folder catalog presence checks before download."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class FolderPresence(Protocol):
    """Resolve an on-disk file for (video_id, save_folder)."""

    def existing_path(self, video_id: str, save_folder: str) -> Path | None:
        """Return absolute path when the track exists in folder; else None."""
        ...
