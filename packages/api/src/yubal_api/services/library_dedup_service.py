"""Collapse divergent copies of the same video_id back to a single inode.

A track can accumulate more than one *physical* file for the same
``video_id`` (e.g. a subscription copy created before a Direct copy existed,
or an External ingest that landed before the catalog recorded a canonical
path). ``ensure_single_inode`` finds all locations for a video, keeps the
best-quality file as the winner (preferring the recorded canonical file when
present), and hardlinks every other location onto it so exactly one inode
backs all of that video's catalog rows.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from yubal.utils.library import (
    STORAGE_ROOTS,
    detect_storage_for_path,
    resolve_under_data,
)

from yubal_api.db.track_catalog import TrackLocation
from yubal_api.db.track_catalog_repository import TrackCatalogRepository

logger = logging.getLogger(__name__)


@dataclass
class DedupResult:
    video_id: str
    resolved_locations: int = 0
    distinct_inodes: int = 0
    linked: int = 0
    errors: int = 0


@dataclass
class DedupBatchResult:
    checked: int = 0
    multi_location: int = 0
    linked: int = 0
    errors: int = 0


class LibraryDedupService:
    """Hardlink divergent copies of a video_id back onto one physical file."""

    def __init__(self, catalog: TrackCatalogRepository) -> None:
        self._catalog = catalog

    def collapse_divergent_copies(self) -> DedupBatchResult:
        """Hardlink every video_id that has multiple physical copies.

        Safe no-op when locations already share one inode or hardlink is
        impossible (cross-device). Intended for sync-all / scheduler / playlist sync.
        """
        batch = DedupBatchResult()
        grouped = self._catalog.list_all_by_video_id()
        batch.checked = len(grouped)
        for video_id, rows in grouped.items():
            if len(rows) < 2:
                continue
            batch.multi_location += 1
            result = self.ensure_single_inode(video_id)
            batch.linked += result.linked
            batch.errors += result.errors
        if batch.linked or batch.errors:
            logger.info(
                "Collapsed divergent copies: multi=%d linked=%d errors=%d",
                batch.multi_location,
                batch.linked,
                batch.errors,
            )
        return batch

    def collapse_for_folder(self, save_folder: str) -> DedupBatchResult:
        """Check only IDs touched by one completed folder job."""
        batch = DedupBatchResult()
        video_ids = {
            location.video_id
            for location, _ in self._catalog.list_for_save_folder(save_folder)
        }
        batch.checked = len(video_ids)
        for video_id in video_ids:
            locations = self._catalog.list_locations_for_video(video_id)
            if len(locations) < 2:
                continue
            batch.multi_location += 1
            result = self.ensure_single_inode(video_id)
            batch.linked += result.linked
            batch.errors += result.errors
        return batch

    def _resolve_location_path(self, loc: TrackLocation) -> Path | None:
        root = STORAGE_ROOTS.get(loc.storage_root)
        if root is None:
            return None
        try:
            return resolve_under_data(root, f"{loc.save_folder}/{loc.relative_path}")
        except ValueError:
            return None

    def ensure_single_inode(self, video_id: str) -> DedupResult:
        """Collapse all of a video's locations onto one physical file."""
        locations = self._catalog.list_locations_for_video(video_id)
        record = self._catalog.get_track(video_id)
        if record is not None and record.immutable:
            # Read-only External sources are never replaced, even when another
            # location shares the same YTM ID.
            return DedupResult(video_id=video_id)
        resolved: list[Path] = []
        for loc in locations:
            path = self._resolve_location_path(loc)
            if path is not None and path.is_file():
                resolved.append(path)

        result = DedupResult(video_id=video_id, resolved_locations=len(resolved))
        if len(resolved) < 2:
            result.distinct_inodes = len(resolved)
            return result

        groups: dict[tuple[int, int], list[Path]] = {}
        for path in resolved:
            try:
                st = path.stat()
            except OSError:
                continue
            groups.setdefault((st.st_dev, st.st_ino), []).append(path)

        result.distinct_inodes = len(groups)
        if len(groups) <= 1:
            return result

        winner_key = self._pick_winner_key(video_id, groups)
        winner_path = groups[winner_key][0]

        for key, paths in groups.items():
            if key == winner_key:
                continue
            for path in paths:
                if self._relink(winner_path, path):
                    result.linked += 1
                else:
                    result.errors += 1

        detected = detect_storage_for_path(winner_path)
        if detected is not None:
            storage, rel = detected
            self._catalog.set_canonical(video_id, storage=storage, relative_path=rel)

        return result

    def _pick_winner_key(
        self, video_id: str, groups: dict[tuple[int, int], list[Path]]
    ) -> tuple[int, int]:
        """Prefer the recorded canonical file's inode; else the largest file."""
        canonical = self._catalog.resolve_canonical_path(video_id)
        if canonical is not None:
            try:
                st = canonical.stat()
                canonical_key = (st.st_dev, st.st_ino)
            except OSError:
                canonical_key = None
            if canonical_key is not None and canonical_key in groups:
                return canonical_key

        def group_size(key: tuple[int, int]) -> int:
            try:
                return groups[key][0].stat().st_size
            except OSError:
                return -1

        return max(groups, key=group_size)

    @staticmethod
    def _relink(winner: Path, loser: Path) -> bool:
        """Replace ``loser`` with a hardlink to ``winner`` (best-effort atomic)."""
        if winner.resolve() == loser.resolve():
            return True
        tmp = loser.with_name(f".{loser.name}.dedup-tmp")
        try:
            tmp.unlink(missing_ok=True)
            os.link(winner, tmp)
            os.replace(tmp, loser)
            return True
        except OSError as e:
            logger.warning("Dedup relink failed (%s -> %s): %s", winner, loser, e)
            tmp.unlink(missing_ok=True)
            return False
