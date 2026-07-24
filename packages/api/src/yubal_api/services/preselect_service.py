"""Preselect library (A): scan index, match, place-mode helpers."""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from yubal.models.track import TrackMetadata
from yubal.services.preselect_protocol import PreselectHit, PreselectPlaceMode
from yubal.utils.library import (
    AUDIO_SUFFIXES,
    PRESELECT_EXTERNAL_ROOT,
    same_filesystem,
)
from yubal.utils.normalize_text import normalize_artist_key, normalize_music_text

from yubal_api.db.preselect import PreselectTrack
from yubal_api.db.preselect_repository import PreselectRepository
from yubal_api.services.preferences import PreferencesStore

logger = logging.getLogger(__name__)

LOSSY_CODECS = frozenset({"mp3", "m4a", "aac", "opus", "ogg", "wma", "webm"})
DURATION_TOLERANCE_MS_DEFAULT = 2000


@dataclass
class ScanProgress:
    running: bool = False
    phase: str = "idle"
    scanned: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    errors: int = 0
    total_indexed: int = 0
    last_error: str | None = None
    finished_at: datetime | None = None


def _quality_key(row: PreselectTrack) -> tuple:
    codec = (row.codec or "").lower()
    lossless = 0 if codec in LOSSY_CODECS else 1
    sr = row.sample_rate or 0
    depth = row.bit_depth or 0
    size = row.size or 0
    return (lossless, sr, depth, size, row.rel_path)


def _read_tags(path: Path) -> PreselectTrack | None:
    try:
        from mediafile import MediaFile

        audio = MediaFile(path)
    except Exception as e:
        logger.debug("Skip unreadable %s: %s", path, e)
        return None

    try:
        st = path.stat()
    except OSError:
        return None

    title = (audio.title or path.stem or "").strip()
    artist_raw = audio.artist or audio.albumartist or ""
    if isinstance(artist_raw, (list, tuple)):
        artists = " / ".join(str(a) for a in artist_raw if a)
    else:
        artists = str(artist_raw or "").strip()
    album = str(audio.album or "").strip()
    album_artist = str(audio.albumartist or artists).strip()

    duration_ms = None
    if audio.length:
        try:
            duration_ms = int(float(audio.length) * 1000)
        except (TypeError, ValueError):
            duration_ms = None

    codec = (path.suffix.lstrip(".") or "").lower()
    if audio.format:
        codec = str(audio.format).lower()

    sample_rate = None
    try:
        sample_rate = int(audio.samplerate) if audio.samplerate else None
    except (TypeError, ValueError):
        pass

    bit_depth = None
    for attr in ("bitdepth", "bits_per_sample"):
        raw = getattr(audio, attr, None)
        if raw:
            try:
                bit_depth = int(raw)
                break
            except (TypeError, ValueError):
                pass

    channels = None
    try:
        channels = int(audio.channels) if audio.channels else None
    except (TypeError, ValueError):
        pass

    year = None
    if audio.year:
        year = str(audio.year)[:16]

    track_number = None
    if audio.track:
        try:
            track_number = int(audio.track)
        except (TypeError, ValueError):
            pass

    disc_number = None
    if audio.disc:
        try:
            disc_number = int(audio.disc)
        except (TypeError, ValueError):
            pass

    has_cover = bool(getattr(audio, "images", None))
    lyrics_embedded = bool(audio.lyrics and str(audio.lyrics).strip())
    has_lrc = path.with_suffix(".lrc").is_file()

    artist_norm = normalize_artist_key(artists)
    title_norm = normalize_music_text(title)
    album_norm = normalize_music_text(album)

    # rel_path filled by caller
    return PreselectTrack(
        rel_path="",
        mtime_ns=getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)),
        size=st.st_size,
        inode=getattr(st, "st_ino", None),
        codec=codec,
        sample_rate=sample_rate,
        bit_depth=bit_depth,
        channels=channels,
        duration_ms=duration_ms,
        title=title[:500],
        artists=artists[:500],
        album=album[:500],
        album_artist=album_artist[:500],
        track_number=track_number,
        disc_number=disc_number,
        year=year,
        title_norm=title_norm[:500],
        artist_norm=artist_norm[:500],
        album_norm=album_norm[:500],
        has_lyrics=lyrics_embedded or has_lrc,
        lyrics_embedded=lyrics_embedded,
        has_cover=has_cover,
        cover_embedded=has_cover,
    )


