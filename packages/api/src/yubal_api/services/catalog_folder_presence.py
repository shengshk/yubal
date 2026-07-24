"""Catalog-backed FolderPresence for per-save-folder download skips."""

from __future__ import annotations

from pathlib import Path

from yubal.utils.library import EXTERNAL_ROOT, STORAGE_EXTERNAL, resolve_under_data

from yubal_api.db.track_catalog_repository import TrackCatalogRepository


class CatalogFolderPresence:
    """Resolve on-disk files via track_locations + is_file check."""

    def __init__(
        self,
        catalog: TrackCatalogRepository,
        data_root: Path,
    ) -> None:
        self._catalog = catalog
        self._data_root = data_root

    def existing_path(self, video_id: str, save_folder: str) -> Path | None:
        if not video_id or not save_folder:
            return None
        loc = self._catalog.get_location(video_id, save_folder)
        if loc is None:
            return None
        folder = save_folder.strip().replace("\\", "/").rstrip("/")
        rel = loc.relative_path.strip().replace("\\", "/")
        root = EXTERNAL_ROOT if loc.storage_root == STORAGE_EXTERNAL else self._data_root
        try:
            path = resolve_under_data(root, f"{folder}/{rel}")
        except ValueError:
            return None
        if path.is_file():
            return path
        return None
