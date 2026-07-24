"""Cross-folder hardlink classification for library stats.

Product rule (all playlist surfaces share this):

  A local file under save_folder F counts as **hardlink** iff
  ``st_nlink > 1`` **and** another catalog location for the same
  ``video_id`` (different ``save_folder``) resolves to the same
  ``(st_dev, st_ino)``.

Raw↔Organized links alone never count (Raw is not a catalog location).
A copy into Direct after a failed cross-mount link must show exclusive
on both sides — even when Organized still shares an inode with Raw and
the Download catalog row exists.
"""

from __future__ import annotations

from pathlib import Path

from yubal.utils import library as library_utils
from yubal.utils.library import STORAGE_EXTERNAL, inode_key

from yubal_api.db.track_catalog import TrackLocation
from yubal_api.db.track_catalog_repository import TrackCatalogRepository


def location_abs_path(loc: TrackLocation, *, download_root: Path) -> Path:
    """Absolute path for a catalog location under Download or External."""
    if loc.storage_root == STORAGE_EXTERNAL:
        root = library_utils.STORAGE_ROOTS.get(
            STORAGE_EXTERNAL, library_utils.EXTERNAL_ROOT
        )
    else:
        root = download_root
    return root / loc.save_folder / loc.relative_path


def is_cross_folder_hardlink(
    abs_path: Path,
    *,
    video_id: str,
    save_folder: str,
    catalog: TrackCatalogRepository,
    download_root: Path,
    location_inode_cache: dict[str, list[tuple[str, tuple[int, int]]]]
    | None = None,
) -> bool:
    """True when ``abs_path`` shares an inode with another catalog folder."""
    key = inode_key(abs_path)
    if key is None:
        return False
    try:
        if abs_path.stat().st_nlink <= 1:
            return False
    except OSError:
        return False

    folder = (save_folder or "").strip().replace("\\", "/")
    for other_folder, other_key in _location_inodes(
        video_id,
        catalog=catalog,
        download_root=download_root,
        cache=location_inode_cache,
    ):
        if other_folder != folder and other_key == key:
            return True
    return False


def classify_catalog_file(
    abs_path: Path,
    *,
    video_id: str,
    save_folder: str,
    catalog: TrackCatalogRepository,
    download_root: Path,
    location_inode_cache: dict[str, list[tuple[str, tuple[int, int]]]]
    | None = None,
) -> str:
    """Return ``hardlink`` or ``real`` for UI / ledger storage labels."""
    if not abs_path.is_file():
        return "missing"
    if is_cross_folder_hardlink(
        abs_path,
        video_id=video_id,
        save_folder=save_folder,
        catalog=catalog,
        download_root=download_root,
        location_inode_cache=location_inode_cache,
    ):
        return "hardlink"
    return "real"


def count_hardlinks_for_folder(
    catalog: TrackCatalogRepository,
    save_folder: str,
    *,
    download_root: Path,
) -> tuple[int, int]:
    """Return ``(local_files, hardlink_count)`` for catalog rows in ``save_folder``."""
    folder = (save_folder or "").strip().replace("\\", "/")
    local = hard = 0
    cache: dict[str, list[tuple[str, tuple[int, int]]]] = {}
    for loc, _rec in catalog.list_for_save_folder(folder):
        path = location_abs_path(loc, download_root=download_root)
        if not path.is_file():
            continue
        local += 1
        if is_cross_folder_hardlink(
            path,
            video_id=loc.video_id,
            save_folder=folder,
            catalog=catalog,
            download_root=download_root,
            location_inode_cache=cache,
        ):
            hard += 1
    return local, hard


def _location_inodes(
    video_id: str,
    *,
    catalog: TrackCatalogRepository,
    download_root: Path,
    cache: dict[str, list[tuple[str, tuple[int, int]]]] | None,
) -> list[tuple[str, tuple[int, int]]]:
    """``(save_folder, inode_key)`` for every on-disk catalog location of ``video_id``."""
    if cache is not None and video_id in cache:
        return cache[video_id]

    rows: list[tuple[str, tuple[int, int]]] = []
    for loc in catalog.list_locations_for_video(video_id):
        folder = (loc.save_folder or "").strip().replace("\\", "/")
        path = location_abs_path(loc, download_root=download_root)
        key = inode_key(path)
        if key is not None:
            rows.append((folder, key))
    if cache is not None:
        cache[video_id] = rows
    return rows