class PreselectService:
    """Scan / match the local preselect library."""

    def __init__(
        self,
        repository: PreselectRepository,
        preferences: PreferencesStore,
        data_path: Path,
    ) -> None:
        self._repository = repository
        self._preferences = preferences
        self._data_path = data_path
        self._lock = threading.Lock()
        self._progress = ScanProgress()

    def status(self) -> ScanProgress:
        with self._lock:
            cur = self._progress
            snapshot = ScanProgress(
                running=cur.running,
                phase=cur.phase,
                scanned=cur.scanned,
                added=cur.added,
                updated=cur.updated,
                removed=cur.removed,
                errors=cur.errors,
                total_indexed=0,
                last_error=cur.last_error,
                finished_at=cur.finished_at,
            )
        # Count outside the lock so we never deadlock with the scan thread
        snapshot.total_indexed = self._repository.count()
        return snapshot

    def hardlink_supported(self) -> bool | None:
        """None when External missing; else whether A and B share a filesystem."""
        root = PRESELECT_EXTERNAL_ROOT
        if not root.is_dir():
            return None
        return same_filesystem(root, self._data_path)

    def root_configured(self) -> bool:
        return PRESELECT_EXTERNAL_ROOT.is_dir()

    @staticmethod
    def fixed_root() -> Path:
        return PRESELECT_EXTERNAL_ROOT

    @property
    def preferences_store(self) -> PreferencesStore:
        return self._preferences

    def scan_incremental(self, *, force_all: bool = False) -> ScanProgress:
        root = PRESELECT_EXTERNAL_ROOT
        if not root.is_dir():
            raise ValueError(
                f"preselect root missing: {root} "
                "(mount ./data/External to /External in compose)"
            )

        with self._lock:
            if self._progress.running:
                raise RuntimeError("preselect scan already running")
            self._progress = ScanProgress(running=True, phase="walking")

        try:
            return self._scan(root, force_all=force_all)
        finally:
            with self._lock:
                self._progress.running = False
                self._progress.phase = "idle"
                self._progress.finished_at = datetime.now(UTC)
                self._progress.total_indexed = self._repository.count()

    def _scan(self, root: Path, *, force_all: bool) -> ScanProgress:
        with self._lock:
            self._progress.phase = "norms"
        norms_updated = self._repository.refresh_all_norms()
        if norms_updated:
            logger.info("Refreshed match norms for %s preselect rows", norms_updated)

        existing = {} if force_all else self._repository.list_path_stats()
        seen: set[str] = set()
        added = updated = errors = scanned = 0

        with self._lock:
            self._progress.phase = "walking"

        for dirpath, dirnames, filenames in os.walk(root):
            # skip hidden dirs
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.startswith("."):
                    continue
                path = Path(dirpath) / name
                if path.suffix.lower() not in AUDIO_SUFFIXES:
                    continue
                try:
                    rel = str(path.resolve().relative_to(root.resolve())).replace(
                        "\\", "/"
                    )
                except ValueError:
                    continue
                seen.add(rel)
                scanned += 1
                try:
                    st = path.stat()
                except OSError:
                    errors += 1
                    continue
                mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
                size = st.st_size
                prev = existing.get(rel)
                if prev is not None and prev == (mtime_ns, size) and not force_all:
                    continue
                row = _read_tags(path)
                if row is None:
                    errors += 1
                    continue
                row.rel_path = rel
                self._repository.upsert(row)
                if prev is None:
                    added += 1
                else:
                    updated += 1

            with self._lock:
                self._progress.scanned = scanned
                self._progress.added = added
                self._progress.updated = updated
                self._progress.errors = errors
                self._progress.phase = "indexing"

        removed_paths = [p for p in existing if p not in seen]
        removed = self._repository.delete_paths(removed_paths)

        with self._lock:
            self._progress.scanned = scanned
            self._progress.added = added
            self._progress.updated = updated
            self._progress.removed = removed
            self._progress.errors = errors
            self._progress.phase = "done"
        # Do not call status() under _lock — Lock is not reentrant (was hanging forever).
        return self.status()

    def lookup(self, track: TrackMetadata) -> PreselectHit | None:
        """PreselectSource protocol — match YTM metadata against A index."""
        prefs = self._preferences.effective()
        if not prefs.preselect_enabled:
            return None
        return self.find_match(track, require_enabled=False)

    def match_row(self, track: TrackMetadata) -> PreselectTrack | None:
        """Best matching preselect row (no place mode), or None."""
        return self._best_row(track)

    def find_match(
        self,
        track: TrackMetadata,
        *,
        require_enabled: bool = False,
    ) -> PreselectHit | None:
        """Find best A file for track; optionally require preselect_enabled."""
        prefs = self._preferences.effective()
        if require_enabled and not prefs.preselect_enabled:
            return None
        root = PRESELECT_EXTERNAL_ROOT
        if not root.is_dir():
            return None

        best = self._best_row(track)
        if best is None:
            return None
        source = root / best.rel_path
        if not source.is_file():
            return None

        mode: PreselectPlaceMode = (
            "link" if prefs.preselect_place_mode == "link" else "copy"
        )
        if mode == "link" and not same_filesystem(root, self._data_path):
            logger.warning(
                "Preselect hardlink requested but A/B are on different filesystems; "
                "falling back to copy"
            )
            mode = "copy"

        logger.info(
            "Preselect match: %s → %s (%s)",
            track.title,
            best.rel_path,
            mode,
        )
        return PreselectHit(source_path=source, mode=mode)

    def _best_row(self, track: TrackMetadata) -> PreselectTrack | None:
        prefs = self._preferences.effective()
        artist_norm = normalize_artist_key(track.artists)
        title_norm = normalize_music_text(track.title)
        if not artist_norm or not title_norm:
            return None

        candidates = self._repository.find_by_artist_title(artist_norm, title_norm)
        if not candidates:
            return None

        match_mode = prefs.preselect_match_mode
        album_norm = normalize_music_text(track.album)
        duration_ms = (
            int(track.duration_seconds * 1000)
            if track.duration_seconds is not None
            else None
        )
        tol = DURATION_TOLERANCE_MS_DEFAULT

        filtered: list[PreselectTrack] = []
        for row in candidates:
            if match_mode in ("standard", "strict") and duration_ms is not None:
                if row.duration_ms is not None and abs(row.duration_ms - duration_ms) > tol:
                    continue
            if match_mode == "strict" and album_norm:
                if row.album_norm and row.album_norm != album_norm:
                    continue
            filtered.append(row)

        if not filtered:
            return None
        return max(filtered, key=_quality_key)
