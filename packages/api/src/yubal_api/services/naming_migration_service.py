"""One-shot rename of legacy ``NN - Title`` files to ``Artist - Title``.

Runs at most once per data root (flag file under ``.yubal/``). Updates
catalog locations, track index, and m3u playlists. Skips immutable tracks
and files already matching the new convention.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from yubal.services.track_index import TrackFileIndex
from yubal.utils.filename import build_track_path
from yubal.utils.library import (
    AUDIO_SUFFIXES,
    STORAGE_DOWNLOAD,
    STORAGE_ROOTS,
    resolve_under_data,
    runtime_state_path,
)

from yubal_api.db.track_catalog import TrackLocation, TrackRecord
from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.services.library_ops import parse_m3u_files, rewrite_path_in_m3u

logger = logging.getLogger(__name__)

# Legacy: "01 - Title" or "1 - Title"
_LEGACY_TRACK_NUM_PREFIX = re.compile(r"^\d{1,3}\s*-\s+")

FLAG_NAME = "naming_artist_title_v1.done"


@dataclass
class NamingMigrationResult:
    checked: int = 0
    renamed: int = 0
    skipped: int = 0
    errors: int = 0


def _flag_path(download_root: Path) -> Path:
    return runtime_state_path(download_root, FLAG_NAME)


def _looks_legacy_stem(stem: str) -> bool:
    return bool(_LEGACY_TRACK_NUM_PREFIX.match(stem))


def _resolve_loc_path(loc: TrackLocation) -> Path | None:
    root = STORAGE_ROOTS.get(loc.storage_root or STORAGE_DOWNLOAD)
    if root is None:
        return None
    try:
        return resolve_under_data(root, f"{loc.save_folder}/{loc.relative_path}")
    except ValueError:
        return None


class NamingConventionMigrator:
    """Rename stock library files from ``NN - Title`` to ``Artist - Title``."""

    def __init__(
        self,
        catalog: TrackCatalogRepository,
        download_root: Path,
        *,
        ascii_filenames: bool = False,
    ) -> None:
        self._catalog = catalog
        self._download_root = download_root
        self._ascii = ascii_filenames
        self._index = TrackFileIndex(download_root)

    def already_done(self) -> bool:
        return _flag_path(self._download_root).is_file()

    def run(self, *, force: bool = False) -> NamingMigrationResult:
        result = NamingMigrationResult()
        if not force and self.already_done():
            return result

        grouped = self._catalog.list_all_by_video_id()
        for video_id, pairs in grouped.items():
            for loc, rec in pairs:
                result.checked += 1
                try:
                    if self._migrate_one(video_id, loc, rec):
                        result.renamed += 1
                    else:
                        result.skipped += 1
                except Exception:
                    result.errors += 1
                    logger.exception(
                        "Naming migration failed for %s @ %s/%s",
                        video_id,
                        loc.save_folder,
                        loc.relative_path,
                    )

        flag = _flag_path(self._download_root)
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("ok\n", encoding="utf-8")
        logger.info(
            "Naming migration: checked=%d renamed=%d skipped=%d errors=%d",
            result.checked,
            result.renamed,
            result.skipped,
            result.errors,
        )
        return result

    def _migrate_one(
        self,
        video_id: str,
        loc: TrackLocation,
        rec: TrackRecord,
    ) -> bool:
        if rec.immutable:
            return False
        src = _resolve_loc_path(loc)
        if src is None or not src.is_file():
            return False
        if src.suffix.lower() not in AUDIO_SUFFIXES:
            return False
        if not _looks_legacy_stem(src.stem):
            return False

        root = STORAGE_ROOTS.get(loc.storage_root or STORAGE_DOWNLOAD)
        if root is None:
            return False
        folder_base = resolve_under_data(root, loc.save_folder)
        artist = (rec.album_artist or rec.artist or "Unknown Artist").strip()
        stem = build_track_path(
            base=folder_base,
            artist=artist,
            year=rec.year,
            album=rec.album or "Unknown Album",
            track_number=rec.track_number,
            title=rec.title or src.stem,
            ascii_filenames=self._ascii,
            video_id=None,
        )
        dest = Path(f"{stem}{src.suffix.lower()}")
        if dest.resolve() == src.resolve():
            return False

        # Collision with a different file → disambiguate with video_id suffix
        if dest.exists() and dest.resolve() != src.resolve():
            stem = build_track_path(
                base=folder_base,
                artist=artist,
                year=rec.year,
                album=rec.album or "Unknown Album",
                track_number=rec.track_number,
                title=rec.title or src.stem,
                ascii_filenames=self._ascii,
                video_id=video_id,
            )
            dest = Path(f"{stem}{src.suffix.lower()}")
            if dest.exists() and dest.resolve() != src.resolve():
                logger.warning(
                    "Naming migration skip (dest exists): %s -> %s", src, dest
                )
                return False

        indexed = self._index.get(video_id)
        was_indexed = indexed is not None and indexed.resolve() == src.resolve()
        was_canonical = False
        if rec.canonical_rel:
            try:
                old_canon = resolve_under_data(
                    STORAGE_ROOTS.get(rec.canonical_storage or STORAGE_DOWNLOAD)
                    or self._download_root,
                    rec.canonical_rel,
                )
                was_canonical = (
                    old_canon is not None and old_canon.resolve() == src.resolve()
                )
            except ValueError:
                was_canonical = False

        dest.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dest)
        src_lrc = src.with_suffix(".lrc")
        dest_lrc = dest.with_suffix(".lrc")
        if src_lrc.is_file() and not dest_lrc.exists():
            try:
                src_lrc.rename(dest_lrc)
            except OSError:
                pass

        new_rel = str(dest.resolve().relative_to(folder_base.resolve()))
        self._catalog.upsert_location(
            video_id=video_id,
            save_folder=loc.save_folder,
            relative_path=new_rel,
            origin=loc.origin,
            storage_root=loc.storage_root or STORAGE_DOWNLOAD,
        )

        if was_canonical:
            storage = loc.storage_root or STORAGE_DOWNLOAD
            root_r = STORAGE_ROOTS[storage].resolve()
            canon_rel = str(dest.resolve().relative_to(root_r))
            self._catalog.set_canonical(
                video_id, storage=storage, relative_path=canon_rel
            )

        if was_indexed or indexed is None:
            self._index.set(video_id, dest)

        # Rewrite m3u under this save folder (download root only)
        if (loc.storage_root or STORAGE_DOWNLOAD) == STORAGE_DOWNLOAD:
            for m3u in parse_m3u_files(self._download_root / loc.save_folder):
                try:
                    rewrite_path_in_m3u(m3u, src, dest)
                except OSError:
                    logger.debug("Could not rewrite m3u %s", m3u)

        logger.info("Renamed %s -> %s", src, dest)
        return True
