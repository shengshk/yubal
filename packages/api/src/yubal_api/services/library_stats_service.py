"""Whole-library physical audio statistics."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from yubal.utils.library import (
    AUDIO_SUFFIXES,
    EXTERNAL_ORGANIZED_DIR,
    EXTERNAL_RAW_DIR,
    STORAGE_DOWNLOAD,
    STORAGE_EXTERNAL,
    STORAGE_WANTED,
)

from yubal_api.db.external_library import META_VERIFIED
from yubal_api.db.external_library_repository import ExternalLibraryRepository
from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.db.wanted_repository import WantedRepository

InodeKey = tuple[int, int]
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LibraryTrackSummary:
    """Mutually exclusive counts over unique physical audio files."""

    effective_count: int
    identified_count: int
    unidentified_count: int
    verified_count: int
    unverified_count: int
    physical_count: int
    hardlink_duplicate_count: int


@dataclass(frozen=True)
class LibraryAudit:
    """Manual consistency audit over files and catalog references."""

    physical_count: int
    hardlink_duplicate_count: int
    catalog_location_count: int
    missing_catalog_locations: int
    repaired_catalog_locations: int
    untracked_physical_count: int

    @property
    def ok(self) -> bool:
        return (
            self.missing_catalog_locations == 0 and self.untracked_physical_count == 0
        )


class LibraryStatsService:
    """Count real audio files once per device/inode across all library roots."""

    def __init__(
        self,
        *,
        catalog: TrackCatalogRepository,
        external: ExternalLibraryRepository,
        wanted: WantedRepository,
        download_root: Path,
        external_root: Path,
        wanted_root: Path,
        state_path: Path | None = None,
    ) -> None:
        self._catalog = catalog
        self._external = external
        self._wanted = wanted
        self._download_root = download_root
        self._external_root = external_root
        self._wanted_root = wanted_root
        self._storage_roots = {
            STORAGE_DOWNLOAD: download_root,
            STORAGE_EXTERNAL: external_root,
            STORAGE_WANTED: wanted_root,
        }
        self._summary_lock = threading.Lock()
        self._summary_scan_lock = threading.Lock()
        self._summary_generation = 0
        self._summary_cached_at = 0.0
        self._summary_cache: LibraryTrackSummary | None = None
        self._summary_refreshing = False
        self._summary_dirty = False
        self._state_path = state_path
        self._load_summary()

    def _load_summary(self) -> None:
        if self._state_path is None or not self._state_path.is_file():
            return
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._summary_cache = LibraryTrackSummary(
                effective_count=int(payload["effective_count"]),
                identified_count=int(payload["identified_count"]),
                unidentified_count=int(payload["unidentified_count"]),
                verified_count=int(payload["verified_count"]),
                unverified_count=int(payload["unverified_count"]),
                physical_count=int(payload["physical_count"]),
                hardlink_duplicate_count=int(payload["hardlink_duplicate_count"]),
            )
            # Snapshots created before the persistent dirty marker have no
            # reliable mutation history. Refresh them once on their next read
            # (never during startup) to establish the new ledger contract.
            self._summary_dirty = "dirty" not in payload or bool(
                payload.get("dirty", False)
            )
            # The persisted snapshot is the statistics ledger.  It remains
            # usable across restarts; a full inode walk is only triggered by
            # an explicit refresh or when no snapshot exists yet.
            self._summary_cached_at = time.monotonic()
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            logger.warning("Could not load library summary snapshot", exc_info=True)

    def _persist_summary(
        self,
        summary: LibraryTrackSummary,
        *,
        dirty: bool,
    ) -> None:
        if self._state_path is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._state_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {**asdict(summary), "dirty": dirty},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            temporary.replace(self._state_path)
        except OSError:
            logger.warning("Could not persist library summary snapshot", exc_info=True)

    @staticmethod
    def _inode(path: Path) -> InodeKey | None:
        try:
            if not path.is_file() or path.suffix.lower() not in AUDIO_SUFFIXES:
                return None
            stat = path.stat()
            return stat.st_dev, stat.st_ino
        except OSError:
            return None

    def _scan_root(self, root: Path, inodes: set[InodeKey]) -> int:
        """Add audio inodes under one root and return its path count."""
        path_count = 0
        if not root.is_dir():
            return path_count
        try:
            for path in root.rglob("*"):
                key = self._inode(path)
                if key is None:
                    continue
                path_count += 1
                inodes.add(key)
        except OSError:
            pass
        return path_count

    def _scan_audio(
        self,
        *,
        use_external_inventory: bool = False,
    ) -> tuple[set[InodeKey], int]:
        inodes: set[InodeKey] = set()
        path_count = self._scan_root(self._download_root, inodes)
        path_count += self._scan_root(self._wanted_root, inodes)

        if not use_external_inventory:
            path_count += self._scan_root(self._external_root, inodes)
            return inodes, path_count

        # Full external maintenance already walked Raw and persisted every
        # path/inode. Reuse that last-good ledger instead of traversing the
        # same large SMB tree again merely to update the statistics card.
        raw_root = self._external_root / EXTERNAL_RAW_DIR
        try:
            raw_device = raw_root.stat().st_dev
        except OSError:
            raw_device = None
        for rel_path, inode in self._external.list_inventory_inodes():
            key = (
                (raw_device, inode)
                if raw_device is not None and inode is not None
                else self._inode(raw_root / rel_path)
            )
            if key is None:
                continue
            path_count += 1
            inodes.add(key)

        # Organized is substantially smaller and is not part of the Raw
        # inventory, so it still receives an exact inode walk.
        path_count += self._scan_root(
            self._external_root / EXTERNAL_ORGANIZED_DIR,
            inodes,
        )
        return inodes, path_count

    def _catalog_identified(self, present: set[InodeKey]) -> set[InodeKey]:
        identified: set[InodeKey] = set()
        for rows in self._catalog.list_all_by_video_id().values():
            for location, _record in rows:
                root = self._storage_roots.get(location.storage_root)
                if root is None:
                    continue
                key = self._inode(root / location.save_folder / location.relative_path)
                if key in present:
                    identified.add(key)
        return identified

    def _raw_status(
        self,
        present: set[InodeKey],
    ) -> tuple[set[InodeKey], set[InodeKey]]:
        identified: set[InodeKey] = set()
        verified: set[InodeKey] = set()
        raw_root = self._external_root / EXTERNAL_RAW_DIR
        try:
            raw_device = raw_root.stat().st_dev
        except OSError:
            raw_device = None
        inventory_inodes = dict(self._external.list_inventory_inodes())
        for playlist in self._external.list_playlists():
            for row in self._external.list_for_dir(playlist.dir_name):
                inode = inventory_inodes.get(
                    row.rel_path,
                    getattr(row, "inode", None),
                )
                key = (
                    (raw_device, int(inode))
                    if raw_device is not None and inode is not None
                    else self._inode(raw_root / row.rel_path)
                )
                if key not in present:
                    continue
                if row.video_id:
                    identified.add(key)
                elif row.meta_status == META_VERIFIED:
                    verified.add(key)
        return identified, verified

    def _wanted_status(
        self,
        present: set[InodeKey],
    ) -> tuple[set[InodeKey], set[InodeKey]]:
        identified: set[InodeKey] = set()
        verified: set[InodeKey] = set()
        for row in self._wanted.list_all():
            if not row.relative_path:
                continue
            key = self._inode(self._wanted_root / row.relative_path)
            if key not in present:
                continue
            if row.video_id:
                identified.add(key)
            elif (
                row.source_id
                and row.source
                and row.source not in {"manual", "id_invalid"}
            ):
                verified.add(key)
        return identified, verified

    def summary(self, *, force: bool = False) -> LibraryTrackSummary:
        """Return the persisted physical totals unless explicitly refreshed."""
        return self._summary_from_disk(force=force)

    def _summary_from_disk(self, *, force: bool) -> LibraryTrackSummary:
        """Build one exact summary while coalescing concurrent scan requests."""
        with self._summary_lock:
            started_cached_at = self._summary_cached_at
            if (
                not force
                and self._summary_cache is not None
                and not self._summary_dirty
            ):
                return self._summary_cache

        # Never let a page refresh and scheduled maintenance hammer the SMB
        # mount at the same time. If the first scanner already produced a clean
        # result, a waiting force-refresh reuses it instead of scanning twice.
        with self._summary_scan_lock:
            with self._summary_lock:
                if (
                    self._summary_cache is not None
                    and not self._summary_dirty
                    and (
                        not force
                        or self._summary_cached_at != started_cached_at
                    )
                ):
                    return self._summary_cache
                generation = self._summary_generation

            result = self._build_summary()

            with self._summary_lock:
                self._summary_cache = result
                # A mutation during the scan belongs to a later snapshot and
                # must not be cleared by this older result.
                self._summary_dirty = self._summary_generation != generation
                dirty = self._summary_dirty
                self._summary_cached_at = time.monotonic()
            self._persist_summary(result, dirty=dirty)
            return result

    def snapshot(
        self,
        *,
        refresh: bool = False,
    ) -> tuple[LibraryTrackSummary | None, bool]:
        """Return the ledger immediately and refresh stale totals in background."""
        start_worker = False
        with self._summary_lock:
            # Keep page loads non-blocking: return the last snapshot while one
            # background worker reconciles dirty/absent totals with the disk.
            if (
                refresh or self._summary_cache is None or self._summary_dirty
            ) and not self._summary_refreshing:
                self._summary_refreshing = True
                start_worker = True
            cached = self._summary_cache
            refreshing = self._summary_refreshing

        if start_worker:
            threading.Thread(
                target=self._refresh_snapshot,
                name="library-summary-refresh",
                daemon=True,
            ).start()
        return cached, refreshing

    def _refresh_snapshot(self) -> None:
        try:
            self._summary_from_disk(force=True)
        except Exception:
            logger.exception("Library summary refresh failed")
        finally:
            with self._summary_lock:
                self._summary_refreshing = False

    def invalidate_summary(self) -> None:
        """Compatibility alias for marking the persistent ledger stale."""
        self.mark_media_changed()

    def mark_media_changed(self) -> None:
        """Persist a stale marker without walking the filesystem immediately.

        File-mutating services call this only after their operation commits.
        The next summary read starts one background inode scan, so restarts do
        not scan the library and a stale snapshot cannot survive indefinitely.
        """
        with self._summary_lock:
            self._summary_generation += 1
            self._summary_dirty = True
            cached = self._summary_cache
        if cached is not None:
            self._persist_summary(cached, dirty=True)

    def _build_summary(self) -> LibraryTrackSummary:
        present, path_count = self._scan_audio(use_external_inventory=True)
        identified = self._catalog_identified(present)
        raw_identified, raw_verified = self._raw_status(present)
        wanted_identified, wanted_verified = self._wanted_status(present)
        identified |= raw_identified | wanted_identified

        # An inode with any trusted YTM ID is identified, even when another
        # hardlinked location also carries metadata-only verification.
        verified = (raw_verified | wanted_verified) - identified
        unverified = present - identified - verified
        unidentified_count = len(verified) + len(unverified)
        return LibraryTrackSummary(
            effective_count=len(identified) + len(verified),
            identified_count=len(identified),
            unidentified_count=unidentified_count,
            verified_count=len(verified),
            unverified_count=len(unverified),
            physical_count=len(present),
            hardlink_duplicate_count=max(0, path_count - len(present)),
        )

    def audit(self, *, repair_missing: bool = False) -> LibraryAudit:
        """Compare physical files with catalog rows and safely prune stale rows."""
        present, path_count = self._scan_audio()
        tracked: set[InodeKey] = set()
        missing: list[tuple[str, str]] = []
        catalog_location_count = 0

        for rows in self._catalog.list_all_by_video_id().values():
            for location, _record in rows:
                catalog_location_count += 1
                root = self._storage_roots.get(location.storage_root)
                key = (
                    self._inode(root / location.save_folder / location.relative_path)
                    if root is not None
                    else None
                )
                if key is None:
                    missing.append((location.save_folder, location.relative_path))
                else:
                    tracked.add(key)

        for playlist in self._external.list_playlists():
            for row in self._external.list_for_dir(playlist.dir_name):
                key = self._inode(self._external_root / EXTERNAL_RAW_DIR / row.rel_path)
                if key is not None:
                    tracked.add(key)

        for row in self._wanted.list_all():
            if not row.relative_path:
                continue
            key = self._inode(self._wanted_root / row.relative_path)
            if key is not None:
                tracked.add(key)

        repaired = 0
        if repair_missing:
            for save_folder, relative_path in missing:
                self._catalog.delete_location(save_folder, relative_path)
                repaired += 1

        return LibraryAudit(
            physical_count=len(present),
            hardlink_duplicate_count=max(0, path_count - len(present)),
            catalog_location_count=catalog_location_count,
            missing_catalog_locations=max(0, len(missing) - repaired),
            repaired_catalog_locations=repaired,
            untracked_physical_count=len(present - tracked),
        )
