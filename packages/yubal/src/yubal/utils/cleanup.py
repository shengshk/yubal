"""Cleanup utilities for partial downloads and temporary files."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def cleanup_part_files(directory: Path) -> int:
    """Remove .part files from a directory tree.

    yt-dlp creates .part files during downloads. If a download is interrupted,
    these partial files should be cleaned up to avoid leaving incomplete data.

    Args:
        directory: Base directory to search for .part files recursively.

    Returns:
        Number of .part files removed.
    """
    cleaned = 0

    try:
        for part_file in directory.rglob("*.part"):
            try:
                part_file.unlink(missing_ok=True)
                cleaned += 1
            except OSError:
                pass  # Best effort cleanup
    except OSError:
        pass  # Directory might not exist

    return cleaned


def cleanup_download_staging(cache_path: Path | None) -> int:
    """Remove abandoned ``yubal-staging`` trees under the download cache.

    Staging is only used while a download job is running. Leftovers after a
    crash/restart are safe to wipe entirely; the next job recreates the tree.
    """
    if cache_path is None:
        return 0
    staging = cache_path / "yubal-staging"
    if not staging.exists():
        return 0
    removed = 0
    try:
        for path in staging.rglob("*"):
            if path.is_file():
                removed += 1
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("Could not clean download staging %s: %s", staging, e)
        return 0
    return removed


def cleanup_startup_temps(
    data_path: Path, cache_path: Path | None = None
) -> dict[str, int]:
    """Startup sweep for leftover ``.part`` files and abandoned staging."""
    parts = cleanup_part_files(data_path)
    if cache_path is not None and cache_path.resolve() != data_path.resolve():
        parts += cleanup_part_files(cache_path)
    staging = cleanup_download_staging(cache_path)
    return {"part_files": parts, "staging_files": staging}
