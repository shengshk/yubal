"""Whole-library physical audio statistics."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from yubal.utils.library import (
    AUDIO_SUFFIXES,
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
            self.missing_catalog_locations == 0
            and self.untracked_physical_count == 0
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

    @staticmethod
    def _inode(path: Path) -> InodeKey | None:
        try:
            if not path.is_file() or path.suffix.lower() not in AUDIO_SUFFIXES:
                return None
            stat = path.stat()
            return stat.st_dev, stat.st_ino
        except OSError:
            return None

    def _scan_audio(self) -> tuple[set[InodeKey], int]:
        inodes: set[InodeKey] = set()
        path_count = 0
        for root in (
            self._download_root,
            self._external_root,
            self._wanted_root,
        ):
            if not root.is_dir():
                continue
            try:
                paths = root.rglob("*")
                for path in paths:
                    key = self._inode(path)
                    if key is None:
                        continue
                    path_count += 1
                    inodes.add(key)
            except OSError:
                continue
        return inodes, path_count

    def _catalog_identified(self, present: set[InodeKey]) -> set[InodeKey]:
        identified: set[InodeKey] = set()
        for rows in self._catalog.list_all_by_video_id().values():
            for location, _record in rows:
                root = self._storage_roots.get(location.storage_root)
                if root is None:
                    continue
                key = self._inode(
                    root / location.save_folder / location.relative_path
                )
                if key in present:
                    identified.add(key)
        return identified

    def _raw_status(
        self,
        present: set[InodeKey],
    ) -> tuple[set[InodeKey], set[InodeKey]]:
        identified: set[InodeKey] = set()
        verified: set[InodeKey] = set()
        for playlist in self._external.list_playlists():
            for row in self._external.list_for_dir(playlist.dir_name):
                key = self._inode(
                    self._external_root / EXTERNAL_RAW_DIR / row.rel_path
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

    def summary(self) -> LibraryTrackSummary:
        present, path_count = self._scan_audio()
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
                    self._inode(
                        root / location.save_folder / location.relative_path
                    )
                    if root is not None
                    else None
                )
                if key is None:
                    missing.append(
                        (location.save_folder, location.relative_path)
                    )
                else:
                    tracked.add(key)

        for playlist in self._external.list_playlists():
            for row in self._external.list_for_dir(playlist.dir_name):
                key = self._inode(
                    self._external_root / EXTERNAL_RAW_DIR / row.rel_path
                )
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
