"""Wash pass: upgrade B library files from preselect library A."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from yubal.models.track import TrackMetadata
from yubal.services.preselect_protocol import PreselectPlaceMode
from yubal.services.track_index import TrackFileIndex
from yubal.utils.library import AUDIO_SUFFIXES

from yubal_api.db.track_catalog import TrackLocation, TrackRecord
from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal.utils.library import PRESELECT_EXTERNAL_ROOT
from yubal_api.services.preselect_service import (
    LOSSY_CODECS,
    PreselectService,
    _quality_key,
    same_filesystem,
)

logger = logging.getLogger(__name__)


@dataclass
class WashResult:
    checked: int = 0
    upgraded: int = 0
    skipped: int = 0
    errors: int = 0


def _path_quality_key(path: Path) -> tuple:
    """Quality tuple for an on-disk B file (aligned with preselect _quality_key)."""
    codec = path.suffix.lstrip(".").lower()
    size = 0
    sr = 0
    depth = 0
    try:
        size = path.stat().st_size
    except OSError:
        pass
    try:
        from mediafile import MediaFile

        audio = MediaFile(path)
        if audio.format:
            codec = str(audio.format).lower()
        if audio.samplerate:
            sr = int(audio.samplerate)
        for attr in ("bitdepth", "bits_per_sample"):
            raw = getattr(audio, attr, None)
            if raw:
                depth = int(raw)
                break
    except Exception:
        pass
    lossless = 0 if codec in LOSSY_CODECS else 1
    return (lossless, sr, depth, size, str(path))


def _is_strictly_better(a_key: tuple, b_key: tuple) -> bool:
    """True when A quality beats B (ignore path tie-breaker)."""
    return a_key[:4] > b_key[:4]


def _record_to_track(video_id: str, rec: TrackRecord) -> TrackMetadata | None:
    title = (rec.title or "").strip() or "Unknown"
    artist = (rec.artist or "").strip() or "Unknown Artist"
    album = (rec.album or "").strip() or "Unknown"
    album_artist = (rec.album_artist or artist).strip() or artist
    try:
        return TrackMetadata(
            source_video_id=video_id,
            title=title,
            artists=[artist],
            album=album,
            album_artists=[album_artist],
            track_number=rec.track_number,
            year=rec.year,
            cover_url=rec.cover_url,
        )
    except Exception:
        logger.debug("Cannot build TrackMetadata for %s", video_id)
        return None


def _abs_path(data_root: Path, loc: TrackLocation) -> Path:
    folder = loc.save_folder.strip().replace("\\", "/").rstrip("/")
    rel = loc.relative_path.strip().replace("\\", "/")
    return data_root / folder / rel


def _place_file(source: Path, dest: Path, mode: PreselectPlaceMode) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    if mode == "link":
        os.link(source, dest)
    else:
        shutil.copy2(source, dest)


def _place_lrc(source: Path, dest: Path, mode: PreselectPlaceMode) -> None:
    src_lrc = source.with_suffix(".lrc")
    dest_lrc = dest.with_suffix(".lrc")
    if not src_lrc.is_file():
        return
    try:
        if dest_lrc.exists():
            dest_lrc.unlink()
        if mode == "link":
            os.link(src_lrc, dest_lrc)
        else:
            shutil.copy2(src_lrc, dest_lrc)
    except OSError:
        try:
            shutil.copy2(src_lrc, dest_lrc)
        except OSError:
            logger.debug("Could not place lyrics for wash: %s", dest_lrc)


class WashService:
    """Upgrade existing B tracks when A has a clearly better match."""

    def __init__(
        self,
        preselect: PreselectService,
        catalog: TrackCatalogRepository,
        data_path: Path,
    ) -> None:
        self._preselect = preselect
        self._catalog = catalog
        self._data_path = data_path

    def run(self) -> WashResult:
        prefs = self._preselect.preferences_store.effective()
        if not prefs.wash_enabled:
            return WashResult()
        root = PRESELECT_EXTERNAL_ROOT
        if not root.is_dir():
            logger.warning("Wash skipped: External missing at %s", root)
            return WashResult()

        mode: PreselectPlaceMode = (
            "link" if prefs.preselect_place_mode == "link" else "copy"
        )
        if mode == "link" and not same_filesystem(root, self._data_path):
            logger.warning("Wash hardlink unavailable; using copy")
            mode = "copy"

        grouped = self._catalog.list_all_by_video_id()
        result = WashResult()
        index = TrackFileIndex(self._data_path)

        for video_id, pairs in grouped.items():
            result.checked += 1
            try:
                upgraded = self._wash_video(
                    video_id=video_id,
                    pairs=pairs,
                    mode=mode,
                    index=index,
                )
                if upgraded:
                    result.upgraded += 1
                else:
                    result.skipped += 1
            except Exception:
                result.errors += 1
                logger.exception("Wash failed for video_id=%s", video_id)

        logger.info(
            "Wash done: checked=%s upgraded=%s skipped=%s errors=%s",
            result.checked,
            result.upgraded,
            result.skipped,
            result.errors,
        )
        return result

    def ready(self) -> tuple[bool, str | None]:
        """Return (ok, error_message)."""
        prefs = self._preselect.preferences_store.effective()
        if not prefs.wash_enabled:
            return False, "wash_enabled is off"
        if not PRESELECT_EXTERNAL_ROOT.is_dir():
            return False, "External preselect mount missing (/External)"
        return True, None

    def placed_count(self) -> int:
        """How many B locations came from preselect (link or copy)."""
        return self._catalog.count_by_origins(["preselect_link", "preselect_copy"])

    def _wash_video(
        self,
        *,
        video_id: str,
        pairs: list[tuple[TrackLocation, TrackRecord]],
        mode: PreselectPlaceMode,
        index: TrackFileIndex,
    ) -> bool:
        if not pairs:
            return False
        rec = pairs[0][1]
        track = _record_to_track(video_id, rec)
        if track is None:
            return False

        hit = self._preselect.find_match(track, require_enabled=False)
        if hit is None:
            return False

        source = hit.source_path
        # Prefer quality row from index for comparison
        a_row = self._preselect.match_row(track)
        a_key = _quality_key(a_row) if a_row else _path_quality_key(source)

        # Existing B files for this video_id
        existing_paths: list[Path] = []
        for loc, _ in pairs:
            path = _abs_path(self._data_path, loc)
            if path.is_file():
                existing_paths.append(path)
        if not existing_paths:
            return False

        # Compare against best (highest quality) existing B file
        b_key = max((_path_quality_key(p) for p in existing_paths), default=None)
        if b_key is None or not _is_strictly_better(a_key, b_key):
            return False

        new_suffix = source.suffix.lower()
        if not new_suffix:
            new_suffix = ".flac"
        origin = "preselect_link" if mode == "link" else "preselect_copy"

        # Atomic place into a temp under data, then distribute
        # For each location: stem + new_suffix
        new_paths: list[tuple[TrackLocation, Path, Path | None]] = []
        # (loc, new_dest, old_path_if_different)

        primary_new: Path | None = None
        for loc, _ in pairs:
            old = _abs_path(self._data_path, loc)
            # stem from existing file or relative_path
            if old.suffix.lower() in AUDIO_SUFFIXES or old.suffix:
                stem = old.with_suffix("")
            else:
                stem = old
            new_dest = Path(f"{stem}{new_suffix}")
            old_keep = old if old.is_file() else None
            new_paths.append((loc, new_dest, old_keep))

        if not new_paths:
            return False

        # Write primary first (temp then replace)
        _, first_dest, _ = new_paths[0]
        first_dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            suffix=new_suffix,
            dir=str(first_dest.parent),
        )
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            if mode == "link":
                tmp_path.unlink(missing_ok=True)
                os.link(source, tmp_path)
            else:
                shutil.copy2(source, tmp_path)
            tmp_path.replace(first_dest)
            primary_new = first_dest
            _place_lrc(source, first_dest, mode)
        except OSError:
            tmp_path.unlink(missing_ok=True)
            raise

        # Remaining locations: hardlink/copy from primary (B-internal share)
        # Prefer hardlink among B paths when same FS (always true under data)
        for loc, new_dest, old_path in new_paths[1:]:
            if new_dest.resolve() == primary_new.resolve():
                continue
            try:
                _place_file(primary_new, new_dest, "link")
            except OSError:
                _place_file(primary_new, new_dest, "copy")
            _place_lrc(source, new_dest, mode)

            if old_path and old_path.resolve() != new_dest.resolve() and old_path.is_file():
                try:
                    old_path.unlink()
                    old_lrc = old_path.with_suffix(".lrc")
                    if old_lrc.is_file():
                        # only remove if not the new sidecar
                        if old_lrc.resolve() != new_dest.with_suffix(".lrc").resolve():
                            old_lrc.unlink(missing_ok=True)
                except OSError:
                    pass

        # Remove old primary if extension changed
        first_loc, first_dest, first_old = new_paths[0]
        if (
            first_old
            and first_old.resolve() != first_dest.resolve()
            and first_old.is_file()
        ):
            try:
                first_old.unlink()
                old_lrc = first_old.with_suffix(".lrc")
                if old_lrc.is_file() and old_lrc.resolve() != first_dest.with_suffix(
                    ".lrc"
                ).resolve():
                    old_lrc.unlink(missing_ok=True)
            except OSError:
                pass

        # Update catalog locations (relative_path may change with suffix)
        for loc, new_dest, _ in new_paths:
            try:
                rel_to_data = new_dest.resolve().relative_to(self._data_path.resolve())
            except ValueError:
                continue
            parts = rel_to_data.parts
            folder = loc.save_folder.strip().replace("\\", "/").rstrip("/")
            folder_parts = tuple(p for p in folder.split("/") if p)
            if parts[: len(folder_parts)] != folder_parts:
                continue
            relative = (
                str(Path(*parts[len(folder_parts) :]))
                if len(parts) > len(folder_parts)
                else new_dest.name
            )
            self._catalog.upsert_location(
                video_id=video_id,
                save_folder=folder,
                relative_path=relative,
                origin=origin,
            )

        index.set(video_id, first_dest)
        logger.info(
            "Washed %s → %s (%s)",
            video_id,
            first_dest,
            mode,
        )
        return True
