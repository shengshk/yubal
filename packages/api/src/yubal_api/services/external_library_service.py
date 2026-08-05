"""External music library: scan Raw/, match against YouTube Music, ingest.

Layout under /External::

    Raw/<dir_name>/...        # user's pre-existing files, one dir per playlist
    Organized/<dir_name>/...  # matched tracks, laid out like Download (Artist/Album)

Each ``<dir_name>`` under Raw becomes one ``ExternalPlaylist`` row. Matching
looks up each raw file on YouTube Music by tags; on success the file is
ingested into ``Organized/<dir_name>`` (moved when the playlist allows
mutation, hardlinked otherwise) and the catalog/track-index are updated so
the rest of the app treats it like any other library track.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from yubal.client import YTMusicClient, YTMusicProtocol
from yubal.config import APIConfig
from yubal.exceptions import UpstreamAPIError
from yubal.lib.matching import (
    extract_base_title,
    has_version_marker,
    match_artists,
    match_title,
    normalize_title,
)
from yubal.models.ytmusic import Artist
from yubal.services.track_index import TrackFileIndex
from yubal.utils.filename import build_track_path
from yubal.utils.library import (
    AUDIO_SUFFIXES,
    DIRECT_FOLDER,
    DOWNLOAD_ROOT,
    EXTERNAL_DEFAULT_DIR,
    EXTERNAL_DELETE_DIR,
    EXTERNAL_ORGANIZED_ROOT,
    EXTERNAL_RAW_DIR,
    EXTERNAL_RAW_ROOT,
    EXTERNAL_ROOT,
    STORAGE_DOWNLOAD,
    STORAGE_EXTERNAL,
    STORAGE_ROOTS,
    detect_storage_for_path,
    ensure_external_layout,
    organized_save_folder,
    same_filesystem,
    sanitize_direct_folder,
)
from yubal.utils.normalize_text import (
    has_cjk,
    normalize_artist_key,
    normalize_music_text,
)

from yubal_api.db.external_library import (
    EXTERNAL_ACCESS_MANAGED,
    EXTERNAL_ACCESS_MODES,
    EXTERNAL_ACCESS_PENDING,
    MATCH_MATCHED,
    MATCH_REJECTED,
    MATCH_UNMATCHED,
    META_PENDING,
    META_VERIFIED,
    ExternalPlaylist,
    ExternalRawTrack,
)
from yubal_api.db.external_library_repository import ExternalLibraryRepository
from yubal_api.db.track_catalog import (
    LocationMembershipStatus,
    TrackLocation,
    TrackRecord,
)
from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.services.library_health_service import LibraryHealthService
from yubal_api.services.library_ops import cleanup_after_audio_removed
from yubal_api.services.preferences import PreferencesStore

logger = logging.getLogger(__name__)

LOSSY_CODECS = frozenset({"mp3", "m4a", "aac", "opus", "ogg", "wma", "webm"})

# Fuzzy-match acceptance thresholds (0-100 scale, see yubal.lib.matching).
_MATCH_TITLE_THRESHOLD = 70.0
_MATCH_ARTIST_THRESHOLD = 62.0

# Match backoff: delay = fail_count * 24h; reject when delay would exceed
# ``match_backoff_cap_days`` (default 7). Cap comes from preferences.
_BACKOFF_STEP_SECONDS = 86400
_DEFAULT_BACKOFF_CAP_DAYS = 7

# Background matching can issue several YTM searches for one local file. Keep
# all automatic matching on this service instance to a conservative shared pace.
_YTM_AUTO_MATCH_MIN_INTERVAL_SECONDS = 1.0
_INVENTORY_WRITE_BATCH = 500


@dataclass
class ScanResult:
    playlists: int = 0
    scanned: int = 0
    added: int = 0
    updated: int = 0
    removed: int = 0
    errors: int = 0


@dataclass
class MatchBatchResult:
    checked: int = 0
    matched: int = 0
    deferred: int = 0
    rejected: int = 0
    errors: int = 0


@dataclass(frozen=True)
class MatchCandidate:
    """Scored YTM search hit for auto-match or manual picker."""

    video_id: str
    title: str
    artists: str
    album: str
    thumbnail_url: str | None
    title_score: float
    artist_score: float
    score: float
    auto_ok: bool


@dataclass(frozen=True)
class MetaCandidate:
    """Wanted-source hit for tag verification / fill (QQ / MusicBrainz / …)."""

    source: str
    source_id: str
    title: str
    artists: str
    album: str
    source_url: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: int | None = None
    score: float = 0.0


@dataclass(frozen=True)
class MatchOneManualResult:
    matched: bool
    video_id: str | None
    ingested: bool
    mode_used: str
    ytm_candidates: list[MatchCandidate]
    meta_candidates: list[MetaCandidate]


_MANUAL_CANDIDATE_LIMIT = 5
# Drop picker rows whose (relaxed) title similarity is below this — avoids
# "artist-overlap only" noise like game-OST dumps with unrelated titles.
_MANUAL_CANDIDATE_MIN_TITLE = 40.0
_RANK_TITLE_WEIGHT = 0.7
_RANK_ARTIST_WEIGHT = 0.3
_META_CANDIDATE_LIMIT = 5

_ARTIST_SPLIT_RE = re.compile(
    r"\s*/\s*|\s*&\s*|\s*;\s*|\s*,\s*|\s+feat\.?\s+|\s+ft\.?\s+",
    re.IGNORECASE,
)


def _split_artist_names(artists: str) -> list[str]:
    return [
        part.strip() for part in _ARTIST_SPLIT_RE.split(artists or "") if part.strip()
    ]


def _search_title(title: str) -> str:
    """Title for YTM search: strip parenthetical subtitles (game/OST tags)."""
    raw = (title or "").strip()
    if not raw:
        return ""
    cleaned = re.sub(
        r"\s*[\(（\[【][^）\)\]】]*[\)）\]】]\s*",  # noqa: RUF001
        " ",
        raw,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if cleaned:
        return cleaned
    # Fallback to matching base (lowercased) if stripping emptied the string.
    return extract_base_title(normalize_title(raw)) or raw


@dataclass
class SyncPlaylistResult:
    matched: int = 0
    recovered: int = 0
    checked: int = 0
    errors: int = 0
    deferred: int = 0
    rejected: int = 0
    meta_checked: int = 0
    meta_verified: int = 0
    enriched: int = 0
    upgraded: int = 0
    asset_errors: int = 0


@dataclass
class DeletePlaylistResult:
    deleted_files: int = 0
    deleted_locations: int = 0
    deleted_raw: int = 0
    moved: int = 0
    reset_matches: int = 0
    skipped_readonly: int = 0
    errors: int = 0


@dataclass(frozen=True)
class PlaylistTrackView:
    """One row for the external playlist track list (raw or organized)."""

    rel_path: str
    dir_name: str
    title: str
    artist: str
    album: str
    video_id: str | None
    match_status: str
    is_raw: bool
    tags_complete: bool = False
    is_junk: bool = False
    # ``rw`` = writable junk (rejected on mutable playlist);
    # ``ro`` = readonly junk (rejected or incomplete tags on readonly).
    junk_kind: str | None = None
    cover_url: str | None = None
    cover_source: str | None = None
    has_embedded_cover: bool = False
    album_artist: str | None = None
    year: str | None = None
    track_number: int | None = None
    # Catalog relative path under Organized/<dir> (matched only); used for delete.
    organized_relative_path: str | None = None
    # True when this video_id already has a catalog location under Direct/.
    in_direct: bool = False
    meta_status: str = "pending"
    meta_source: str | None = None
    meta_source_id: str | None = None
    meta_source_url: str | None = None
    can_mutate: bool = False


@dataclass
class ExternalPlaylistView:
    dir_name: str
    allow_mutate: bool
    access_mode: str
    access_mode_locked: bool
    source_mutated_at: datetime | None
    source_mutation_kind: str | None
    show_raw: bool
    show_junk: bool
    inventory_scanned: bool
    unmatched_count: int
    matched_count: int
    meta_verified_count: int
    meta_rejected_count: int
    meta_rejected_mutable_count: int
    cloud: int
    local: int
    offline: int
    exclusive: int
    shared: int
    hardlink: int
    cover_track_path: str | None
    enabled: bool
    max_items: int
    sync_jitter_seconds: int
    offline_marking_enabled: bool
    offline_cleanup_enabled: bool
    offline_cleanup_action: str
    offline_cleanup_delay_hours: int
    last_synced_at: datetime | None
    last_sync_status: str | None


def _location_abs_path(loc: TrackLocation) -> Path:
    root = STORAGE_ROOTS.get(loc.storage_root or STORAGE_EXTERNAL, EXTERNAL_ROOT)
    return root / loc.save_folder / loc.relative_path


def _quality_key(row: ExternalRawTrack) -> tuple:
    codec = (row.codec or "").lower()
    lossless = 0 if codec in LOSSY_CODECS else 1
    sr = row.sample_rate or 0
    depth = row.bit_depth or 0
    size = row.size or 0
    return (lossless, sr, depth, size, row.rel_path)


def _path_quality_key(path: Path) -> tuple:
    """On-disk quality tuple aligned with ``_quality_key`` (ignore path for compare)."""
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
    """True when A beats B on (lossless, sr, depth, size); path tie-breaker ignored."""
    return a_key[:4] > b_key[:4]


def _read_raw_tags(
    path: Path,
    rel_path: str,
    dir_name: str,
    *,
    origin_kind: str = "",
    origin_ref: str = "",
) -> ExternalRawTrack | None:
    """Probe one audio file's tags into an ExternalRawTrack row."""
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

    year = str(audio.year)[:16] if audio.year else None

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

    file_key = None
    try:
        file_key = f"{st.st_dev}:{st.st_ino}"
    except AttributeError:
        pass

    return ExternalRawTrack(
        rel_path=rel_path,
        dir_name=dir_name,
        origin_kind=origin_kind,
        origin_ref=origin_ref,
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
        title_norm=normalize_music_text(title)[:500],
        artist_norm=normalize_artist_key(artists)[:500],
        album_norm=normalize_music_text(album)[:500],
        has_lyrics=lyrics_embedded or has_lrc,
        lyrics_embedded=lyrics_embedded,
        has_cover=has_cover,
        cover_embedded=has_cover,
        file_key=file_key,
    )


class ExternalLibraryService:
    """Scan / match / ingest raw external audio files into the catalog."""

    def __init__(
        self,
        repository: ExternalLibraryRepository,
        catalog: TrackCatalogRepository,
        preferences: PreferencesStore,
        *,
        cookies_path: Path | None = None,
        ytmusic_client: YTMusicProtocol | None = None,
        track_index: TrackFileIndex | None = None,
        enrichment: object | None = None,
    ) -> None:
        self._repository = repository
        self._catalog = catalog
        self._preferences = preferences
        self._client: YTMusicProtocol = ytmusic_client or YTMusicClient(
            config=APIConfig(search_limit=10),
            cookies_path=cookies_path,
        )
        # The index file lives under the Download root by convention, but
        # entries may point at either root (dual-root aware index).
        self._track_index = track_index or TrackFileIndex(DOWNLOAD_ROOT)
        self._enrichment = enrichment
        self._wanted: object | None = None
        self._media_changed: Callable[[], None] | None = None
        # Discovery, legacy reconciliation and Raw mutations can be reached
        # from both the list API and a sync job.  Reentrancy is required
        # because scan_raw performs discovery under the same operation lock.
        self._lock = threading.RLock()
        self._inventory_lock = threading.Lock()
        self._inventory_worker_running = False
        self._track_page_cache: dict[
            tuple[str, bool | None, str], tuple[float, list[PlaylistTrackView]]
        ] = {}
        self._ytm_auto_match_lock = threading.Lock()
        self._ytm_auto_match_next_at = 0.0
        # Injected clients are test/manual adapters; the production client is
        # always throttled. This also keeps existing deterministic tests fast.
        self._ytm_auto_match_interval = (
            0.0 if ytmusic_client is not None else _YTM_AUTO_MATCH_MIN_INTERVAL_SECONDS
        )

    def bind_enrichment(self, enrichment: object | None) -> None:
        """Optional LibraryEnrichmentService for per-playlist enrich during sync."""
        self._enrichment = enrichment

    def bind_wanted_service(self, wanted: object | None) -> None:
        """Optional WantedService for migrating meta-verified unmatched rows."""
        self._wanted = wanted

    def bind_media_changed(self, callback: Callable[[], None] | None) -> None:
        """Mark global statistics stale after a background inventory completes."""
        self._media_changed = callback

    def clear_match_cooldowns(self, *, include_rejected: bool = False) -> int:
        """Clear match backoff (and optionally requeue rejected junk)."""
        return self._repository.clear_match_cooldowns(include_rejected=include_rejected)

    def find_strict_raw_path(
        self,
        *,
        title_norm: str,
        artist_norm: str,
        album_norm: str,
    ) -> Path | None:
        """Resolve a strict Raw candidate from the persistent metadata index."""
        for row in self._repository.find_strict_metadata_rows(
            title_norm=title_norm,
            artist_norm=artist_norm,
            album_norm=album_norm,
        ):
            path = EXTERNAL_RAW_ROOT / row.rel_path
            if path.is_file():
                return path
        return None

    def _wait_for_ytm_auto_match_slot(self) -> None:
        """Reserve one globally paced YTM search slot for background matching."""
        if self._ytm_auto_match_interval <= 0:
            return
        with self._ytm_auto_match_lock:
            now = time.monotonic()
            wait = max(0.0, self._ytm_auto_match_next_at - now)
            self._ytm_auto_match_next_at = max(now, self._ytm_auto_match_next_at) + (
                self._ytm_auto_match_interval
            )
        if wait:
            time.sleep(wait)

    # -- Playlists --

    def sync_playlists_from_disk(self, *, maintenance: bool = False) -> list[str]:
        """Upsert one ExternalPlaylist per top-level directory under Raw/.

        Playlist rows whose folders disappeared are cancelled (户口注销): tracks
        stamped with that playlist_uid are liberated (writable, no origin).
        """
        with self._lock:
            return self._sync_playlists_from_disk(maintenance=maintenance)

    def _sync_playlists_from_disk(self, *, maintenance: bool = False) -> list[str]:
        """Locked implementation of playlist discovery and legacy repair."""
        ensure_external_layout()
        root = EXTERNAL_RAW_ROOT
        if not root.is_dir():
            return []
        try:
            dirs = sorted(
                p.name
                for p in root.iterdir()
                if p.is_dir() and not p.name.startswith(".")
            )
        except OSError:
            return []
        default_mode = str(
            getattr(
                self._preferences.effective(),
                "external_new_playlist_mode",
                EXTERNAL_ACCESS_PENDING,
            )
        )
        if default_mode not in EXTERNAL_ACCESS_MODES:
            default_mode = EXTERNAL_ACCESS_PENDING
        for dir_name in dirs:
            is_system = dir_name in (EXTERNAL_DEFAULT_DIR, EXTERNAL_DELETE_DIR)
            mode = EXTERNAL_ACCESS_MANAGED if is_system else default_mode
            self._repository.upsert_playlist(
                dir_name,
                access_mode=mode,
                enabled=mode != EXTERNAL_ACCESS_PENDING,
            )
        if maintenance:
            self._reconcile_legacy_recycle_organized(set(dirs))
        removed = self._repository.delete_playlists_not_in(
            set(dirs),
            protected={EXTERNAL_DEFAULT_DIR, EXTERNAL_DELETE_DIR},
        )
        for removed_dir_name, playlist_uid in removed:
            self._repository.delete_inventory_for_dir(removed_dir_name)
            self._repository.delete_paths_for_dir(removed_dir_name)
            liberated = self._catalog.liberate_origin(playlist_uid)
            if liberated:
                logger.info(
                    "Liberated %d track(s) after playlist hukou cancelled (%s)",
                    liberated,
                    playlist_uid,
                )
        self._schedule_playlist_inventories()
        return dirs

    def _legacy_recycle_origin(
        self,
        location: TrackLocation,
        record: TrackRecord,
        active_dirs: set[str],
    ) -> tuple[str, str]:
        """Resolve durable provenance for an old Organized/Delete location."""
        if record.origin_playlist_uid:
            source = self._repository.get_playlist_by_uid(record.origin_playlist_uid)
            if source is not None and source.dir_name in active_dirs:
                return "external", record.origin_playlist_uid

        origin = (location.origin or "").lower()
        if "wanted" in origin:
            return "wanted", record.video_id
        if "subscription" in origin or "sublist" in origin:
            return "subscription", record.video_id
        if "direct" in origin or origin in {"download", "search"}:
            return "direct", record.video_id
        # A joined catalog row is still attributable even when an old version
        # did not preserve its original product lane.
        return "system", f"catalog:{record.video_id}"

    def _reconcile_legacy_recycle_organized(
        self,
        active_dirs: set[str],
    ) -> None:
        """Collapse legacy Organized/Delete into canonical Raw/Delete.

        Tracked files retain explicit provenance and become unmatched recycle
        items. Untracked files are invalid development residue and are removed.
        A failed move leaves remaining files untouched for a later retry.
        """
        root = EXTERNAL_ORGANIZED_ROOT / EXTERNAL_DELETE_DIR
        if not root.is_dir():
            return

        save_folder = organized_save_folder(EXTERNAL_DELETE_DIR)
        tracked = {
            loc.relative_path.replace("\\", "/"): (loc, rec)
            for loc, rec in self._catalog.list_for_save_folder(save_folder)
        }
        moved = deleted = stale = errors = 0

        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix.lower() not in AUDIO_SUFFIXES:
                    continue
                try:
                    relative = str(path.relative_to(root)).replace("\\", "/")
                except ValueError:
                    continue
                loc_record = tracked.pop(relative, None)
                if loc_record is None:
                    try:
                        path.unlink()
                        path.with_suffix(".lrc").unlink(missing_ok=True)
                        deleted += 1
                    except OSError:
                        errors += 1
                        logger.warning(
                            "Could not delete unattributed Organized/Delete file %s",
                            path,
                        )
                    continue

                location, record = loc_record
                origin_kind, origin_ref = self._legacy_recycle_origin(
                    location,
                    record,
                    active_dirs,
                )
                try:
                    destination = self.ingest_file_to_raw_delete(
                        path,
                        origin_kind=origin_kind,
                        origin_ref=origin_ref,
                        title=record.title,
                        artists=record.artist,
                        album=record.album,
                        album_artist=record.album_artist,
                        year=record.year,
                        track_number=record.track_number,
                    )
                    if destination is None:
                        errors += 1
                        continue
                    self._catalog.delete_location(save_folder, relative)
                    moved += 1
                except OSError:
                    errors += 1
                    logger.warning(
                        "Could not migrate Organized/Delete file %s",
                        path,
                        exc_info=True,
                    )

        # Catalog rows whose old files no longer exist must not keep the
        # recycle-center card pointing at Organized/Delete.
        for relative, (_location, _record) in tracked.items():
            self._catalog.delete_location(save_folder, relative)
            stale += 1

        if errors == 0:
            # Remove orphan covers, sidecars and now-empty directories only
            # after every audio migration/deletion succeeded.
            for dirpath, dirnames, filenames in os.walk(root, topdown=False):
                directory = Path(dirpath)
                for name in filenames:
                    try:
                        (directory / name).unlink()
                    except OSError:
                        logger.warning(
                            "Could not remove legacy recycle sidecar %s",
                            directory / name,
                        )
                for name in dirnames:
                    try:
                        (directory / name).rmdir()
                    except OSError:
                        pass
            try:
                root.rmdir()
            except OSError:
                pass

        if moved or deleted or stale or errors:
            logger.info(
                "Reconciled Organized/Delete: moved=%d deleted=%d stale=%d errors=%d",
                moved,
                deleted,
                stale,
                errors,
            )

    def _inventory_candidates(self) -> list[str]:
        return [
            row.dir_name
            for row in self._repository.list_playlists()
            if row.dir_name not in (EXTERNAL_DEFAULT_DIR, EXTERNAL_DELETE_DIR)
            and row.inventory_scanned_at is None
        ]

    def _schedule_playlist_inventories(self) -> None:
        """Inventory unscanned folders after cards have already been returned."""
        with self._inventory_lock:
            if self._inventory_worker_running:
                return
            if not self._inventory_candidates():
                return
            self._inventory_worker_running = True
        threading.Thread(
            target=self._playlist_inventory_worker,
            name="external-playlist-inventory",
            daemon=True,
        ).start()

    def _playlist_inventory_worker(self) -> None:
        """Inventory folders one at a time to avoid hammering SMB."""
        try:
            while True:
                candidates = self._inventory_candidates()
                if not candidates:
                    return
                with self._lock:
                    if not self._inventory_playlist_folder(candidates[0]):
                        return
        finally:
            with self._inventory_lock:
                self._inventory_worker_running = False

    def _inventory_playlist_folder(self, dir_name: str) -> bool:
        """Persist a cheap path/stat inventory without reading audio tags."""
        root = EXTERNAL_RAW_ROOT / dir_name
        count = 0
        representative: str | None = None
        batch: list[tuple[str, str, int, int, int | None]] = []
        try:
            if root.is_dir():
                for dirpath, dirnames, filenames in os.walk(root):
                    dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
                    for name in sorted(filenames):
                        if name.startswith(".") or Path(name).suffix.lower() not in (
                            AUDIO_SUFFIXES
                        ):
                            continue
                        path = Path(dirpath) / name
                        count += 1
                        try:
                            st = path.stat()
                        except OSError:
                            continue
                        rel_path = str(path.relative_to(EXTERNAL_RAW_ROOT)).replace(
                            "\\", "/"
                        )
                        batch.append(
                            (
                                rel_path,
                                dir_name,
                                getattr(
                                    st,
                                    "st_mtime_ns",
                                    int(st.st_mtime * 1e9),
                                ),
                                int(st.st_size),
                                getattr(st, "st_ino", None),
                            )
                        )
                        if len(batch) >= _INVENTORY_WRITE_BATCH:
                            self._repository.upsert_inventory_batch(batch)
                            batch.clear()
                        if representative is None:
                            representative = rel_path
                            self._repository.record_inventory_representative(
                                dir_name,
                                cover_rel=representative,
                            )
            if batch:
                self._repository.upsert_inventory_batch(batch)
            self._repository.record_inventory(
                dir_name,
                audio_count=count,
                cover_rel=representative,
            )
            if self._media_changed is not None:
                self._media_changed()
            return True
        except OSError:
            logger.warning("External inventory failed for %s", dir_name)
            return False

    # Compatibility for focused tests and older internal callers.
    def _inventory_pending_folder(self, dir_name: str) -> bool:
        return self._inventory_playlist_folder(dir_name)

    def _purge_unattributed_archive_files(self) -> None:
        """Delete legacy/manual archive files that have no trustworthy source.

        Raw/Delete is only a Yubal-managed destination.  Keeping a file there
        without provenance would make its permission ambiguous, so it is
        invalid data rather than a read-only fallback.
        """
        archive_root = EXTERNAL_RAW_ROOT / EXTERNAL_DELETE_DIR
        if not archive_root.is_dir():
            return
        for dirpath, _dirnames, filenames in os.walk(archive_root):
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix.lower() not in AUDIO_SUFFIXES:
                    continue
                try:
                    rel_path = str(
                        path.resolve().relative_to(EXTERNAL_RAW_ROOT.resolve())
                    )
                except ValueError:
                    continue
                row = self._repository.get(rel_path)
                if row is not None and row.origin_kind and row.origin_ref:
                    continue
                logger.warning(
                    "Preserving unattributed archive file for manual recovery: %s",
                    path,
                )

    def _raw_row_is_mutable(self, row: ExternalRawTrack) -> bool:
        """Resolve permission from immutable source provenance, never its folder."""
        if row.origin_kind == "external":
            source = self._repository.get_playlist_by_uid(row.origin_ref)
            return bool(source and source.allow_mutate)
        # Download / subscription / system archive sources are application-owned.
        return row.origin_kind in {
            "direct",
            "manual",
            "subscription",
            "system",
            "wanted",
        }

    def _playlist_origin(self, dir_name: str) -> tuple[str, str]:
        if dir_name == EXTERNAL_DEFAULT_DIR:
            return ("manual", "archive")
        playlist = self._repository.get_playlist(dir_name)
        if playlist is None:
            raise ValueError(f"playlist not found: {dir_name}")
        return ("external", playlist.playlist_uid)

    def list_playlists(self) -> list[ExternalPlaylistView]:
        return [
            self._build_playlist_view(playlist)
            for playlist in self._repository.list_playlists()
        ]

    def get_playlist_view(self, dir_name: str) -> ExternalPlaylistView | None:
        playlist = self._repository.get_playlist(dir_name)
        if playlist is None:
            return None
        return self._build_playlist_view(playlist)

    def record_playlist_sync_status(self, dir_name: str, *, status: str) -> None:
        """Persist the final status after the shared sync core finishes."""
        self._repository.record_sync(dir_name, status=status)

    def inode_allows_asset_embedding(self, path: Path) -> bool:
        """Whether a hardlinked file may change the shared audio inode.

        Wanted files can be hardlinks to External/Raw. Resolve the originating
        playlist by inode so a readonly source never gets modified indirectly.
        App-owned sidecars remain allowed.
        """
        try:
            target = path.stat()
        except OSError:
            return False
        for playlist in self._repository.list_playlists():
            for row in self._repository.list_for_dir(playlist.dir_name):
                candidate = EXTERNAL_RAW_ROOT / row.rel_path
                try:
                    stat = candidate.stat()
                except OSError:
                    continue
                if stat.st_dev == target.st_dev and stat.st_ino == target.st_ino:
                    return bool(playlist.allow_mutate)
        return True

    def _build_playlist_view(self, playlist: ExternalPlaylist) -> ExternalPlaylistView:
        save_folder = organized_save_folder(playlist.dir_name)
        (
            matched_count,
            local,
            offline,
            hardlink,
            representative,
        ) = self._catalog.folder_snapshot(save_folder)
        unmatched_count = self._repository.count_unmatched_for_dir(playlist.dir_name)
        indexed_raw_count = self._repository.count_for_dir(playlist.dir_name)
        inventory_count = self._repository.count_inventory_for_dir(playlist.dir_name)
        # Paths discovered but not yet tag-indexed are honest pending items,
        # not invisible zeroes. They naturally move into the existing buckets
        # as the background stock queue advances.
        unmatched_count += max(0, inventory_count - indexed_raw_count)
        inventory_scanned = playlist.inventory_scanned_at is not None
        if playlist.discovered_audio_count is not None and (
            playlist.access_mode == EXTERNAL_ACCESS_PENDING or indexed_raw_count == 0
        ):
            unmatched_count = int(playlist.discovered_audio_count)
        meta_verified_count = self._repository.count_meta_verified_unmatched(
            playlist.dir_name
        )
        meta_rejected_count = self._repository.count_meta_rejected_for_dir(
            playlist.dir_name
        )
        if playlist.dir_name in (EXTERNAL_DEFAULT_DIR, EXTERNAL_DELETE_DIR):
            # System pits may contain mixed origins, so preserve per-row hukou.
            meta_rejected_mutable_count = sum(
                1
                for row in self._repository.list_meta_rejected_for_dir(
                    playlist.dir_name
                )
                if self._raw_row_is_mutable(row)
            )
        else:
            meta_rejected_mutable_count = (
                meta_rejected_count if playlist.allow_mutate else 0
            )

        cloud = local
        # Prefer an inventory-confirmed Raw file with embedded artwork.  The
        # previous single "latest track" choice could select a coverless file,
        # while discovered_cover_rel could point at a file already moved away.
        raw_cover_rel = self._repository.inventory_cover_path(playlist.dir_name)
        if raw_cover_rel:
            cover_track_path = f"External/raw/{raw_cover_rel}".replace("\\", "/")
        elif representative:
            cover_track_path = f"External/{save_folder}/{representative}".replace(
                "\\", "/"
            )
        else:
            raw_fallback = self._repository.first_inventory_path(playlist.dir_name)
            cover_track_path = (
                f"External/raw/{raw_fallback}".replace("\\", "/")
                if raw_fallback
                else None
            )

        return ExternalPlaylistView(
            dir_name=playlist.dir_name,
            allow_mutate=playlist.allow_mutate,
            access_mode=playlist.access_mode,
            access_mode_locked=playlist.source_mutated_at is not None,
            source_mutated_at=playlist.source_mutated_at,
            source_mutation_kind=playlist.source_mutation_kind,
            show_raw=playlist.show_raw,
            show_junk=bool(playlist.show_raw and playlist.show_junk),
            inventory_scanned=inventory_scanned,
            unmatched_count=unmatched_count,
            matched_count=matched_count,
            meta_verified_count=meta_verified_count,
            meta_rejected_count=meta_rejected_count,
            meta_rejected_mutable_count=meta_rejected_mutable_count,
            cloud=cloud,
            local=local,
            offline=offline,
            exclusive=local - hardlink,
            shared=0,
            hardlink=hardlink,
            cover_track_path=cover_track_path,
            enabled=playlist.enabled,
            max_items=playlist.max_items,
            sync_jitter_seconds=playlist.sync_jitter_seconds,
            offline_marking_enabled=playlist.offline_marking_enabled,
            offline_cleanup_enabled=playlist.offline_cleanup_enabled,
            offline_cleanup_action=playlist.offline_cleanup_action or "archive",
            offline_cleanup_delay_hours=int(playlist.offline_cleanup_delay_hours or 72),
            last_synced_at=playlist.last_synced_at,
            last_sync_status=playlist.last_sync_status,
        )

    def update_playlist_settings(
        self,
        dir_name: str,
        *,
        allow_mutate: bool | None = None,
        access_mode: str | None = None,
        show_raw: bool | None = None,
        show_junk: bool | None = None,
        enabled: bool | None = None,
        max_items: int | None = None,
        sync_jitter_seconds: int | None = None,
        offline_marking_enabled: bool | None = None,
        offline_cleanup_enabled: bool | None = None,
        offline_cleanup_action: str | None = None,
        offline_cleanup_delay_hours: int | None = None,
    ) -> ExternalPlaylist | None:
        playlist = self._repository.get_playlist(dir_name)
        if playlist is None:
            return None
        # Default / Delete are pinned writable; refuse toggling mutate.
        if dir_name in (EXTERNAL_DEFAULT_DIR, EXTERNAL_DELETE_DIR):
            if allow_mutate is False:
                raise ValueError(
                    f"{dir_name} is a system playlist and must stay writable"
                )
            allow_mutate = True if allow_mutate is not None else None
            access_mode = EXTERNAL_ACCESS_MANAGED

        requested_mode = access_mode
        if requested_mode is None and allow_mutate is not None:
            requested_mode = EXTERNAL_ACCESS_MANAGED if allow_mutate else "readonly"
        if (
            requested_mode == EXTERNAL_ACCESS_PENDING
            and playlist.access_mode != EXTERNAL_ACCESS_PENDING
        ):
            raise ValueError("a configured playlist cannot return to pending")
        if (
            playlist.source_mutated_at is not None
            and requested_mode is not None
            and requested_mode != playlist.access_mode
        ):
            raise ValueError(
                "access mode is locked because original source content "
                "has already been changed"
            )

        prev_mutate = playlist.allow_mutate
        updated = self._repository.update_playlist_settings(
            dir_name,
            allow_mutate=allow_mutate,
            access_mode=access_mode,
            show_raw=show_raw,
            show_junk=show_junk,
            enabled=enabled,
            max_items=max_items,
            sync_jitter_seconds=sync_jitter_seconds,
            offline_marking_enabled=offline_marking_enabled,
            offline_cleanup_enabled=offline_cleanup_enabled,
            offline_cleanup_action=offline_cleanup_action,
            offline_cleanup_delay_hours=offline_cleanup_delay_hours,
        )
        if updated is None:
            return None
        if updated.allow_mutate != prev_mutate:
            flipped = self._catalog.set_immutable_for_origin(
                updated.playlist_uid,
                immutable=not updated.allow_mutate,
            )
            if flipped:
                logger.info(
                    "Flipped immutable on %d track(s) for playlist %s (mutate=%s)",
                    flipped,
                    dir_name,
                    updated.allow_mutate,
                )
        return updated

    def _mark_source_mutated(
        self,
        *,
        playlist_uid: str | None,
        mutation_kind: str,
    ) -> None:
        """Lock a real external source; app-owned/system sources need no lock."""
        if not playlist_uid:
            return
        playlist = self._repository.get_playlist_by_uid(playlist_uid)
        if playlist is None:
            return
        if playlist.dir_name in (EXTERNAL_DEFAULT_DIR, EXTERNAL_DELETE_DIR):
            return
        self._repository.mark_source_mutated(
            playlist_uid,
            mutation_kind=mutation_kind,
        )

    def _mark_row_source_mutated(
        self,
        row: ExternalRawTrack,
        mutation_kind: str,
    ) -> None:
        if row.origin_kind == "external":
            self._mark_source_mutated(
                playlist_uid=row.origin_ref,
                mutation_kind=mutation_kind,
            )

    def _mark_catalog_source_mutated(
        self,
        record: object,
        mutation_kind: str,
    ) -> None:
        self._mark_source_mutated(
            playlist_uid=getattr(record, "origin_playlist_uid", None),
            mutation_kind=mutation_kind,
        )

    @staticmethod
    def _existing_file_signature(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    @classmethod
    def _existing_file_changed(
        cls,
        path: Path,
        before: tuple[int, int] | None,
    ) -> bool:
        """New files are reversible additions; only changed existing files lock."""
        if before is None:
            return False
        return cls._existing_file_signature(path) != before

    def activate_pending_playlist_names(self, access_mode: str) -> list[str]:
        """Classify new folders and return exactly those made processable."""
        if access_mode not in {"readonly", EXTERNAL_ACCESS_MANAGED}:
            raise ValueError("access mode must be readonly or managed")
        activated: list[str] = []
        for playlist in self._repository.list_playlists():
            if playlist.access_mode != EXTERNAL_ACCESS_PENDING:
                continue
            updated = self._repository.update_playlist_settings(
                playlist.dir_name,
                access_mode=access_mode,
                enabled=True,
            )
            if updated is not None:
                activated.append(updated.dir_name)
        return activated

    def activate_pending_playlists(self, access_mode: str) -> int:
        """Compatibility count for callers that do not need activated names."""
        return len(self.activate_pending_playlist_names(access_mode))

    def configured_playlist_names(self) -> list[str]:
        """Return external folders eligible for media processing."""
        return [
            playlist.dir_name
            for playlist in self._repository.list_playlists()
            if playlist.enabled
            and playlist.access_mode != EXTERNAL_ACCESS_PENDING
        ]

    def pending_processing_count(self, dir_name: str) -> int:
        return self._repository.pending_processing_count(
            dir_name,
            now=datetime.now(UTC),
        )

    def _batch_limit(self, playlist: ExternalPlaylist, *, dir_name: str) -> int:
        """Scale per-pass work with backlog so large libraries drain faster."""
        base = max(int(playlist.max_items or 50), 20)
        pending = self.pending_processing_count(dir_name)
        if pending >= 10_000:
            return min(500, max(base, pending // 80))
        if pending >= 1_000:
            return min(200, max(base, pending // 40))
        return base

    # -- Scan --

    def scan_raw(
        self,
        health: LibraryHealthService,
        *,
        enabled_only: bool = False,
        dir_name: str | None = None,
        metadata_limit: int | None = None,
    ) -> ScanResult:
        """Discover filesystem changes, then parse a bounded metadata batch.

        Discovery only stats files and updates the persistent inventory. Expensive
        tag reads are a separate resumable queue. ``metadata_limit=None`` keeps
        the explicit scan API backwards-compatible by draining the queue; normal
        scheduled/playlist sync passes a finite budget.
        """
        discovered = self.discover_raw(
            health,
            enabled_only=enabled_only,
            dir_name=dir_name,
        )
        indexed, index_errors = self.index_inventory_batch(
            limit=metadata_limit,
            enabled_only=enabled_only,
            dir_name=dir_name,
        )
        discovered.updated += indexed
        discovered.errors += index_errors
        return discovered

    def discover_raw(
        self,
        health: LibraryHealthService,
        *,
        enabled_only: bool = False,
        dir_name: str | None = None,
    ) -> ScanResult:
        """Cheap full reconciliation: path/stat only, never opens audio tags."""
        health.ensure_healthy()
        ensure_external_layout()
        root = EXTERNAL_RAW_ROOT
        if not root.is_dir():
            raise ValueError(f"external raw root missing: {root}")

        with self._lock:
            dirs = self.sync_playlists_from_disk(maintenance=True)
            self._repository.refresh_all_norms()

            allowed_dirs: set[str] | None = None
            if dir_name is not None:
                allowed_dirs = {dir_name}
            elif enabled_only:
                allowed_dirs = self._repository.list_enabled_dir_names()
                if not allowed_dirs:
                    logger.info("External scan_raw: no enabled playlists; skipping")
                    return ScanResult(playlists=0)

            existing = self._repository.list_inventory_path_stats()
            seen: set[str] = set()
            added = updated = errors = scanned = 0
            inventory_batch: list[tuple[str, str, int, int, int | None]] = []

            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                # Restrict top-level playlist dirs when filtering.
                try:
                    rel_root = str(
                        Path(dirpath).resolve().relative_to(root.resolve())
                    ).replace("\\", "/")
                except ValueError:
                    continue
                if rel_root == ".":
                    rel_root = ""
                if not rel_root and allowed_dirs is not None:
                    dirnames[:] = [d for d in dirnames if d in allowed_dirs]
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
                    parts = rel.split("/")
                    if len(parts) < 2:
                        continue  # file directly under Raw/, not in a playlist dir
                    playlist_dir = parts[0]
                    if allowed_dirs is not None and playlist_dir not in allowed_dirs:
                        continue
                    # Raw/Delete is not imported automatically. Preserve unknown
                    # files for manual recovery rather than deleting them merely
                    # because the DB was reset or an older migration was partial.
                    if (
                        playlist_dir == EXTERNAL_DELETE_DIR
                        and self._repository.get(rel) is None
                    ):
                        logger.warning(
                            "Skipping unattributed archive file pending "
                            "manual recovery: %s",
                            path,
                        )
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
                    inode = getattr(st, "st_ino", None)
                    prev = existing.get(rel)
                    if prev is not None and prev == (mtime_ns, size, inode):
                        continue
                    inventory_batch.append(
                        (
                            rel,
                            playlist_dir,
                            int(mtime_ns),
                            int(size),
                            inode,
                        )
                    )
                    if len(inventory_batch) >= _INVENTORY_WRITE_BATCH:
                        part_added, part_updated = (
                            self._repository.upsert_inventory_batch(inventory_batch)
                        )
                        added += part_added
                        updated += part_updated
                        inventory_batch.clear()

            if inventory_batch:
                part_added, part_updated = self._repository.upsert_inventory_batch(
                    inventory_batch
                )
                added += part_added
                updated += part_updated

            # Only consider deletes within the scanned scope.
            if allowed_dirs is not None:
                scoped_existing = [
                    p for p in existing if p.split("/", 1)[0] in allowed_dirs
                ]
            else:
                scoped_existing = list(existing)
            removed_paths = [p for p in scoped_existing if p not in seen]
            scoped_scan = allowed_dirs is not None
            allow_deletes = (
                health.allow_scoped_index_deletes(
                    len(seen),
                    len(scoped_existing),
                )
                if scoped_scan
                else health.allow_index_deletes(len(seen))
            )
            removed = 0
            if allow_deletes:
                if removed_paths:
                    removed = self._repository.delete_inventory_paths(removed_paths)
                    self._repository.delete_paths(removed_paths)
                # A single-playlist count must never overwrite the persisted
                # whole-library baseline.
                if not scoped_scan:
                    health.record_good_raw_scan(len(seen))
            else:
                if scoped_scan:
                    logger.warning(
                        "Skipping scoped external raw index deletes: "
                        "walked %d vs indexed %d",
                        len(seen),
                        len(scoped_existing),
                    )
                else:
                    logger.warning(
                        "Skipping external raw index deletes: "
                        "walked %d vs last good %d",
                        len(seen),
                        health.last_good_raw_count,
                    )

            inventory_dirs = allowed_dirs if allowed_dirs is not None else set(dirs)
            for inventory_dir in inventory_dirs:
                playlist = self._repository.get_playlist(inventory_dir)
                cover_rel = (
                    playlist.discovered_cover_rel if playlist is not None else None
                )
                self._repository.record_inventory(
                    inventory_dir,
                    audio_count=self._repository.count_inventory_for_dir(inventory_dir),
                    cover_rel=cover_rel,
                )

            healed = 0
            if dir_name is not None:
                healed = self.heal_orphan_matches(dir_name)
            elif allowed_dirs is not None:
                for dname in allowed_dirs:
                    healed += self.heal_orphan_matches(dname)
            else:
                healed = self.heal_orphan_matches()

            return ScanResult(
                playlists=len(dirs) if allowed_dirs is None else len(allowed_dirs),
                scanned=scanned,
                added=added,
                updated=updated + healed,
                removed=removed,
                errors=errors,
            )

    def index_inventory_batch(
        self,
        *,
        limit: int | None,
        enabled_only: bool = False,
        dir_name: str | None = None,
    ) -> tuple[int, int]:
        """Read tags for pending inventory rows; safe to resume after interruption."""
        enabled_dirs: set[str] | None = None
        if enabled_only and dir_name is None:
            enabled_dirs = self._repository.list_enabled_dir_names()
        # An explicit unlimited scan drains all currently pending rows. Normal
        # sync calls always provide a bounded budget.
        batch_limit = limit
        if batch_limit is None:
            batch_limit = max(1, self._repository.count())
            inventory_total = sum(
                self._repository.count_inventory_for_dir(name)
                for name in (
                    enabled_dirs
                    if enabled_dirs is not None
                    else [dir_name]
                    if dir_name is not None
                    else [p.dir_name for p in self._repository.list_playlists()]
                )
            )
            batch_limit = max(batch_limit, inventory_total)
        rows = self._repository.list_pending_inventory(
            limit=max(0, int(batch_limit)),
            dir_name=dir_name,
            dir_names=enabled_dirs,
        )
        indexed = errors = 0
        for inventory in rows:
            path = EXTERNAL_RAW_ROOT / inventory.rel_path
            origin_kind, origin_ref = self._playlist_origin(inventory.dir_name)
            row = _read_raw_tags(
                path,
                inventory.rel_path,
                inventory.dir_name,
                origin_kind=origin_kind,
                origin_ref=origin_ref,
            )
            if row is None:
                errors += 1
                continue
            self._repository.upsert(row)
            if self._repository.mark_inventory_indexed(
                inventory.rel_path,
                mtime_ns=inventory.mtime_ns,
            ):
                indexed += 1
        return indexed, errors

    def heal_orphan_matches(self, dir_name: str | None = None) -> int:
        """Re-ingest MATCH_MATCHED raw rows that have no Organized catalog location.

        Prevents scan total (Raw files) from drifting above unmatched+matched UI
        counts after Organized files were moved away without resetting match state.
        """
        healed = 0
        rows = self._repository.list_matched(dir_name)
        by_dir: dict[str, list] = {}
        for row in rows:
            by_dir.setdefault(row.dir_name, []).append(row)
        for dname, matched_rows in by_dir.items():
            save_folder = organized_save_folder(dname)
            present = {
                rec.video_id
                for _, rec in self._catalog.list_for_save_folder(save_folder)
            }
            for row in matched_rows:
                if not row.video_id or row.video_id in present:
                    continue
                try:
                    if self.ingest_matched(row.rel_path):
                        healed += 1
                        present.add(row.video_id)
                except Exception:
                    logger.exception("Failed healing orphan match %s", row.rel_path)
        if healed:
            logger.info("Healed %d orphan external matches", healed)
        return healed

    # -- Matching --

    @staticmethod
    def tags_complete_enough(
        title: str | None, artists: str | None, album: str | None
    ) -> bool:
        """Relaxed-complete tags for immutable ingest / match (plan: album required).

        Lyrics need not be embedded; cover need not be optimal/present.
        """
        return (
            bool((title or "").strip())
            and bool((artists or "").strip())
            and bool((album or "").strip())
        )

    @staticmethod
    def junk_kind_for_row(row: ExternalRawTrack, readonly: bool) -> str | None:
        """Return ``rw`` / ``ro`` junk grade, or None when not junk.

        Metadata verification is authoritative for the Wanted workflow. A row
        that failed YTM matching can still be a valid, verified song and must
        remain in the verified bucket rather than being downgraded to junk.

        - Writable junk (``rw``): ``MATCH_REJECTED`` on a mutable playlist.
        - Readonly junk (``ro``): rejected, or incomplete tags, on readonly.
        Mutable + incomplete tags (not rejected) is ordinary unmatched, not junk.
        """
        if row.meta_status == META_VERIFIED:
            return None
        if row.match_status == MATCH_REJECTED:
            return "ro" if readonly else "rw"
        if readonly and not ExternalLibraryService.tags_complete_enough(
            row.title, row.artists, row.album
        ):
            return "ro"
        return None

    @staticmethod
    def is_junk_row(row: ExternalRawTrack, readonly: bool) -> bool:
        """Junk = rejected, or readonly playlist with incomplete tags."""
        return ExternalLibraryService.junk_kind_for_row(row, readonly) is not None

    @staticmethod
    def quality_key(row: ExternalRawTrack) -> tuple:
        return _quality_key(row)

    def _backoff_seconds(self, fail_count: int) -> int:
        """Linear backoff: fail_count * 24h (no exponential)."""
        return max(0, int(fail_count)) * _BACKOFF_STEP_SECONDS

    def _backoff_cap_days(self) -> int:
        """Days after which cumulative backoff marks the row rejected (junk)."""
        try:
            days = int(self._preferences.effective().match_backoff_cap_days)
        except (AttributeError, TypeError, ValueError):
            days = _DEFAULT_BACKOFF_CAP_DAYS
        return max(1, min(30, days))

    def _should_reject_after_fails(self, fail_count_after: int) -> bool:
        """Reject when delay would exceed the configured cap (default 7 days)."""
        cap_seconds = self._backoff_cap_days() * _BACKOFF_STEP_SECONDS
        return fail_count_after * _BACKOFF_STEP_SECONDS > cap_seconds

    def _record_failure(self, row: ExternalRawTrack, *, rejected: bool) -> None:
        n = row.match_fail_count + 1
        next_at = datetime.now(UTC) + timedelta(seconds=self._backoff_seconds(n))
        self._repository.record_match_failure(
            row.rel_path, next_eligible_at=next_at, rejected=rejected
        )

    def _rank_match_candidates(
        self,
        row: ExternalRawTrack,
        *,
        mode: str,
        rate_limit: bool,
    ) -> list[MatchCandidate]:
        """Search YTM and rank candidates for ``row``.

        Search prefers clean title + primary artist for recall (avoids game/OST
        credit spam). Auto-eligibility follows ``mode``. Picker ranking weights
        title higher than artist so low-title noise ranks down.
        """
        complete = self.tags_complete_enough(row.title, row.artists, row.album)
        relaxed = mode == "relaxed"

        full_title = (row.title or "").strip()
        clean_title = _search_title(full_title)
        artist_parts = _split_artist_names(row.artists or "")
        primary_artist = artist_parts[0] if artist_parts else ""
        all_artists = (row.artists or "").strip()

        # Priority: clean title + primary artist first; album / full-credit last.
        queries: list[str] = []
        if primary_artist and clean_title:
            queries.append(f"{primary_artist} {clean_title}")
            queries.append(f"{clean_title} {primary_artist}")
        if clean_title:
            queries.append(clean_title)
        if full_title and full_title != clean_title:
            if primary_artist:
                queries.append(f"{primary_artist} {full_title}")
            queries.append(full_title)
        # Full credit string is noisy; keep once, after cleaner queries.
        if all_artists and clean_title and all_artists != primary_artist:
            queries.append(f"{primary_artist or all_artists} {clean_title}")
        # Album queries last (often flood game/OST catalogs).
        album_queries: list[str] = []
        if row.album and row.album_artist:
            album_queries.append(f"{row.album_artist} {row.album}".strip())
            album_queries.append(f"{row.album} {row.album_artist}".strip())
        if row.album and clean_title:
            album_queries.append(f"{clean_title} {row.album}".strip())
        if not complete:
            stem = Path(row.rel_path).stem.strip()
            if stem:
                queries.append(_search_title(stem) or stem)

        def _dedupe(seq: list[str]) -> list[str]:
            seen: set[str] = set()
            out: list[str] = []
            for q in seq:
                if q and q not in seen:
                    seen.add(q)
                    out.append(q)
            return out

        primary_queries = _dedupe(queries)
        album_queries = _dedupe(album_queries)
        if not primary_queries and not album_queries:
            return []

        results = []
        search_errors: list[Exception] = []
        successful_searches = 0
        for query in primary_queries:
            try:
                if rate_limit:
                    self._wait_for_ytm_auto_match_slot()
                results.extend(self._client.search_songs(query)[:8])
                successful_searches += 1
            except UpstreamAPIError as exc:
                search_errors.append(exc)
                logger.warning(
                    "External match search unavailable for %s query=%r: %s",
                    row.rel_path,
                    query,
                    exc,
                )
                continue
        for query in album_queries:
            try:
                # Fewer hits from album queries — they pollute with OST noise.
                if rate_limit:
                    self._wait_for_ytm_auto_match_slot()
                results.extend(self._client.search_songs(query)[:4])
                successful_searches += 1
            except UpstreamAPIError as exc:
                search_errors.append(exc)
                logger.warning(
                    "External match search unavailable for %s query=%r: %s",
                    row.rel_path,
                    query,
                    exc,
                )
                continue
        if not successful_searches and search_errors:
            raise UpstreamAPIError(
                f"All YTM search requests failed: {search_errors[-1]}"
            ) from search_errors[-1]
        if not results:
            return []

        by_id: dict[str, object] = {}
        for r in results:
            vid = getattr(r, "video_id", None)
            if vid and vid not in by_id:
                by_id[vid] = r
        results = list(by_id.values())

        target_artists = [Artist(name=part) for part in artist_parts] or [
            Artist(name=all_artists or "Unknown Artist")
        ]
        # Primary-only set for scoring — featuring/game credits shouldn't dominate.
        primary_artists = (
            [Artist(name=primary_artist)] if primary_artist else target_artists
        )
        swapped_artists = [
            Artist(name=part)
            for part in _split_artist_names(full_title)
            if part.strip()
        ] or [Artist(name=full_title or "Unknown Artist")]

        local_has_version = has_version_marker(full_title)
        # Compare titles against clean + full local forms.
        local_titles = [t for t in (clean_title, full_title) if t]
        ranked: list[MatchCandidate] = []

        for result in results:
            cand_title = getattr(result, "title", "") or ""
            cand_artists = " / ".join(
                a.name for a in getattr(result, "artists", []) or [] if a.name
            )
            cand_album = (result.album.name if result.album else "") or ""
            thumbs = getattr(result, "thumbnails", None) or []
            thumb_url = None
            if thumbs:
                best_thumb = max(
                    thumbs,
                    key=lambda t: (
                        (getattr(t, "width", 0) or 0) * (getattr(t, "height", 0) or 0)
                    ),
                )
                thumb_url = getattr(best_thumb, "url", None)

            version_blocked = (
                not relaxed
                and not local_has_version
                and (has_version_marker(cand_title) or has_version_marker(cand_album))
            )

            title_full = 0.0
            title_relaxed = 0.0
            for local_t in local_titles:
                title_m = match_title(local_t, cand_title)
                title_full = max(title_full, title_m.similarity)
                title_relaxed = max(
                    title_relaxed,
                    title_m.similarity,
                    title_m.base_similarity,
                )
            # Swapped title↔artist only as a weak signal (don't let it dominate).
            swapped_title_m = match_title(all_artists, cand_title)
            title_full = max(title_full, swapped_title_m.similarity * 0.5)
            title_relaxed = max(
                title_relaxed,
                swapped_title_m.similarity * 0.5,
                swapped_title_m.base_similarity * 0.5,
            )
            title_for_auto = title_relaxed if relaxed else title_full

            artist_m = match_artists(primary_artists, list(result.artists))
            all_artist_m = match_artists(target_artists, list(result.artists))
            swapped_artist_m = match_artists(swapped_artists, list(result.artists))
            artist_score = max(
                artist_m.best_score,
                all_artist_m.best_score * 0.85,
                swapped_artist_m.best_score * 0.5,
            )

            album_boost = 0.0
            if row.album and cand_album:
                album_m = match_title(row.album, cand_album)
                album_swapped = match_title(row.album_artist or "", cand_album)
                album_boost = (
                    max(
                        album_m.similarity,
                        album_m.base_similarity,
                        album_swapped.similarity,
                        album_swapped.base_similarity,
                    )
                    * 0.05
                )

            # Picker / confidence: title-heavy blend (not min()).
            rank_score = (
                title_relaxed * _RANK_TITLE_WEIGHT
                + artist_score * _RANK_ARTIST_WEIGHT
                + album_boost
            )
            # Studio local: demote Live/DJ/remix candidates in the picker too.
            if version_blocked:
                rank_score *= 0.35

            auto_ok = (
                not version_blocked
                and title_for_auto >= _MATCH_TITLE_THRESHOLD
                and artist_score >= _MATCH_ARTIST_THRESHOLD
            )
            ranked.append(
                MatchCandidate(
                    video_id=result.video_id,
                    title=cand_title,
                    artists=cand_artists,
                    album=cand_album,
                    thumbnail_url=thumb_url,
                    title_score=round(title_relaxed, 1),
                    artist_score=round(artist_score, 1),
                    score=round(max(0.0, min(100.0, rank_score)), 1),
                    auto_ok=auto_ok,
                )
            )

        ranked.sort(key=lambda c: c.score, reverse=True)
        return ranked

    def match_one(
        self,
        row: ExternalRawTrack,
        *,
        strict_tags: bool = True,
        mode: str | None = None,
    ) -> bool:
        """Attempt to match one raw track against YouTube Music.

        Returns True (and records success) on a confident match, else records
        a backed-off failure and returns False.

        ``strict_tags`` (auto/batch): require title+artist+album before search.
        Manual match passes ``strict_tags=False`` so incomplete tags still try
        title/artist/filename queries.

        ``mode``: ``strict`` (default) or ``relaxed``. When omitted, uses the
        ``match_strictness`` preference. Strict rejects Live/DJ candidates for
        studio locals and scores full titles only; relaxed allows base-title
        matches across version suffixes.
        """
        matched, _candidates = self._match_one_with_candidates(
            row, strict_tags=strict_tags, mode=mode
        )
        return matched

    def _match_one_with_candidates(
        self,
        row: ExternalRawTrack,
        *,
        strict_tags: bool = True,
        mode: str | None = None,
        record_failure: bool = True,
        rate_limit: bool = True,
    ) -> tuple[bool, list[MatchCandidate]]:
        if row.video_id and row.match_status == MATCH_MATCHED:
            return True, []

        complete = self.tags_complete_enough(row.title, row.artists, row.album)
        if strict_tags and not complete:
            if record_failure:
                rejected = self._should_reject_after_fails(row.match_fail_count + 1)
                self._record_failure(row, rejected=rejected)
            return False, []

        match_mode = mode or self._preferences.effective().match_strictness or "strict"
        match_mode = match_mode.lower().strip()
        if match_mode not in {"strict", "relaxed"}:
            match_mode = "strict"

        try:
            ranked = self._rank_match_candidates(
                row, mode=match_mode, rate_limit=rate_limit
            )
        except UpstreamAPIError as exc:
            logger.warning("External match deferred for %s: %s", row.rel_path, exc)
            return False, []
        if not ranked:
            if record_failure:
                rejected = self._should_reject_after_fails(row.match_fail_count + 1)
                self._record_failure(row, rejected=rejected)
            return False, []

        auto = next((c for c in ranked if c.auto_ok), None)
        if auto is not None:
            self._repository.record_match_success(
                row.rel_path,
                video_id=auto.video_id,
                confidence=auto.score,
            )
            return True, []

        picker = [c for c in ranked if c.title_score >= _MANUAL_CANDIDATE_MIN_TITLE][
            :_MANUAL_CANDIDATE_LIMIT
        ]
        # Do not fall back to low-title noise; empty picker is preferable.

        if record_failure:
            rejected = self._should_reject_after_fails(row.match_fail_count + 1)
            self._record_failure(row, rejected=rejected)
        return False, picker

    def match_batch(
        self,
        health: LibraryHealthService,
        *,
        limit: int = 25,
        dir_name: str | None = None,
        ignore_backoff: bool = False,
        include_junk: bool = False,
        junk_only: bool = False,
        enabled_only: bool = True,
        only_rel_paths: set[str] | frozenset[str] | None = None,
    ) -> MatchBatchResult:
        """Attempt YTM matches for a batch of raw tracks.

        ``ignore_backoff`` defaults False. Sync All / scheduled pipeline must
        leave it False (cooldown honored). Prefer reset_match for immediate
        single-track retries.

        When ``junk_only`` is True, skip rows that are not junk. When
        ``include_junk`` is False (and not junk_only), skip junk as usual.
        ``only_rel_paths``, when set, restricts to those Raw relative paths
        (used after index-only scrape so former junk still matches).
        """
        health.ensure_healthy()
        now = datetime.now(UTC)

        enabled_dirs: set[str] | None = None
        if enabled_only and dir_name is None:
            enabled_dirs = self._repository.list_enabled_dir_names()
            if not enabled_dirs:
                logger.info("External match_batch: no enabled playlists; skipping")
                return MatchBatchResult()

        if dir_name is not None and enabled_only:
            playlist = self._repository.get_playlist(dir_name)
            if playlist is not None and not playlist.enabled:
                logger.info(
                    "External match_batch: skipping disabled playlist %s",
                    dir_name,
                )
                return MatchBatchResult()

        # When restricting to a known path set (e.g. junk scrape targets),
        # overshoot the SQL limit so filtered rows still fill ``limit``.
        fetch_limit = limit
        if only_rel_paths is not None:
            fetch_limit = max(limit, len(only_rel_paths), 200)

        rows = self._repository.list_matchable(
            now=now,
            limit=fetch_limit,
            dir_name=dir_name,
            ignore_backoff=ignore_backoff,
            dir_names=enabled_dirs,
        )
        readonly_cache: dict[str, bool] = {}
        enabled_cache: dict[str, bool] = {}
        if dir_name:
            playlist = self._repository.get_playlist(dir_name)
            readonly_cache[dir_name] = (
                playlist is not None and not playlist.allow_mutate
            )
            enabled_cache[dir_name] = playlist is not None and playlist.enabled
        result = MatchBatchResult()
        for row in rows:
            if only_rel_paths is not None and row.rel_path not in only_rel_paths:
                continue

            if enabled_only and dir_name is None:
                enabled = enabled_cache.get(row.dir_name)
                if enabled is None:
                    playlist = self._repository.get_playlist(row.dir_name)
                    enabled = playlist is not None and playlist.enabled
                    enabled_cache[row.dir_name] = enabled
                    readonly_cache.setdefault(
                        row.dir_name,
                        playlist is not None and not playlist.allow_mutate,
                    )
                if not enabled:
                    logger.info(
                        "External match skip disabled playlist track %s",
                        row.rel_path,
                    )
                    continue

            readonly = readonly_cache.get(row.dir_name)
            if readonly is None:
                playlist = self._repository.get_playlist(row.dir_name)
                readonly = playlist is not None and not playlist.allow_mutate
                readonly_cache[row.dir_name] = readonly

            is_junk = self.is_junk_row(row, readonly)
            if junk_only:
                if not is_junk:
                    logger.info(
                        "External match skip non-junk %s (junk_only)",
                        row.rel_path,
                    )
                    continue
            elif not include_junk and is_junk:
                logger.info(
                    "External match skip junk %s (status=%s readonly=%s)",
                    row.rel_path,
                    row.match_status,
                    readonly,
                )
                continue

            if not ignore_backoff and row.match_next_eligible_at is not None:
                eligible = row.match_next_eligible_at
                if eligible.tzinfo is None:
                    eligible = eligible.replace(tzinfo=UTC)
                if eligible > now:
                    logger.info(
                        "External match skip cooldown %s until %s",
                        row.rel_path,
                        eligible.isoformat(),
                    )
                    continue

            result.checked += 1
            try:
                matched = self.match_one(row)
            except (UpstreamAPIError, OSError) as e:
                # ytmusicapi/requests can surface TLS EOF as OSError.  This is
                # a remote transport failure before any Raw→Organized write;
                # never reset a previously confirmed match for it.
                result.deferred += 1
                logger.warning(
                    "External match transport error for %s: %s",
                    row.rel_path,
                    e,
                )
                continue
            except Exception:
                # Matching failed before ingest. Preserve the row so a later
                # scheduled pass can retry; resetting here loses valid state.
                result.deferred += 1
                logger.exception(
                    "External match failed before ingest for %s",
                    row.rel_path,
                )
                continue

            if not matched:
                refreshed = self._repository.get(row.rel_path)
                if refreshed and refreshed.match_status == MATCH_REJECTED:
                    result.rejected += 1
                else:
                    result.deferred += 1
                continue

            try:
                ingested = self.ingest_matched(row.rel_path)
            except OSError as e:
                # Only this branch has touched Organized. A real filesystem
                # failure may be retried from Raw, so roll back the match.
                result.errors += 1
                self._repository.reset_match_state(row.rel_path)
                logger.exception(
                    "External Organized write failed for %s: %s",
                    row.rel_path,
                    e,
                )
            except Exception:
                result.errors += 1
                self._repository.reset_match_state(row.rel_path)
                logger.exception("External match ingest failed for %s", row.rel_path)
            else:
                if ingested:
                    result.matched += 1
                else:
                    # Matched on YTM but could not place into Organized — roll back
                    # so the row stays visible as unmatched and can be retried.
                    self._repository.reset_match_state(row.rel_path)
                    result.errors += 1
                    logger.error(
                        "External match ingest failed for %s (YTM matched but "
                        "could not write Organized; check /External/Organized "
                        "permissions)",
                        row.rel_path,
                    )
            if result.checked >= limit:
                break
        return result

    def sync_playlist(
        self,
        dir_name: str,
        health: LibraryHealthService,
        *,
        enrich: bool = False,
        raw_match: bool = True,
        verify_meta: bool = True,
        junk_match: bool = False,
    ) -> SyncPlaylistResult:
        """Sync one external playlist with selectable steps.

        Order (when flags on)::

            scan → fill empty tags (QQ/MB) → meta-verify still-unmatched
            → YTM match (managed sources only) → cover/lyrics enrich → recover

        At least one of ``enrich`` / ``raw_match`` / ``verify_meta`` /
        ``junk_match`` is required.
        """
        if not (enrich or raw_match or verify_meta or junk_match):
            raise ValueError(
                "at least one of enrich, raw_match, verify_meta, junk_match is required"
            )

        playlist = self._repository.get_playlist(dir_name)
        if playlist is None:
            raise ValueError(f"playlist not found: {dir_name}")
        if playlist.access_mode == EXTERNAL_ACCESS_PENDING:
            raise ValueError(
                "playlist access mode is pending; choose read-only or managed first"
            )

        result = SyncPlaylistResult()
        status = "success"
        try:
            health.ensure_healthy()
            readonly = not playlist.allow_mutate
            batch_limit = self._batch_limit(playlist, dir_name=dir_name)

            needs_index = raw_match or verify_meta
            if needs_index:
                self.index_inventory_batch(
                    limit=batch_limit,
                    dir_name=dir_name,
                )
                self.fill_empty_tags_batch(
                    dir_name,
                    limit=batch_limit,
                    write_file=bool(playlist.allow_mutate),
                )

            if verify_meta:
                meta_result = self.verify_meta_batch(
                    dir_name,
                    limit=batch_limit,
                    write_file=bool(playlist.allow_mutate),
                    force=False,
                )
                result.meta_checked += int(meta_result.get("checked", 0))
                result.meta_verified += int(meta_result.get("verified", 0))

            if raw_match and playlist.allow_mutate:
                match_result = self.match_batch(
                    health,
                    limit=batch_limit,
                    dir_name=dir_name,
                    ignore_backoff=False,
                    include_junk=False,
                    junk_only=False,
                    enabled_only=False,
                )
                result.matched += match_result.matched
                result.checked += match_result.checked
                result.deferred += match_result.deferred
                result.rejected += match_result.rejected
                result.errors += match_result.errors

            if enrich:
                # Cover/lyrics only — never rewrite title/artist/album here.
                self._enrich_organized_folder(dir_name)

            if junk_match:
                junk_paths = {
                    row.rel_path
                    for row in self._repository.list_for_dir(dir_name)
                    if self.is_junk_row(row, readonly)
                    and row.match_status != MATCH_MATCHED
                }
                for rel in junk_paths:
                    row = self._repository.get(rel)
                    if row is None:
                        continue
                    if row.match_status == MATCH_REJECTED:
                        self._repository.reset_match_state(rel)
                        row = self._repository.get(rel) or row
                    # Fill empty via QQ/MB (index-only for junk; file when mutable).
                    self._fill_empty_tags_one(
                        row, write_file=bool(playlist.allow_mutate)
                    )
                junk_result = self.match_batch(
                    health,
                    limit=playlist.max_items,
                    dir_name=dir_name,
                    ignore_backoff=False,
                    include_junk=True,
                    junk_only=False,
                    enabled_only=False,
                    only_rel_paths=junk_paths,
                )
                result.matched += junk_result.matched
                result.checked += junk_result.checked
                result.deferred += junk_result.deferred
                result.rejected += junk_result.rejected
                result.errors += junk_result.errors

            if raw_match or junk_match:
                result.recovered += self._recover_missing_organized(dir_name)
                self._collapse_present_inodes(dir_name)
        except Exception:
            status = "failed"
            result.errors += 1
            logger.exception("External playlist sync failed for %s", dir_name)
        finally:
            self._repository.record_sync(dir_name, status=status)
        return result

    def _enrich_organized_folder(self, dir_name: str) -> None:
        """Best-effort enrich of Organized/<dir> catalog tracks."""
        enrichment = self._enrichment
        if enrichment is None:
            logger.info(
                "External playlist enrich skipped (no enrichment service): %s",
                dir_name,
            )
            return
        enrich_track = getattr(enrichment, "enrich_track", None)
        if not callable(enrich_track):
            logger.warning(
                "External playlist enrich skipped (invalid enrichment): %s",
                dir_name,
            )
            return
        save_folder = organized_save_folder(dir_name)
        for loc, rec in self._catalog.list_for_save_folder(save_folder):
            if loc.membership_status == LocationMembershipStatus.OFFLINE:
                continue
            if not rec.video_id:
                continue
            try:
                path = _location_abs_path(loc)
                audio_before = self._existing_file_signature(path)
                lyrics_path = path.with_suffix(".lrc")
                lyrics_before = self._existing_file_signature(lyrics_path)
                enrich_track(rec.video_id)
                if self._existing_file_changed(
                    path, audio_before
                ) or self._existing_file_changed(lyrics_path, lyrics_before):
                    self._mark_catalog_source_mutated(rec, "media_overwritten")
            except Exception:
                logger.exception(
                    "External playlist enrich failed for %s / %s",
                    dir_name,
                    rec.video_id,
                )

    def _recover_missing_organized(self, dir_name: str) -> int:
        """Re-ingest matched raw when Organized copy is missing. Returns count.

        When re-ingest fails and the playlist allows ID-invalid marking, probe
        YouTube Music; dead IDs are marked offline (sets ``missing_since``).
        """
        playlist = self._repository.get_playlist(dir_name)
        mark_offline = bool(playlist and playlist.offline_marking_enabled)
        save_folder = organized_save_folder(dir_name)
        matched_raw = {
            row.video_id: row
            for row in self._repository.list_matched(dir_name)
            if row.video_id
        }
        recovered = 0
        marked = 0
        present_ids: set[str] = set()
        for loc, rec in self._catalog.list_for_save_folder(save_folder):
            present_ids.add(rec.video_id)
            if loc.membership_status in (
                LocationMembershipStatus.OFFLINE,
                LocationMembershipStatus.BLOCKED,
            ):
                continue
            if _location_abs_path(loc).is_file():
                continue
            raw = matched_raw.get(rec.video_id)
            if raw is not None and self.ingest_matched(raw.rel_path):
                recovered += 1
                continue
            if not mark_offline or not rec.video_id:
                continue
            if self._probe_and_mark_id_invalid(save_folder, rec.video_id):
                marked += 1
        for vid, raw in matched_raw.items():
            if vid in present_ids:
                continue
            if self.ingest_matched(raw.rel_path):
                recovered += 1
                present_ids.add(vid)
        if marked:
            logger.info(
                "Marked %d ID-invalid track(s) offline for external %s",
                marked,
                dir_name,
            )
            # delay=0: clean immediately after this sync (aligned with subscription).
            if (
                playlist is not None
                and playlist.offline_cleanup_enabled
                and int(playlist.offline_cleanup_delay_hours or 0) == 0
            ):
                self.run_id_invalid_cleanup(dir_name=dir_name)
        return recovered

    def _probe_and_mark_id_invalid(self, save_folder: str, video_id: str) -> bool:
        """Probe YTM; mark location offline when the cloud ID is dead."""
        from yubal.exceptions import TrackNotFoundError, UpstreamAPIError

        from yubal_api.services.direct_recover_service import is_unavailable_error

        try:
            self._client.get_track(video_id)
            return False
        except TrackNotFoundError as e:
            reason = str(e)
        except UpstreamAPIError as e:
            reason = str(e)
            if not is_unavailable_error(reason) and "not found" not in reason.lower():
                logger.debug(
                    "Skip offline mark for %s (transient?): %s", video_id, reason
                )
                return False
        except Exception:
            logger.debug("Skip offline mark probe for %s", video_id, exc_info=True)
            return False
        self._catalog.set_membership_status(
            save_folder,
            video_id,
            LocationMembershipStatus.OFFLINE,
        )
        logger.info(
            "Marked external track %s offline after unavailable probe (%s)",
            video_id,
            reason,
        )
        return True

    def run_id_invalid_cleanup(
        self,
        *,
        now: datetime | None = None,
        dir_name: str | None = None,
    ) -> int:
        """Dispose due ID-invalid Organized locations for external playlists.

        ``archive`` moves files to Raw/Delete; ``delete`` unlinks them.
        """
        now = now or datetime.now(UTC)
        processed = 0
        playlists = (
            [self._repository.get_playlist(dir_name)]
            if dir_name
            else self._repository.list_playlists()
        )
        for playlist in playlists:
            if playlist is None or not playlist.offline_cleanup_enabled:
                continue
            delay = max(0, int(playlist.offline_cleanup_delay_hours or 0))
            cutoff = now - timedelta(hours=delay)
            action = (playlist.offline_cleanup_action or "archive").lower()
            to_raw = action != "delete"
            processed += self._clear_offline_due(
                playlist.dir_name,
                cutoff=cutoff,
                to_raw_delete=to_raw,
            )
        return processed

    def _clear_offline_due(
        self,
        dir_name: str,
        *,
        cutoff: datetime,
        to_raw_delete: bool,
    ) -> int:
        """Clear offline locations with ``missing_since <= cutoff``."""
        save_folder = organized_save_folder(dir_name)
        stop_at = EXTERNAL_ROOT / save_folder
        cleared = 0
        for loc, rec in list(self._catalog.list_for_save_folder(save_folder)):
            if loc.membership_status != LocationMembershipStatus.OFFLINE:
                continue
            if loc.missing_since is None or loc.missing_since > cutoff:
                continue
            abs_path = _location_abs_path(loc)
            try:
                if to_raw_delete:
                    if abs_path.is_file():
                        origin_kind, origin_ref = self._playlist_origin(dir_name)
                        self.ingest_file_to_raw_delete(
                            abs_path,
                            origin_kind=origin_kind,
                            origin_ref=origin_ref,
                            title=rec.title,
                            artists=rec.artist,
                            album=rec.album or "",
                            album_artist=rec.album_artist or "",
                            year=rec.year,
                            track_number=rec.track_number,
                        )
                        cleanup_after_audio_removed(abs_path.parent, stop_at)
                else:
                    if abs_path.is_file():
                        abs_path.unlink()
                        lrc = abs_path.with_suffix(".lrc")
                        if lrc.is_file():
                            lrc.unlink(missing_ok=True)
                        cleanup_after_audio_removed(abs_path.parent, stop_at)
                self._catalog.delete_location(save_folder, loc.relative_path)
                self._catalog.liberate_tracks([rec.video_id])
                cleared += 1
            except Exception:
                logger.exception(
                    "Failed ID-invalid cleanup %s/%s", dir_name, loc.relative_path
                )
        return cleared

    def _collapse_present_inodes(self, dir_name: str) -> None:
        """Hardlink same-video copies for tracks present in this playlist."""
        from yubal_api.services.library_dedup_service import LibraryDedupService

        save_folder = organized_save_folder(dir_name)
        present_ids = {
            rec.video_id
            for _loc, rec in self._catalog.list_for_save_folder(save_folder)
            if rec.video_id
        }
        dedup = LibraryDedupService(self._catalog)
        for vid in present_ids:
            dedup.ensure_single_inode(vid)

    def delete_playlist(
        self,
        dir_name: str,
        mode: str,
        *,
        direct_folder: str,
    ) -> DeletePlaylistResult:
        playlist = self._repository.get_playlist(dir_name)
        if playlist is None:
            raise ValueError(f"playlist not found: {dir_name}")

        ledger_modes = {
            "forget_matched",
        }
        # Offline cleanup always touches files (delete or Raw/Delete) — never
        # list-only, which would leave zombie files. Allowed even when readonly.
        cleanup_modes = {
            "clear_offline_delete",
            "clear_offline_to_raw_delete",
        }
        file_modes = {
            "delete_matched",
            "move_matched_to_direct",
            "add_matched_to_direct",
            "add_meta_verified_to_wanted",
            "delete_unmatched",
            "archive_meta_rejected",
            "delete_meta_rejected",
            "delete_all",
        }
        if mode not in ledger_modes | file_modes | cleanup_modes:
            raise ValueError(f"unknown delete mode: {mode}")
        if dir_name == EXTERNAL_DELETE_DIR and mode == "clear_offline_to_raw_delete":
            raise ValueError("recycle-center items cannot be moved to recycle center")
        if mode in file_modes and not playlist.allow_mutate:
            # Meta→Wanted hardlinks the raw file; allowed on readonly playlists.
            if mode not in {
                "add_meta_verified_to_wanted",
                "archive_meta_rejected",
                "delete_meta_rejected",
            }:
                raise ValueError(
                    "read-only playlist: use forget_matched, "
                    "add_meta_verified_to_wanted, or clear_offline_* modes; "
                    "other file-touching modes require allow_mutate"
                )

        result = DeletePlaylistResult()
        liberate_ids: list[str] = []

        if mode in ("forget_matched", "delete_matched", "delete_all"):
            matched = self._delete_matched_organized(dir_name)
            result.deleted_files += matched[0]
            result.deleted_locations += matched[1]
            result.reset_matches += matched[2]
            result.errors += matched[3]
            if mode in ("delete_matched", "delete_all"):
                liberate_ids.extend(matched[4])
        if mode == "move_matched_to_direct":
            moved = self._move_matched_to_direct(dir_name, direct_folder)
            result.moved += moved[0]
            result.deleted_locations += moved[1]
            result.errors += moved[2]
            liberate_ids.extend(moved[3])
        if mode == "add_matched_to_direct":
            added = self._add_matched_to_direct(dir_name, direct_folder)
            result.moved += added[0]
            result.errors += added[1]
            # Keep hukou — hardlink into Direct, still managed by origin.
        if mode == "add_meta_verified_to_wanted":
            added = self._add_meta_verified_to_wanted(dir_name)
            result.moved += added[0]
            result.errors += added[1]
        if mode in ("delete_unmatched", "delete_all"):
            unmatched = self._delete_unmatched_raw(dir_name)
            result.deleted_raw += unmatched[0]
            result.errors += unmatched[1]
        if mode == "delete_meta_rejected":
            rejected = self._delete_meta_rejected_raw(dir_name)
            result.deleted_raw += rejected[0]
            result.skipped_readonly += rejected[1]
            result.errors += rejected[2]
        if mode == "archive_meta_rejected":
            rejected = self._archive_meta_rejected_raw(dir_name)
            result.moved += rejected[0]
            result.skipped_readonly += rejected[1]
            result.errors += rejected[2]
        if mode in ("clear_offline_delete", "clear_offline_to_raw_delete"):
            cleared = self.clear_offline(
                dir_name,
                to_raw_delete=mode == "clear_offline_to_raw_delete",
                delete_files=True,
            )
            result.deleted_files += cleared.get("deleted_files", 0)
            result.deleted_locations += cleared.get("cleared", 0)
            result.moved += cleared.get("moved", 0)
            result.errors += cleared.get("errors", 0)
            liberate_ids.extend(cleared.get("video_ids") or [])

        if liberate_ids:
            self._catalog.liberate_tracks(list(dict.fromkeys(liberate_ids)))
        return result

    def clear_offline(
        self,
        dir_name: str,
        *,
        to_raw_delete: bool = False,
        delete_files: bool = True,
    ) -> dict[str, int | list[str]]:
        """Clear offline Organized locations for one external playlist."""
        save_folder = organized_save_folder(dir_name)
        stop_at = EXTERNAL_ROOT / save_folder
        cleared = moved = deleted_files = errors = 0
        video_ids: list[str] = []
        for loc, rec in list(self._catalog.list_for_save_folder(save_folder)):
            if loc.membership_status != LocationMembershipStatus.OFFLINE:
                continue
            abs_path = _location_abs_path(loc)
            try:
                if delete_files and to_raw_delete:
                    if abs_path.is_file():
                        origin_kind, origin_ref = self._playlist_origin(dir_name)
                        dest = self.ingest_file_to_raw_delete(
                            abs_path,
                            origin_kind=origin_kind,
                            origin_ref=origin_ref,
                            title=rec.title,
                            artists=rec.artist,
                            album=rec.album or "",
                            album_artist=rec.album_artist or "",
                            year=rec.year,
                            track_number=rec.track_number,
                        )
                        if dest is not None:
                            moved += 1
                        cleanup_after_audio_removed(abs_path.parent, stop_at)
                    self._catalog.delete_location(save_folder, loc.relative_path)
                    cleared += 1
                    video_ids.append(rec.video_id)
                elif delete_files:
                    if abs_path.is_file():
                        abs_path.unlink()
                        self._mark_catalog_source_mutated(rec, "audio_deleted")
                        lrc = abs_path.with_suffix(".lrc")
                        if lrc.is_file():
                            lrc.unlink(missing_ok=True)
                        deleted_files += 1
                        cleanup_after_audio_removed(abs_path.parent, stop_at)
                    self._catalog.delete_location(save_folder, loc.relative_path)
                    cleared += 1
                    video_ids.append(rec.video_id)
                else:
                    # Ledger-only: drop location row, leave file on disk.
                    self._catalog.delete_location(save_folder, loc.relative_path)
                    cleared += 1
            except Exception:
                errors += 1
                logger.exception(
                    "Failed clearing offline %s/%s", dir_name, loc.relative_path
                )
        return {
            "cleared": cleared,
            "moved": moved,
            "deleted_files": deleted_files,
            "errors": errors,
            "video_ids": video_ids,
        }

    def _delete_matched_organized(
        self, dir_name: str
    ) -> tuple[int, int, int, int, list[str]]:
        save_folder = organized_save_folder(dir_name)
        stop_at = EXTERNAL_ROOT / save_folder
        deleted_files = deleted_locations = reset_matches = errors = 0
        video_ids: list[str] = []
        for loc, rec in list(self._catalog.list_for_save_folder(save_folder)):
            if rec.immutable:
                errors += 1
                logger.warning(
                    "Refused deletion of readonly-origin organized track %s",
                    loc.relative_path,
                )
                continue
            abs_path = _location_abs_path(loc)
            if abs_path.is_file():
                try:
                    abs_path.unlink()
                    self._mark_catalog_source_mutated(rec, "audio_deleted")
                    lrc = abs_path.with_suffix(".lrc")
                    if lrc.is_file():
                        lrc.unlink(missing_ok=True)
                    deleted_files += 1
                    cleanup_after_audio_removed(abs_path.parent, stop_at)
                except OSError:
                    errors += 1
                    logger.warning("Could not delete organized file %s", abs_path)
                    continue
            self._catalog.delete_location(save_folder, loc.relative_path)
            deleted_locations += 1
            video_ids.append(rec.video_id)
        for row in self._repository.list_matched(dir_name):
            if (
                row.video_id in video_ids
                and self._repository.reset_match_state(row.rel_path) is not None
            ):
                reset_matches += 1
        return deleted_files, deleted_locations, reset_matches, errors, video_ids

    def _collapse_video_inodes(self, video_id: str) -> None:
        """Hardlink divergent copies of one video onto a single inode when possible."""
        from yubal_api.services.library_dedup_service import LibraryDedupService

        LibraryDedupService(self._catalog).ensure_single_inode(video_id)

    def _add_matched_to_direct(
        self, dir_name: str, direct_folder: str
    ) -> tuple[int, int]:
        """Hardlink/copy matched Organized tracks into Direct; keep external intact."""
        save_folder = organized_save_folder(dir_name)
        dest_folder = sanitize_direct_folder(direct_folder)
        dest_base = DOWNLOAD_ROOT / dest_folder
        added = errors = 0
        for loc, rec in list(self._catalog.list_for_save_folder(save_folder)):
            if loc.membership_status == LocationMembershipStatus.OFFLINE:
                continue
            src = _location_abs_path(loc)
            dest = dest_base / loc.relative_path
            try:
                if src.is_file():
                    self._link_or_copy_file_preserving_sidecar(src, dest)
                elif not dest.is_file():
                    continue
                self._catalog.upsert_location(
                    video_id=rec.video_id,
                    save_folder=dest_folder,
                    relative_path=loc.relative_path,
                    origin="external_add",
                    storage_root=STORAGE_DOWNLOAD,
                )
                self._collapse_video_inodes(rec.video_id)
                added += 1
            except OSError:
                errors += 1
                logger.warning("Could not add %s to Download Center", src)
        return added, errors

    def _add_meta_verified_to_wanted(self, dir_name: str) -> tuple[int, int]:
        """Migrate unmatched meta-verified raw rows into the wishlist."""
        if self._wanted is None:
            raise RuntimeError("wanted service not configured")
        prefs = self._preferences.effective()
        if not prefs.wanted_enabled:
            raise RuntimeError("wishlist is disabled")
        add_fn = getattr(self._wanted, "add_from_external_meta", None)
        if not callable(add_fn):
            raise RuntimeError("wanted service missing add_from_external_meta")

        added = errors = 0
        from yubal_api.services.meta_verify import meta_fingerprint

        for row in self._repository.list_meta_verified_unmatched(dir_name):
            fp = meta_fingerprint(row.title, row.artists, row.album)
            if row.meta_fingerprint and row.meta_fingerprint != fp:
                self._repository.invalidate_meta(row.rel_path)
                continue
            src = EXTERNAL_RAW_ROOT / row.rel_path
            title = (row.meta_title or row.title or "").strip()
            artists = (row.meta_artists or row.artists or "").strip()
            album = (row.meta_album or row.album or "").strip()
            if not title or not artists or not album:
                errors += 1
                continue
            try:
                add_fn(
                    title=title,
                    artists=artists,
                    album=album,
                    source=row.meta_source or "manual",
                    source_id=row.meta_source_id or "",
                    source_url=row.meta_source_url,
                    thumbnail_url=row.meta_thumbnail_url,
                    source_path=src if src.is_file() else None,
                )
                added += 1
            except Exception:
                errors += 1
                logger.exception("Failed meta-verified→wanted for %s", row.rel_path)
        return added, errors

    def _wanted_source_flags(self) -> dict[str, object]:
        prefs = self._preferences.effective()
        return {
            "enable_musicbrainz": prefs.wanted_source_musicbrainz,
            "enable_qq": prefs.wanted_source_qq,
            "enable_discogs": prefs.wanted_source_discogs,
            "enable_lastfm": prefs.wanted_source_lastfm,
            "lastfm_api_key": prefs.lastfm_api_key,
        }

    def _fill_empty_tags_one(self, row: ExternalRawTrack, *, write_file: bool) -> bool:
        """Fill empty title/artist/album from QQ/MB (never YTM)."""
        from yubal_api.services.meta_verify import pick_fill_hit_for_empty_fields

        if self.tags_complete_enough(row.title, row.artists, row.album):
            return False
        prefs = self._preferences.effective()
        if not prefs.wanted_enabled:
            return False
        flags = self._wanted_source_flags()
        if not any(
            [
                flags["enable_musicbrainz"],
                flags["enable_qq"],
                flags["enable_discogs"],
                flags["enable_lastfm"],
            ]
        ):
            return False

        hit = pick_fill_hit_for_empty_fields(
            title=row.title or "",
            artists=row.artists or "",
            album=row.album or "",
            duration_ms=row.duration_ms,
            enable_musicbrainz=bool(flags["enable_musicbrainz"]),
            enable_qq=bool(flags["enable_qq"]),
            enable_discogs=bool(flags["enable_discogs"]),
            enable_lastfm=bool(flags["enable_lastfm"]),
            lastfm_api_key=str(flags["lastfm_api_key"] or ""),
        )
        if hit is None:
            return False

        title = (row.title or "").strip() or (hit.title or "").strip()
        artists = (row.artists or "").strip() or (hit.artist or "").strip()
        album = (row.album or "").strip() or (hit.album or "").strip()
        if not self.tags_complete_enough(title, artists, album):
            # Still incomplete — apply whatever we can to the index.
            pass
        if (
            title == (row.title or "").strip()
            and artists == (row.artists or "").strip()
            and album == (row.album or "").strip()
        ):
            return False

        if write_file:
            path = EXTERNAL_RAW_ROOT / row.rel_path
            if path.is_file():
                try:
                    from mediafile import MediaFile

                    audio = MediaFile(path)
                    changed = False
                    if not (str(audio.title or "").strip()) and title:
                        audio.title = title
                        changed = True
                    if not (str(audio.artist or "").strip()) and artists:
                        audio.artist = artists
                        changed = True
                    if not (str(audio.album or "").strip()) and album:
                        audio.album = album
                        changed = True
                    if changed:
                        audio.save()
                        self._mark_row_source_mutated(row, "audio_tags")
                    refreshed = _read_raw_tags(
                        path,
                        row.rel_path,
                        row.dir_name,
                        origin_kind=row.origin_kind,
                        origin_ref=row.origin_ref,
                    )
                    if refreshed is not None:
                        refreshed.match_status = row.match_status
                        refreshed.video_id = row.video_id
                        refreshed.match_confidence = row.match_confidence
                        refreshed.match_fail_count = row.match_fail_count
                        refreshed.match_next_eligible_at = row.match_next_eligible_at
                        if not (refreshed.title or "").strip():
                            refreshed.title = title
                            refreshed.title_norm = normalize_music_text(title)[:500]
                        if not (refreshed.artists or "").strip():
                            refreshed.artists = artists
                            refreshed.artist_norm = normalize_artist_key(artists)[:500]
                        if not (refreshed.album or "").strip():
                            refreshed.album = album
                            refreshed.album_norm = normalize_music_text(album)[:500]
                        self._repository.upsert(refreshed)
                        return True
                except Exception:
                    logger.exception("Failed filling empty tags on file %s", path)

        refreshed = ExternalRawTrack(
            rel_path=row.rel_path,
            dir_name=row.dir_name,
            origin_kind=row.origin_kind,
            origin_ref=row.origin_ref,
            mtime_ns=row.mtime_ns,
            size=row.size,
            inode=row.inode,
            codec=row.codec,
            sample_rate=row.sample_rate,
            bit_depth=row.bit_depth,
            channels=row.channels,
            duration_ms=row.duration_ms,
            title=title[:500],
            artists=artists[:500],
            album=album[:500],
            album_artist=(row.album_artist or artists)[:500],
            track_number=row.track_number,
            disc_number=row.disc_number,
            year=row.year,
            title_norm=normalize_music_text(title)[:500],
            artist_norm=normalize_artist_key(artists)[:500],
            album_norm=normalize_music_text(album)[:500],
            has_lyrics=row.has_lyrics,
            lyrics_embedded=row.lyrics_embedded,
            has_cover=row.has_cover,
            cover_embedded=row.cover_embedded,
            file_key=row.file_key,
        )
        self._repository.upsert(refreshed)
        return True

    def fill_empty_tags_batch(
        self,
        dir_name: str,
        *,
        limit: int = 50,
        write_file: bool = False,
    ) -> dict[str, int]:
        """Pre-YTM pass: fill empty critical tags via Wanted sources (QQ/MB)."""
        filled = skipped = checked = 0
        for row in self._repository.list_for_dir(dir_name):
            if checked >= limit:
                break
            if row.match_status == MATCH_MATCHED:
                skipped += 1
                continue
            # Empty-tag recovery is part of the same one-pass decision. Once
            # this exact file/tag state completed its YTM lane, routine syncs
            # must not query metadata providers for it again.
            if row.ytm_attempted_at is not None:
                skipped += 1
                continue
            if self.tags_complete_enough(row.title, row.artists, row.album):
                skipped += 1
                continue
            checked += 1
            if checked > 1:
                time.sleep(0.35)
            if self._fill_empty_tags_one(row, write_file=write_file):
                filled += 1
        return {"checked": checked, "filled": filled, "skipped": skipped}

    def verify_meta_batch(
        self,
        dir_name: str,
        *,
        limit: int = 50,
        write_file: bool = False,
        force: bool = False,
    ) -> dict[str, int]:
        """Verify unmatched complete-tag rows against Wanted-enabled sources.

        Runs after scan, before YTM match. Mutable playlists write verified tags
        back to the audio file; readonly only stores meta_* on the index.
        """
        from yubal_api.services.meta_search import (
            musicbrainz_cooldown_remaining_seconds,
        )
        from yubal_api.services.meta_verify import (
            meta_fingerprint,
            verify_tags_against_wanted_sources,
        )

        prefs = self._preferences.effective()
        if not prefs.wanted_enabled:
            return {"checked": 0, "verified": 0, "rejected": 0, "skipped": 0}
        if not (
            prefs.wanted_source_musicbrainz
            or prefs.wanted_source_qq
            or prefs.wanted_source_discogs
            or prefs.wanted_source_lastfm
        ):
            return {"checked": 0, "verified": 0, "rejected": 0, "skipped": 0}

        now = datetime.now(UTC)
        checked = verified = rejected = skipped = 0
        candidates = self._repository.list_meta_verifiable(
            dir_name,
            now=now,
            limit=limit,
            force=force,
        )
        for row in candidates:
            fp = meta_fingerprint(row.title, row.artists, row.album)
            if (
                row.meta_status == META_VERIFIED
                and row.meta_fingerprint
                and row.meta_fingerprint != fp
            ):
                self._repository.invalidate_meta(row.rel_path)
                row = self._repository.get(row.rel_path) or row

            checked += 1
            # MusicBrainz limits are shared globally in meta_search so Wanted
            # and External passes cannot collectively exceed the provider cap.

            mb_cooldown = musicbrainz_cooldown_remaining_seconds()
            result = verify_tags_against_wanted_sources(
                title=row.title,
                artists=row.artists,
                album=row.album,
                duration_ms=row.duration_ms,
                # Once the shared circuit opens, skip MusicBrainz for the rest
                # of this batch instead of logging the same failure per track.
                enable_musicbrainz=(
                    prefs.wanted_source_musicbrainz and mb_cooldown <= 0
                ),
                enable_qq=prefs.wanted_source_qq,
                enable_discogs=prefs.wanted_source_discogs,
                enable_lastfm=prefs.wanted_source_lastfm,
                lastfm_api_key=prefs.lastfm_api_key,
            )
            mb_cooldown = musicbrainz_cooldown_remaining_seconds()
            if result.hit is None and (
                (result.errored and not result.rejected)
                or (prefs.wanted_source_musicbrainz and mb_cooldown > 0)
            ):
                # Incomplete provider coverage stays pending. Retry shortly
                # after the real shared cooldown, rather than turning a
                # two-minute outage into a one-hour per-track delay.
                delay_seconds = max(60, mb_cooldown + 15)
                self._repository.defer_meta_retry(
                    row.rel_path,
                    next_eligible_at=now + timedelta(seconds=delay_seconds),
                )
                skipped += 1
                continue
            if result.hit is None:
                fails = int(row.meta_fail_count or 0) + 1
                delay_h = min(24 * 7, 6 * max(1, fails))
                self._repository.record_meta_rejected(
                    row.rel_path,
                    fingerprint=fp,
                    next_eligible_at=now + timedelta(hours=delay_h),
                )
                rejected += 1
                continue

            hit = result.hit
            if write_file:
                # Stamp meta fields onto the in-memory row so write-back can
                # re-record verified state after the file upsert.
                row.meta_source = hit.source
                row.meta_source_id = hit.source_id
                row.meta_source_url = hit.source_url
                row.meta_thumbnail_url = hit.thumbnail_url
                wrote = self._write_meta_tags_to_file(
                    row,
                    title=hit.title,
                    artists=hit.artist,
                    album=hit.album or row.album,
                )
                if not wrote:
                    self._repository.record_meta_verified(
                        row.rel_path,
                        source=hit.source,
                        source_id=hit.source_id,
                        source_url=hit.source_url,
                        title=hit.title,
                        artists=hit.artist,
                        album=hit.album or row.album,
                        thumbnail_url=hit.thumbnail_url,
                        fingerprint=fp,
                    )
            else:
                self._repository.record_meta_verified(
                    row.rel_path,
                    source=hit.source,
                    source_id=hit.source_id,
                    source_url=hit.source_url,
                    title=hit.title,
                    artists=hit.artist,
                    album=hit.album or row.album,
                    thumbnail_url=hit.thumbnail_url,
                    fingerprint=fp,
                )
            verified += 1

        return {
            "checked": checked,
            "verified": verified,
            "rejected": rejected,
            "skipped": skipped,
        }

    def verify_meta_enabled(
        self, *, per_playlist_limit: int | None = None
    ) -> dict[str, int]:
        """Run meta verification for every enabled external playlist."""
        totals = {"checked": 0, "verified": 0, "rejected": 0, "skipped": 0}
        for dir_name in sorted(self._repository.list_enabled_dir_names()):
            playlist = self._repository.get_playlist(dir_name)
            if playlist is None:
                continue
            limit = per_playlist_limit
            if limit is None:
                limit = max(int(playlist.max_items or 25), 20)
            try:
                part = self.verify_meta_batch(
                    dir_name,
                    limit=limit,
                    write_file=bool(playlist.allow_mutate),
                )
            except Exception:
                logger.exception("External meta verify failed for %s", dir_name)
                continue
            for key in totals:
                totals[key] += int(part.get(key, 0))
        return totals

    def _write_meta_tags_to_file(
        self,
        row: ExternalRawTrack,
        *,
        title: str,
        artists: str,
        album: str,
    ) -> bool:
        """Write verified tags conservatively: fill blanks or soft-equal polish only."""
        from yubal_api.services.meta_verify import (
            _soft_artist_equal,
            _soft_text_equal,
            meta_fingerprint,
        )

        path = EXTERNAL_RAW_ROOT / row.rel_path
        if not path.is_file():
            return False

        def _should_write(local: str, remote: str, *, kind: str) -> bool:
            loc = (local or "").strip()
            rem = (remote or "").strip()
            if not rem:
                return False
            if not loc:
                return True
            if kind == "artist":
                return _soft_artist_equal(loc, rem) and loc != rem
            return _soft_text_equal(loc, rem, album=(kind == "album")) and loc != rem

        try:
            from mediafile import MediaFile

            audio = MediaFile(path)
            changed = False
            if _should_write(str(audio.title or ""), title, kind="title"):
                audio.title = title
                changed = True
            if _should_write(str(audio.artist or ""), artists, kind="artist"):
                audio.artist = artists
                changed = True
            if _should_write(str(audio.album or ""), album, kind="album"):
                audio.album = album
                changed = True
            if changed:
                audio.save()
                self._mark_row_source_mutated(row, "audio_tags")
        except Exception:
            logger.exception("Failed writing meta tags to %s", path)
            return False
        refreshed = _read_raw_tags(
            path,
            row.rel_path,
            row.dir_name,
            origin_kind=row.origin_kind,
            origin_ref=row.origin_ref,
        )
        if refreshed is None:
            return False
        refreshed.match_status = row.match_status
        refreshed.video_id = row.video_id
        refreshed.match_confidence = row.match_confidence
        refreshed.match_fail_count = row.match_fail_count
        refreshed.match_next_eligible_at = row.match_next_eligible_at
        self._repository.upsert(refreshed)

        self._repository.record_meta_verified(
            row.rel_path,
            source=row.meta_source or "manual",
            source_id=row.meta_source_id or "",
            source_url=row.meta_source_url,
            title=title,
            artists=artists,
            album=album,
            thumbnail_url=row.meta_thumbnail_url,
            fingerprint=meta_fingerprint(
                refreshed.title, refreshed.artists, refreshed.album
            ),
        )
        return True

    def _move_matched_to_direct(
        self, dir_name: str, direct_folder: str
    ) -> tuple[int, int, int, list[str]]:
        save_folder = organized_save_folder(dir_name)
        dest_folder = sanitize_direct_folder(direct_folder)
        dest_base = DOWNLOAD_ROOT / dest_folder
        moved = deleted_locations = errors = 0
        video_ids: list[str] = []
        for loc, rec in list(self._catalog.list_for_save_folder(save_folder)):
            if rec.immutable:
                errors += 1
                logger.warning(
                    "Refused move of readonly-origin organized track %s",
                    loc.relative_path,
                )
                continue
            src = _location_abs_path(loc)
            dest = dest_base / loc.relative_path
            if not src.is_file():
                errors += 1
                logger.warning("Source missing during move to Direct: %s", src)
                continue
            try:
                self._move_file_preserving_sidecar(src, dest)
                self._mark_catalog_source_mutated(rec, "audio_moved")
                moved += 1
                cleanup_after_audio_removed(src.parent, EXTERNAL_ROOT / save_folder)
                self._track_index.set(rec.video_id, dest)
                try:
                    rel_dl = str(dest.resolve().relative_to(DOWNLOAD_ROOT.resolve()))
                    self._catalog.set_canonical(
                        rec.video_id,
                        storage=STORAGE_DOWNLOAD,
                        relative_path=rel_dl,
                    )
                except ValueError:
                    pass
            except OSError:
                errors += 1
                logger.warning("Could not move %s to %s", src, dest)
                continue
            self._catalog.upsert_location(
                video_id=rec.video_id,
                save_folder=dest_folder,
                relative_path=loc.relative_path,
                origin="external_move",
                storage_root=STORAGE_DOWNLOAD,
            )
            self._catalog.delete_location(save_folder, loc.relative_path)
            deleted_locations += 1
            video_ids.append(rec.video_id)
            # Clear Raw match so UI unmatched+matched stays consistent with scan.
            for row in self._repository.list_matched(dir_name):
                if row.video_id == rec.video_id:
                    self._repository.reset_match_state(row.rel_path)
        return moved, deleted_locations, errors, video_ids

    @staticmethod
    def _link_or_copy_file_preserving_sidecar(src: Path, dest: Path) -> None:
        """Place ``src`` at ``dest`` without removing the source (hardlink or copy)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.resolve() == src.resolve():
            return
        if dest.exists():
            dest.unlink()
        src_lrc = src.with_suffix(".lrc")
        dest_lrc = dest.with_suffix(".lrc")
        linked = False
        if same_filesystem(src.parent, dest.parent):
            try:
                os.link(src, dest)
                linked = True
            except OSError:
                linked = False
        if not linked:
            shutil.copy2(src, dest)
        if src_lrc.is_file() and not dest_lrc.exists():
            if linked:
                try:
                    os.link(src_lrc, dest_lrc)
                except OSError:
                    shutil.copy2(src_lrc, dest_lrc)
            else:
                shutil.copy2(src_lrc, dest_lrc)

    @staticmethod
    def _move_file_preserving_sidecar(src: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        src_lrc = src.with_suffix(".lrc")
        dest_lrc = dest.with_suffix(".lrc")
        try:
            nlink = src.stat().st_nlink
        except OSError as e:
            raise OSError(f"stat failed for {src}") from e
        if nlink > 1:
            shutil.copy2(src, dest)
            src.unlink()
        elif same_filesystem(src.parent, dest.parent):
            shutil.move(str(src), str(dest))
        else:
            shutil.copy2(src, dest)
            src.unlink()
        if src_lrc.is_file():
            if nlink > 1 or not same_filesystem(src.parent, dest.parent):
                if not dest_lrc.exists():
                    shutil.copy2(src_lrc, dest_lrc)
                src_lrc.unlink(missing_ok=True)
            elif not dest_lrc.exists():
                shutil.move(str(src_lrc), str(dest_lrc))

    def _delete_unmatched_raw(self, dir_name: str) -> tuple[int, int]:
        deleted_raw = errors = 0
        paths: list[str] = []
        stop_at = EXTERNAL_RAW_ROOT / dir_name
        for row in self._repository.list_for_dir(dir_name):
            if row.match_status == MATCH_MATCHED:
                continue
            if not self._raw_row_is_mutable(row):
                errors += 1
                logger.warning(
                    "Refused bulk deletion of readonly-origin raw file %s",
                    row.rel_path,
                )
                continue
            path = EXTERNAL_RAW_ROOT / row.rel_path
            if path.is_file():
                try:
                    path.unlink()
                    self._mark_row_source_mutated(row, "audio_deleted")
                    lrc = path.with_suffix(".lrc")
                    if lrc.is_file():
                        lrc.unlink(missing_ok=True)
                    deleted_raw += 1
                    cleanup_after_audio_removed(path.parent, stop_at)
                except OSError:
                    errors += 1
                    logger.warning("Could not delete raw file %s", path)
            paths.append(row.rel_path)
        if paths:
            self._repository.delete_paths(paths)
        return deleted_raw, errors

    def _delete_meta_rejected_raw(self, dir_name: str) -> tuple[int, int, int]:
        """Delete only completed tag-validation failures from mutable sources."""
        deleted = skipped_readonly = errors = 0
        paths: list[str] = []
        stop_at = EXTERNAL_RAW_ROOT / dir_name
        for row in self._repository.list_meta_rejected_for_dir(dir_name):
            if not self._raw_row_is_mutable(row):
                skipped_readonly += 1
                continue
            path = EXTERNAL_RAW_ROOT / row.rel_path
            try:
                if path.is_file():
                    path.unlink()
                    self._mark_row_source_mutated(row, "audio_deleted")
                    path.with_suffix(".lrc").unlink(missing_ok=True)
                    cleanup_after_audio_removed(path.parent, stop_at)
                    deleted += 1
                paths.append(row.rel_path)
            except OSError:
                errors += 1
                logger.warning("Could not delete meta-rejected raw file %s", path)
        if paths:
            self._repository.delete_paths(paths)
        return deleted, skipped_readonly, errors

    def _archive_meta_rejected_raw(self, dir_name: str) -> tuple[int, int, int]:
        """Move tag-validation failures to the archive without losing provenance."""
        if dir_name == EXTERNAL_DEFAULT_DIR:
            raise ValueError("archive items are already in archived tracks")
        moved = skipped_readonly = errors = 0
        stop_at = EXTERNAL_RAW_ROOT / dir_name
        for row in self._repository.list_meta_rejected_for_dir(dir_name):
            if not self._raw_row_is_mutable(row):
                skipped_readonly += 1
                continue
            path = EXTERNAL_RAW_ROOT / row.rel_path
            try:
                if not path.is_file():
                    self._repository.delete_paths([row.rel_path])
                    continue
                dest = self.ingest_file_to_raw_default(
                    path,
                    origin_kind=row.origin_kind,
                    origin_ref=row.origin_ref,
                    title=row.title,
                    artists=row.artists,
                    album=row.album,
                    album_artist=row.album_artist,
                    year=row.year,
                    track_number=row.track_number,
                )
                if dest is not None:
                    self._repository.delete_paths([row.rel_path])
                    cleanup_after_audio_removed(path.parent, stop_at)
                    moved += 1
            except OSError:
                errors += 1
                logger.warning("Could not archive meta-rejected raw file %s", path)
        return moved, skipped_readonly, errors

    def reset_match(self, rel_path: str) -> ExternalRawTrack | None:
        """Manual reset so a track is retried on the next match batch."""
        return self._repository.reset_match_state(rel_path)

    def get_raw_track(self, rel_path: str) -> ExternalRawTrack | None:
        """Look up one indexed raw track row by its Raw/-relative path."""
        return self._repository.get(rel_path)

    # -- Ingest --

    def ingest_matched(self, rel_path: str) -> bool:
        """Place a matched raw file into Organized/<dir_name> and update the catalog."""
        row = self._repository.get(rel_path)
        if row is None or not row.video_id or row.match_status != MATCH_MATCHED:
            return False

        source = EXTERNAL_RAW_ROOT / row.rel_path
        if not source.is_file():
            return False

        playlist = self._repository.get_playlist(
            row.dir_name
        ) or self._repository.upsert_playlist(row.dir_name)
        save_folder = organized_save_folder(row.dir_name)
        organized_base = EXTERNAL_ROOT / save_folder

        existing_record = self._catalog.get_track(row.video_id)
        if existing_record is not None and existing_record.canonical_rel:
            if self._ingest_dedupe(
                row,
                existing_record.video_id,
                save_folder,
                organized_base,
                playlist,
            ):
                return True
            # Stale canonical (file moved/deleted) — fall through and place from Raw.
        return self._ingest_new(row, playlist, save_folder, organized_base, source)

    def _dest_for_row(
        self, row: ExternalRawTrack, organized_base: Path, suffix: str
    ) -> Path:
        primary_artist = row.album_artist or row.artists or "Unknown Artist"
        stem = build_track_path(
            base=organized_base,
            artist=primary_artist,
            year=row.year,
            album=row.album or "Unknown Album",
            track_number=row.track_number,
            title=row.title or Path(row.rel_path).stem,
            video_id=None,
        )
        dest = Path(f"{stem}{suffix}")
        if dest.exists():
            stem = build_track_path(
                base=organized_base,
                artist=primary_artist,
                year=row.year,
                album=row.album or "Unknown Album",
                track_number=row.track_number,
                title=row.title or Path(row.rel_path).stem,
                video_id=row.video_id,
            )
            dest = Path(f"{stem}{suffix}")
        return dest

    def _ingest_dedupe(
        self,
        row: ExternalRawTrack,
        video_id: str,
        save_folder: str,
        organized_base: Path,
        playlist: ExternalPlaylist,
    ) -> bool:
        """Second+ sighting of a video_id: quality-aware hardlink/move, never copy Raw.

        Readonly Raw egress is hardlink-only. Writable may move Raw into Organized.

        - Raw strictly better → Raw (or moved Organized) becomes master; other
          locations hardlink onto it. Readonly ⇒ immutable (protect Raw inode).
        - Raw worse/equal → leave Raw alone; Organized hardlinks to existing
          master. Do not stamp readonly hukou onto an unrelated library master.
        """
        source = EXTERNAL_RAW_ROOT / row.rel_path
        if not source.is_file():
            return False

        canonical = self._catalog.resolve_canonical_path(video_id)
        existing = canonical if canonical is not None and canonical.is_file() else None

        try:
            if existing is not None and source.stat().st_ino == existing.stat().st_ino:
                if source.stat().st_dev == existing.stat().st_dev:
                    # Already one inode — only ensure Organized door.
                    return self._place_organized_link(
                        video_id,
                        row,
                        playlist,
                        save_folder,
                        organized_base,
                        link_from=existing,
                        promote_raw=False,
                    )
        except OSError:
            pass

        if existing is None:
            raw_better = True
        else:
            raw_better = _is_strictly_better(
                _quality_key(row), _path_quality_key(existing)
            )

        if raw_better:
            return self._ingest_dedupe_raw_wins(
                row,
                video_id,
                save_folder,
                organized_base,
                playlist,
                source,
            )
        assert existing is not None
        return self._place_organized_link(
            video_id,
            row,
            playlist,
            save_folder,
            organized_base,
            link_from=existing,
            promote_raw=False,
        )

    def _ingest_dedupe_raw_wins(
        self,
        row: ExternalRawTrack,
        video_id: str,
        save_folder: str,
        organized_base: Path,
        playlist: ExternalPlaylist,
        source: Path,
    ) -> bool:
        """Promote better Raw to master via move (writable) or hardlink (readonly)."""
        dest = self._dest_for_row(row, organized_base, source.suffix.lower())
        dest.parent.mkdir(parents=True, exist_ok=True)

        if playlist.allow_mutate:
            # Move Raw → Organized, then hardlink every other location onto it.
            if dest.resolve() != source.resolve():
                if dest.exists():
                    dest.unlink()
                self._move_file_preserving_sidecar(source, dest)
                self._mark_source_mutated(
                    playlist_uid=playlist.playlist_uid,
                    mutation_kind="audio_moved",
                )
            master = dest
            try:
                rel_to_external = master.resolve().relative_to(EXTERNAL_ROOT.resolve())
                self._catalog.set_canonical(
                    video_id,
                    storage=STORAGE_EXTERNAL,
                    relative_path=str(rel_to_external),
                )
            except ValueError:
                detected = detect_storage_for_path(master)
                if detected is not None:
                    self._catalog.set_canonical(
                        video_id, storage=detected[0], relative_path=detected[1]
                    )
            self._relink_all_locations_to(video_id, master)
            relative_path = str(master.resolve().relative_to(organized_base.resolve()))
            self._catalog.upsert_location(
                video_id=video_id,
                save_folder=save_folder,
                relative_path=relative_path,
                origin="external_move",
                storage_root=STORAGE_EXTERNAL,
            )
            self._catalog.stamp_origin_hukou(
                video_id,
                playlist_uid=playlist.playlist_uid,
                immutable=False,
            )
            self._track_index.set(video_id, master)
            return True

        # Readonly: hardlink only — Raw stays, becomes canonical master.
        if not self._hardlink_only(source, dest):
            logger.warning(
                "Readonly Raw hardlink failed (%s -> %s); refusing copy",
                source,
                dest,
            )
            return False
        self._hardlink_sidecar(source, dest)
        try:
            rel_to_external = source.resolve().relative_to(EXTERNAL_ROOT.resolve())
            self._catalog.set_canonical(
                video_id,
                storage=STORAGE_EXTERNAL,
                relative_path=str(rel_to_external),
            )
        except ValueError:
            logger.warning("Could not set Raw canonical for %s", source)
            return False
        self._relink_all_locations_to(video_id, source)
        relative_path = str(dest.resolve().relative_to(organized_base.resolve()))
        self._catalog.upsert_location(
            video_id=video_id,
            save_folder=save_folder,
            relative_path=relative_path,
            origin="external_link",
            storage_root=STORAGE_EXTERNAL,
        )
        # Protect Raw inode: any shared path must refuse tag writes.
        self._catalog.stamp_origin_hukou(
            video_id,
            playlist_uid=playlist.playlist_uid,
            immutable=True,
        )
        self._catalog.set_immutable(video_id, True)
        self._track_index.set(video_id, source)
        return True

    def _place_organized_link(
        self,
        video_id: str,
        row: ExternalRawTrack,
        playlist: ExternalPlaylist,
        save_folder: str,
        organized_base: Path,
        *,
        link_from: Path,
        promote_raw: bool,
    ) -> bool:
        """Link Organized onto ``link_from`` without changing Raw."""
        _ = promote_raw
        dest = self._dest_for_row(row, organized_base, link_from.suffix.lower())
        if dest.resolve() != link_from.resolve():
            if not self._hardlink_only(link_from, dest):
                logger.warning("Organized hardlink failed (%s -> %s)", link_from, dest)
                return False
            self._hardlink_sidecar(link_from, dest)
        relative_path = str(dest.resolve().relative_to(organized_base.resolve()))
        self._catalog.upsert_location(
            video_id=video_id,
            save_folder=save_folder,
            relative_path=relative_path,
            origin="external_dedupe",
            storage_root=STORAGE_EXTERNAL,
        )
        # Existing library master stays; readonly must not stamp hukou onto it.
        if playlist.allow_mutate:
            self._catalog.stamp_origin_hukou(
                video_id,
                playlist_uid=playlist.playlist_uid,
                immutable=False,
            )
        self._track_index.set(video_id, dest if dest.is_file() else link_from)
        return True

    def _relink_all_locations_to(self, video_id: str, winner: Path) -> None:
        """Hardlink every catalog location for ``video_id`` onto ``winner``."""
        from yubal_api.services.library_dedup_service import LibraryDedupService

        try:
            winner_res = winner.resolve()
        except OSError:
            return
        for loc in self._catalog.list_locations_for_video(video_id):
            path = _location_abs_path(loc)
            if not path.is_file():
                continue
            try:
                if path.resolve() == winner_res:
                    continue
            except OSError:
                continue
            LibraryDedupService._relink(winner, path)
        detected = detect_storage_for_path(winner)
        if detected is not None:
            self._catalog.set_canonical(
                video_id, storage=detected[0], relative_path=detected[1]
            )

    @staticmethod
    def _hardlink_only(src: Path, dest: Path) -> bool:
        """Place ``dest`` as a hardlink to ``src``. No copy fallback (Raw iron rule)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            if dest.exists() and dest.resolve() == src.resolve():
                return True
        except OSError:
            pass
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                return False
        try:
            os.link(src, dest)
            return True
        except OSError as e:
            logger.warning("hardlink failed %s -> %s: %s", src, dest, e)
            return False

    @staticmethod
    def _hardlink_sidecar(src: Path, dest: Path) -> None:
        src_lrc = src.with_suffix(".lrc")
        dest_lrc = dest.with_suffix(".lrc")
        if not src_lrc.is_file() or dest_lrc.exists():
            return
        try:
            os.link(src_lrc, dest_lrc)
        except OSError:
            logger.debug("Could not hardlink lyrics sidecar for %s", dest)

    def _ingest_new(
        self,
        row: ExternalRawTrack,
        playlist: ExternalPlaylist,
        save_folder: str,
        organized_base: Path,
        source: Path,
    ) -> bool:
        dest = self._dest_for_row(row, organized_base, source.suffix.lower())
        dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.resolve() != source.resolve():
            if playlist.allow_mutate:
                if dest.exists():
                    dest.unlink()
                os.rename(source, dest)
                self._mark_source_mutated(
                    playlist_uid=playlist.playlist_uid,
                    mutation_kind="audio_moved",
                )
            else:
                if not self._hardlink_only(source, dest):
                    logger.warning(
                        "Readonly ingest hardlink failed (%s -> %s); refusing copy",
                        source,
                        dest,
                    )
                    return False

            src_lrc = source.with_suffix(".lrc")
            dest_lrc = dest.with_suffix(".lrc")
            if src_lrc.is_file() and not dest_lrc.exists():
                try:
                    if playlist.allow_mutate:
                        os.rename(src_lrc, dest_lrc)
                    else:
                        os.link(src_lrc, dest_lrc)
                except OSError:
                    if playlist.allow_mutate:
                        try:
                            shutil.copy2(src_lrc, dest_lrc)
                        except OSError:
                            logger.debug("Could not place lyrics sidecar for %s", dest)
                    else:
                        logger.debug("Could not hardlink lyrics sidecar for %s", dest)

        rel_to_external = dest.resolve().relative_to(EXTERNAL_ROOT.resolve())
        relative_path = str(dest.resolve().relative_to(organized_base.resolve()))

        assert row.video_id is not None
        self._catalog.upsert_track(
            video_id=row.video_id,
            title=row.title or Path(row.rel_path).stem,
            artist=row.artists or "Unknown Artist",
            album_artist=row.album_artist or row.artists or "Unknown Artist",
            album=row.album or "",
            track_number=row.track_number,
            year=row.year,
            has_embedded_cover=row.has_cover,
            has_lyrics_embedded=row.lyrics_embedded,
            has_lyrics_sidecar=row.has_lyrics,
            authoritative_assets=True,
        )
        self._catalog.upsert_location(
            video_id=row.video_id,
            save_folder=save_folder,
            relative_path=relative_path,
            origin="external_move" if playlist.allow_mutate else "external_link",
            storage_root=STORAGE_EXTERNAL,
        )
        self._catalog.set_canonical(
            row.video_id,
            storage=STORAGE_EXTERNAL,
            relative_path=str(rel_to_external),
        )
        self._catalog.stamp_origin_hukou(
            row.video_id,
            playlist_uid=playlist.playlist_uid,
            immutable=not playlist.allow_mutate,
        )
        self._track_index.set(row.video_id, dest)
        return True

    # -- Listing --

    def list_playlist_tracks(
        self, dir_name: str, *, show_raw: bool | None = None
    ) -> list[PlaylistTrackView]:
        playlist = self._repository.get_playlist(dir_name)
        default_show_raw = playlist.show_raw if playlist else True
        effective_show_raw = show_raw if show_raw is not None else default_show_raw
        effective_show_junk = bool(
            effective_show_raw and playlist is not None and playlist.show_junk
        )

        save_folder = organized_save_folder(dir_name)
        out: list[PlaylistTrackView] = []
        direct_ids = {
            loc.video_id
            for loc, _rec in self._catalog.list_for_save_folder(DIRECT_FOLDER)
            if loc.video_id
        }
        for loc, rec in self._catalog.list_for_save_folder(save_folder):
            tags_ok = self.tags_complete_enough(rec.title, rec.artist, rec.album)
            out.append(
                PlaylistTrackView(
                    # Stream path under /External (library file endpoint).
                    rel_path=f"{save_folder}/{loc.relative_path}",
                    dir_name=dir_name,
                    title=rec.title,
                    artist=rec.artist,
                    album=rec.album or "",
                    video_id=rec.video_id,
                    match_status=MATCH_MATCHED,
                    is_raw=False,
                    tags_complete=tags_ok,
                    is_junk=False,
                    junk_kind=None,
                    cover_url=rec.cover_url,
                    cover_source=rec.cover_source,
                    has_embedded_cover=rec.has_embedded_cover,
                    album_artist=rec.album_artist,
                    year=rec.year,
                    track_number=rec.track_number,
                    organized_relative_path=loc.relative_path,
                    in_direct=rec.video_id in direct_ids,
                    can_mutate=not rec.immutable,
                )
            )

        if effective_show_raw:
            for row in self._repository.list_for_dir(dir_name):
                if row.match_status == MATCH_MATCHED:
                    continue
                tags_ok = self.tags_complete_enough(row.title, row.artists, row.album)
                can_mutate = self._raw_row_is_mutable(row)
                junk_kind = self.junk_kind_for_row(row, not can_mutate)
                if junk_kind is not None and not effective_show_junk:
                    continue
                out.append(
                    PlaylistTrackView(
                        # Stream path: Raw/<dir>/… under /External.
                        rel_path=f"{EXTERNAL_RAW_DIR}/{row.rel_path}",
                        dir_name=dir_name,
                        title=row.title or Path(row.rel_path).stem,
                        artist=row.artists or "",
                        album=row.album or "",
                        video_id=row.video_id,
                        match_status=row.match_status,
                        is_raw=True,
                        tags_complete=tags_ok,
                        is_junk=junk_kind is not None,
                        junk_kind=junk_kind,
                        album_artist=row.album_artist,
                        year=row.year,
                        track_number=row.track_number,
                        meta_status=row.meta_status or META_PENDING,
                        meta_source=row.meta_source,
                        meta_source_id=row.meta_source_id,
                        meta_source_url=row.meta_source_url,
                        can_mutate=can_mutate,
                    )
                )

        return out

    @staticmethod
    def _playlist_track_page_key(
        track: PlaylistTrackView,
        sort_key: str,
    ) -> tuple[int, str, str]:
        """Mirror the UI's stable quality-bucket ordering for paged rows."""
        if not track.is_raw:
            bucket = 0
        elif track.meta_status == META_VERIFIED:
            bucket = 1
        elif track.junk_kind == "rw":
            bucket = 3
        elif track.junk_kind == "ro":
            bucket = 4
        else:
            bucket = 2
        value = (
            track.artist
            if sort_key == "artist"
            else track.album
            if sort_key == "album"
            else track.title
        )
        return bucket, (value or "").casefold(), track.rel_path.casefold()

    def list_playlist_tracks_page(
        self,
        dir_name: str,
        *,
        offset: int,
        limit: int,
        sort_key: str = "title",
        show_raw: bool | None = None,
    ) -> tuple[int, list[PlaylistTrackView]]:
        """Return a stable cached slice instead of serializing the whole list."""
        normalized_sort = (
            sort_key if sort_key in {"title", "artist", "album"} else "title"
        )
        cache_key = (dir_name, show_raw, normalized_sort)
        now = time.monotonic()
        cached = self._track_page_cache.get(cache_key)
        if cached is None or cached[0] <= now:
            for key in [
                key
                for key, value in self._track_page_cache.items()
                if value[0] <= now
            ]:
                self._track_page_cache.pop(key, None)
            while len(self._track_page_cache) >= 4:
                self._track_page_cache.pop(next(iter(self._track_page_cache)))
            rows = self.list_playlist_tracks(dir_name, show_raw=show_raw)
            rows.sort(
                key=lambda track: self._playlist_track_page_key(
                    track,
                    normalized_sort,
                )
            )
            # Long enough for a user to browse many pages; all mutations use a
            # fresh service request after this bounded snapshot expires.
            cached = (now + 300.0, rows)
            self._track_page_cache[cache_key] = cached
        rows = cached[1]
        start = max(0, int(offset))
        size = max(1, min(200, int(limit)))
        return len(rows), rows[start : start + size]

    def invalidate_track_page_cache(self, dir_name: str | None = None) -> None:
        """Drop paged UI snapshots after a known playlist mutation."""
        if dir_name is None:
            self._track_page_cache.clear()
            return
        for key in [key for key in self._track_page_cache if key[0] == dir_name]:
            self._track_page_cache.pop(key, None)

    def delete_track(
        self,
        dir_name: str,
        *,
        rel_path: str,
        mode: str,
    ) -> dict[str, int | bool]:
        """Delete one external track.

        ``rel_path`` is the list/stream path (``Organized/…`` or ``Raw/…``).

        Modes:
        - matched ``keep_match``: remove Organized file; keep catalog location
          (recovery / match record).
        - matched ``clear_match``: remove file + catalog location + reset raw match.
        - unmatched ``delete_raw``: delete Raw file + index row (mutable only).
        """
        playlist = self._repository.get_playlist(dir_name)
        if playlist is None:
            raise ValueError(f"playlist not found: {dir_name}")

        normalized = rel_path.strip().replace("\\", "/").lstrip("/")
        if normalized.startswith("External/"):
            normalized = normalized[len("External/") :]

        if normalized.startswith(f"{EXTERNAL_RAW_DIR}/"):
            raw_rel = normalized[len(EXTERNAL_RAW_DIR) + 1 :]
            if mode != "delete_raw":
                raise ValueError("unmatched tracks only support delete_raw")
            deleted, errors = self._delete_raw_paths(dir_name, [raw_rel])
            return {
                "deleted_files": deleted,
                "deleted_locations": 0,
                "reset_matches": 0,
                "errors": errors,
                "ok": errors == 0 and deleted > 0,
            }

        save_folder = organized_save_folder(dir_name)
        prefix = f"{save_folder}/"
        if not normalized.startswith(prefix):
            raise ValueError(f"path not in playlist: {rel_path}")
        organized_rel = normalized[len(prefix) :]

        if mode not in ("keep_match", "clear_match"):
            raise ValueError(f"unsupported delete mode for matched track: {mode}")

        deleted_files = deleted_locations = reset_matches = errors = 0
        loc_rec = None
        for loc, rec in self._catalog.list_for_save_folder(save_folder):
            if loc.relative_path == organized_rel:
                loc_rec = (loc, rec)
                break
        if loc_rec is None:
            raise ValueError(f"matched track not found: {rel_path}")
        loc, rec = loc_rec
        if rec.immutable:
            raise PermissionError("readonly-origin track cannot be removed")
        abs_path = _location_abs_path(loc)
        if abs_path.is_file():
            try:
                abs_path.unlink()
                self._mark_catalog_source_mutated(rec, "audio_deleted")
                lrc = abs_path.with_suffix(".lrc")
                if lrc.is_file():
                    lrc.unlink(missing_ok=True)
                deleted_files = 1
                cleanup_after_audio_removed(
                    abs_path.parent, EXTERNAL_ROOT / save_folder
                )
            except OSError:
                errors = 1
                logger.warning("Could not delete organized file %s", abs_path)
        # Do not detach catalog state if the physical deletion failed; it must
        # stay visible and retryable.
        if mode == "clear_match" and errors == 0:
            self._catalog.delete_location(save_folder, loc.relative_path)
            deleted_locations = 1
            for row in self._repository.list_matched(dir_name):
                if row.video_id == rec.video_id:
                    if self._repository.reset_match_state(row.rel_path) is not None:
                        reset_matches += 1
        return {
            "deleted_files": deleted_files,
            "deleted_locations": deleted_locations,
            "reset_matches": reset_matches,
            "errors": errors,
            "ok": errors == 0,
        }

    def _delete_raw_paths(self, dir_name: str, raw_rels: list[str]) -> tuple[int, int]:
        deleted = errors = 0
        paths: list[str] = []
        stop_at = EXTERNAL_RAW_ROOT / dir_name
        for raw_rel in raw_rels:
            row = self._repository.get(raw_rel)
            if row is None or row.dir_name != dir_name:
                errors += 1
                continue
            if not self._raw_row_is_mutable(row):
                errors += 1
                logger.warning(
                    "Refused deletion of readonly-origin raw file %s", raw_rel
                )
                continue
            path = EXTERNAL_RAW_ROOT / row.rel_path
            deleted_or_missing = not path.is_file()
            if path.is_file():
                try:
                    path.unlink()
                    self._mark_row_source_mutated(row, "audio_deleted")
                    lrc = path.with_suffix(".lrc")
                    if lrc.is_file():
                        lrc.unlink(missing_ok=True)
                    deleted += 1
                    deleted_or_missing = True
                    cleanup_after_audio_removed(path.parent, stop_at)
                except OSError:
                    errors += 1
                    logger.warning("Could not delete raw file %s", path)
            if deleted_or_missing:
                paths.append(row.rel_path)
        if paths:
            self._repository.delete_paths(paths)
        return deleted, errors

    def match_one_manual(
        self, rel_path: str, *, mode: str | None = None
    ) -> MatchOneManualResult:
        """Manual match for one raw track (``Raw/…`` or Raw-relative path).

        Incomplete tags: fill empty fields via QQ/MB (never YTM), then YTM match.
        On auto-miss, returns up to 5 YTM candidates and up to 5 meta candidates.

        Returns ``MatchOneManualResult``.
        """
        raw_rel = rel_path.strip().replace("\\", "/").lstrip("/")
        if raw_rel.startswith("External/"):
            raw_rel = raw_rel[len("External/") :]
        if raw_rel.startswith(f"{EXTERNAL_RAW_DIR}/"):
            raw_rel = raw_rel[len(EXTERNAL_RAW_DIR) + 1 :]

        match_mode = (
            (mode or self._preferences.effective().match_strictness or "strict")
            .lower()
            .strip()
        )
        if match_mode not in {"strict", "relaxed"}:
            match_mode = "strict"

        self.reset_match(raw_rel)
        row = self.get_raw_track(raw_rel)
        if row is None:
            raise ValueError(f"raw track not found: {rel_path}")

        playlist = self._repository.get_playlist(row.dir_name)
        mutable = bool(playlist and playlist.allow_mutate)
        complete = self.tags_complete_enough(row.title, row.artists, row.album)

        if not complete:
            self._fill_empty_tags_one(row, write_file=mutable)
            row = self.get_raw_track(raw_rel) or row

        matched, candidates = self._match_one_with_candidates(
            row, strict_tags=False, mode=match_mode, rate_limit=False
        )
        if matched:
            refreshed = self.get_raw_track(raw_rel)
            video_id = refreshed.video_id if refreshed else row.video_id
            ingested = self.ingest_matched(raw_rel)
            if ingested and video_id:
                self._post_match_enrich(video_id, rewrite_metadata=False)
            return MatchOneManualResult(
                matched=True,
                video_id=video_id,
                ingested=ingested,
                mode_used=match_mode,
                ytm_candidates=[],
                meta_candidates=[],
            )

        meta_candidates = self._list_meta_candidates_for_row(row)
        return MatchOneManualResult(
            matched=False,
            video_id=None,
            ingested=False,
            mode_used=match_mode,
            ytm_candidates=candidates,
            meta_candidates=meta_candidates,
        )

    def _list_meta_candidates_for_row(
        self, row: ExternalRawTrack
    ) -> list[MetaCandidate]:
        from yubal_api.services.meta_verify import list_meta_candidates

        prefs = self._preferences.effective()
        if not prefs.wanted_enabled:
            return []
        flags = self._wanted_source_flags()
        if not any(
            [
                flags["enable_musicbrainz"],
                flags["enable_qq"],
                flags["enable_discogs"],
                flags["enable_lastfm"],
            ]
        ):
            return []
        title = (row.title or Path(row.rel_path).stem).strip()
        artists = (row.artists or "").strip()
        album = (row.album or "").strip()
        hits = list_meta_candidates(
            title=title,
            artists=artists,
            album=album,
            duration_ms=row.duration_ms,
            enable_musicbrainz=bool(flags["enable_musicbrainz"]),
            enable_qq=bool(flags["enable_qq"]),
            enable_discogs=bool(flags["enable_discogs"]),
            enable_lastfm=bool(flags["enable_lastfm"]),
            lastfm_api_key=str(flags["lastfm_api_key"] or ""),
            per_source_limit=_META_CANDIDATE_LIMIT,
            limit=_META_CANDIDATE_LIMIT,
            require_soft_match=True,
        )
        return [
            MetaCandidate(
                source=h.source,
                source_id=h.source_id,
                title=h.title,
                artists=h.artist,
                album=h.album or "",
                source_url=h.source_url,
                thumbnail_url=h.thumbnail_url,
                duration_seconds=h.duration_seconds,
                score=float(h.score or 0.0),
            )
            for h in hits
        ]

    def accept_match(
        self, rel_path: str, video_id: str, *, confidence: float = 0.0
    ) -> tuple[bool, str | None, bool]:
        """Bind a manually chosen YTM ``video_id`` and ingest.

        Returns ``(matched, video_id, ingested)``.
        """
        raw_rel = rel_path.strip().replace("\\", "/").lstrip("/")
        if raw_rel.startswith("External/"):
            raw_rel = raw_rel[len("External/") :]
        if raw_rel.startswith(f"{EXTERNAL_RAW_DIR}/"):
            raw_rel = raw_rel[len(EXTERNAL_RAW_DIR) + 1 :]

        video_id = (video_id or "").strip()
        if not video_id:
            raise ValueError("video_id is required")

        row = self.get_raw_track(raw_rel)
        if row is None:
            raise ValueError(f"raw track not found: {rel_path}")

        self._repository.record_match_success(
            raw_rel,
            video_id=video_id,
            confidence=float(confidence or 0.0),
        )
        ingested = self.ingest_matched(raw_rel)
        if ingested:
            self._post_match_enrich(video_id, rewrite_metadata=False)
        return True, video_id, ingested

    def accept_meta_candidate(
        self,
        rel_path: str,
        *,
        source: str,
        source_id: str,
        title: str,
        artists: str,
        album: str = "",
        source_url: str | None = None,
        thumbnail_url: str | None = None,
        write_file: bool | None = None,
    ) -> bool:
        """Mark a raw track meta-verified from a manually chosen Wanted-source hit.

        Does not bind a YTM ``video_id``. Optional conservative tag write-back
        when the playlist is mutable.
        """
        from yubal_api.services.meta_verify import meta_fingerprint

        raw_rel = rel_path.strip().replace("\\", "/").lstrip("/")
        if raw_rel.startswith("External/"):
            raw_rel = raw_rel[len("External/") :]
        if raw_rel.startswith(f"{EXTERNAL_RAW_DIR}/"):
            raw_rel = raw_rel[len(EXTERNAL_RAW_DIR) + 1 :]

        row = self.get_raw_track(raw_rel)
        if row is None:
            raise ValueError(f"raw track not found: {rel_path}")
        if row.match_status == MATCH_MATCHED:
            raise ValueError("track already YTM-matched")

        playlist = self._repository.get_playlist(row.dir_name)
        mutable = bool(playlist and playlist.allow_mutate)
        do_write = mutable if write_file is None else bool(write_file) and mutable

        title = (title or "").strip()
        artists = (artists or "").strip()
        album = (album or row.album or "").strip()
        if not title or not artists:
            raise ValueError("title and artists are required")

        fp = meta_fingerprint(row.title, row.artists, row.album)
        if do_write:
            row.meta_source = source
            row.meta_source_id = source_id
            row.meta_source_url = source_url
            row.meta_thumbnail_url = thumbnail_url
            wrote = self._write_meta_tags_to_file(
                row, title=title, artists=artists, album=album
            )
            if not wrote:
                self._repository.record_meta_verified(
                    row.rel_path,
                    source=source,
                    source_id=source_id,
                    source_url=source_url,
                    title=title,
                    artists=artists,
                    album=album,
                    thumbnail_url=thumbnail_url,
                    fingerprint=fp,
                )
        else:
            self._repository.record_meta_verified(
                row.rel_path,
                source=source,
                source_id=source_id,
                source_url=source_url,
                title=title,
                artists=artists,
                album=album,
                thumbnail_url=thumbnail_url,
                fingerprint=fp,
            )
        return True

    def _scrape_raw_tags_once(
        self, row: ExternalRawTrack, *, write_file: bool = True
    ) -> bool:
        """Best-effort YTM search → fill missing critical tags only.

        Never replaces a non-empty local field with YTM text. YTM often returns
        romanized/pinyin titles for CJK catalog entries; overwriting would destroy
        good local tags (e.g. 此生不换 → Ci Sheng Bu Huan).

        When ``write_file`` is True, write filled tags onto the Raw audio file then
        refresh the index from disk. When False, upsert into the index only.
        """
        local_title = (row.title or "").strip()
        local_artists = (row.artists or "").strip()
        local_album = (row.album or "").strip()
        if self.tags_complete_enough(local_title, local_artists, local_album):
            return False

        query = " ".join(
            part
            for part in (
                local_artists,
                local_title or Path(row.rel_path).stem.strip(),
                local_album,
            )
            if part
        ).strip()
        if not query:
            return False
        try:
            results = self._client.search_songs(query)[:5]
        except Exception:
            logger.exception("Raw tag scrape search failed for %s", row.rel_path)
            return False
        if not results:
            return False
        best = results[0]
        remote_title = (best.title or "").strip()
        remote_artists = " / ".join(a.name for a in best.artists if a.name).strip()
        remote_album = (best.album.name if best.album else "") or ""
        if not self.tags_complete_enough(
            remote_title or local_title,
            remote_artists or local_artists,
            remote_album or local_album,
        ):
            return False

        def _pick(local: str, remote: str) -> str:
            if local:
                # Keep CJK local over ASCII-only YTM romanization.
                if has_cjk(local) and remote and not has_cjk(remote):
                    return local
                return local
            return remote

        title = _pick(local_title, remote_title)
        artists = _pick(local_artists, remote_artists)
        album = _pick(local_album, remote_album)
        if not self.tags_complete_enough(title, artists, album):
            return False
        if title == local_title and artists == local_artists and album == local_album:
            return False

        if not write_file:
            refreshed = ExternalRawTrack(
                rel_path=row.rel_path,
                dir_name=row.dir_name,
                origin_kind=row.origin_kind,
                origin_ref=row.origin_ref,
                mtime_ns=row.mtime_ns,
                size=row.size,
                inode=row.inode,
                codec=row.codec,
                sample_rate=row.sample_rate,
                bit_depth=row.bit_depth,
                channels=row.channels,
                duration_ms=row.duration_ms,
                title=title,
                artists=artists,
                album=album,
                album_artist=(
                    artists
                    if best.album and not (row.album_artist or "").strip()
                    else (row.album_artist or artists)
                ),
                track_number=row.track_number,
                disc_number=row.disc_number,
                year=row.year,
                title_norm=normalize_music_text(title)[:500],
                artist_norm=normalize_artist_key(artists)[:500],
                album_norm=normalize_music_text(album)[:500],
                has_lyrics=row.has_lyrics,
                lyrics_embedded=row.lyrics_embedded,
                has_cover=row.has_cover,
                cover_embedded=row.cover_embedded,
                file_key=row.file_key,
            )
            # Preserve match bookkeeping (upsert only copies tag/file fields).
            self._repository.upsert(refreshed)
            return True

        path = EXTERNAL_RAW_ROOT / row.rel_path
        if not path.is_file():
            return False
        try:
            from mediafile import MediaFile

            audio = MediaFile(path)
            if not (str(audio.title or "").strip()):
                audio.title = title
            if not (str(audio.artist or "").strip()):
                audio.artist = artists
            if not (str(audio.album or "").strip()):
                audio.album = album
            if (
                best.album
                and getattr(best, "artists", None)
                and not (str(audio.albumartist or "").strip())
            ):
                audio.albumartist = artists
            audio.save()
            self._mark_row_source_mutated(row, "audio_tags")
        except Exception:
            logger.exception("Failed writing scraped tags to %s", path)
            return False
        refreshed = _read_raw_tags(
            path,
            row.rel_path,
            row.dir_name,
            origin_kind=row.origin_kind,
            origin_ref=row.origin_ref,
        )
        if refreshed is None:
            return False
        # Preserve match bookkeeping fields when re-upserting from disk.
        refreshed.match_status = row.match_status
        refreshed.video_id = row.video_id
        refreshed.match_confidence = row.match_confidence
        refreshed.match_fail_count = row.match_fail_count
        refreshed.match_next_eligible_at = row.match_next_eligible_at
        # Index may still need fills when file tags stayed empty.
        if not (refreshed.title or "").strip():
            refreshed.title = title
            refreshed.title_norm = normalize_music_text(title)[:500]
        if not (refreshed.artists or "").strip():
            refreshed.artists = artists
            refreshed.artist_norm = normalize_artist_key(artists)[:500]
        if not (refreshed.album or "").strip():
            refreshed.album = album
            refreshed.album_norm = normalize_music_text(album)[:500]
        self._repository.upsert(refreshed)
        return True

    def _post_match_enrich(self, video_id: str, *, rewrite_metadata: bool) -> None:
        """After ingest: lyrics/cover (and tags when mutable) via enrich_file."""
        record = self._catalog.get_track(video_id)
        if record is None:
            return
        path = None
        for loc in self._catalog.list_locations_for_video(video_id):
            candidate = _location_abs_path(loc)
            if candidate.is_file():
                path = candidate
                break
        if path is None:
            return
        try:
            from yubal import AudioCodec, DownloadConfig, TrackMetadata
            from yubal.models.enums import MatchResult
            from yubal.services.download_service import DownloadService

            duration = None
            try:
                from mediafile import MediaFile

                length = MediaFile(path).length
                if length is not None and float(length) > 0:
                    duration = max(1, round(float(length)))
            except Exception:
                logger.debug(
                    "Could not read duration for post-match enrich %s",
                    path,
                    exc_info=True,
                )
            meta = TrackMetadata(
                source_video_id=video_id,
                title=record.title or "Unknown Track",
                artists=[record.artist or "Unknown Artist"],
                album=record.album or record.title or "Unknown Album",
                album_artists=[
                    record.album_artist or record.artist or "Unknown Artist"
                ],
                track_number=record.track_number,
                year=record.year,
                cover_url=record.cover_url,
                duration_seconds=duration,
                match_result=MatchResult.MATCHED,
            )
            service = DownloadService(
                config=DownloadConfig(
                    base_path=EXTERNAL_ROOT,
                    library_folder=str(path.parent),
                    codec=AudioCodec.OPUS,
                ),
                ytmusic_client=self._client,
            )
            audio_before = self._existing_file_signature(path)
            lyrics_path = path.with_suffix(".lrc")
            lyrics_before = self._existing_file_signature(lyrics_path)
            outcome = service.enrich_file(path, meta, rewrite_metadata=rewrite_metadata)
            if self._existing_file_changed(
                path, audio_before
            ) or self._existing_file_changed(lyrics_path, lyrics_before):
                self._mark_catalog_source_mutated(record, "media_overwritten")
            self._catalog.update_asset_state(
                video_id=video_id,
                has_embedded_cover=outcome.has_embedded_cover,
                has_lyrics_embedded=outcome.has_lyrics_embedded,
                has_lyrics_sidecar=outcome.has_lyrics_sidecar,
                lyrics=outcome.lyrics or record.lyrics,
                cover_source=outcome.cover_source,
                lyrics_source=outcome.lyrics_source,
                last_enriched_at=datetime.now(UTC),
                last_enrich_error=outcome.error,
            )
        except Exception:
            logger.exception("Post-match enrich failed for %s", video_id)

    # -- Cross-library migrate (Direct ↔ External) --

    def ensure_special_playlists(self) -> None:
        """Ensure Raw/Delete + Default playlists exist (mutable salvage/archive)."""
        ensure_external_layout()
        for name in (EXTERNAL_DELETE_DIR, EXTERNAL_DEFAULT_DIR):
            self._repository.upsert_playlist(name)
            self._repository.update_playlist_settings(name, allow_mutate=True)

    def reclaim_special_pit(self, target: str) -> DeletePlaylistResult:
        """Empty salvage pits: Raw/Delete and/or Organized/Default.

        ``target`` is ``delete``, ``default``, or ``both``. Always deletes
        index rows together with files (never ledger-only).
        """
        self.ensure_special_playlists()
        if target == "delete":
            return self.delete_playlist(
                EXTERNAL_DELETE_DIR,
                "delete_all",
                direct_folder=DIRECT_FOLDER,
            )
        if target == "default":
            return self.delete_playlist(
                EXTERNAL_DEFAULT_DIR,
                "delete_all",
                direct_folder=DIRECT_FOLDER,
            )
        if target == "both":
            deleted = self.reclaim_special_pit("delete")
            defaulted = self.reclaim_special_pit("default")
            return DeletePlaylistResult(
                deleted_files=deleted.deleted_files + defaulted.deleted_files,
                deleted_locations=(
                    deleted.deleted_locations + defaulted.deleted_locations
                ),
                deleted_raw=deleted.deleted_raw + defaulted.deleted_raw,
                reset_matches=deleted.reset_matches + defaulted.reset_matches,
                moved=deleted.moved + defaulted.moved,
                errors=deleted.errors + defaulted.errors,
            )
        raise ValueError(f"unknown reclaim target: {target}")

    def ingest_file_to_raw_delete(
        self,
        src: Path,
        *,
        origin_kind: str,
        origin_ref: str,
        title: str = "",
        artists: str = "",
        album: str = "",
        album_artist: str = "",
        year: str | None = None,
        track_number: int | None = None,
    ) -> Path | None:
        """Move a file into ``Raw/Delete`` as unmatched (YTM id cleared)."""
        return self._ingest_file_to_raw_system_folder(
            src,
            target_dir=EXTERNAL_DELETE_DIR,
            origin_kind=origin_kind,
            origin_ref=origin_ref,
            title=title,
            artists=artists,
            album=album,
            album_artist=album_artist,
            year=year,
            track_number=track_number,
        )

    def ingest_file_to_raw_default(
        self,
        src: Path,
        *,
        origin_kind: str,
        origin_ref: str,
        title: str = "",
        artists: str = "",
        album: str = "",
        album_artist: str = "",
        year: str | None = None,
        track_number: int | None = None,
    ) -> Path | None:
        """Move an unmatched file into the writable archive ingress."""
        return self._ingest_file_to_raw_system_folder(
            src,
            target_dir=EXTERNAL_DEFAULT_DIR,
            origin_kind=origin_kind,
            origin_ref=origin_ref,
            title=title,
            artists=artists,
            album=album,
            album_artist=album_artist,
            year=year,
            track_number=track_number,
        )

    def _ingest_file_to_raw_system_folder(
        self,
        src: Path,
        *,
        target_dir: str,
        origin_kind: str,
        origin_ref: str,
        title: str,
        artists: str,
        album: str,
        album_artist: str,
        year: str | None,
        track_number: int | None,
    ) -> Path | None:
        """Move a file into a system Raw folder while preserving provenance."""
        if not EXTERNAL_ROOT.is_dir():
            raise RuntimeError("external library mount not available")
        if target_dir not in {EXTERNAL_DELETE_DIR, EXTERNAL_DEFAULT_DIR}:
            raise ValueError(f"invalid system raw folder: {target_dir}")
        self.ensure_special_playlists()
        if not src.is_file():
            return None
        safe_title = (title or src.stem or "track").strip() or "track"
        safe_artist = (artists or "Unknown").strip() or "Unknown"
        base_name = f"{safe_artist} - {safe_title}{src.suffix.lower()}"
        # Avoid path separators from tags.
        base_name = base_name.replace("/", "-").replace("\\", "-")
        dest_dir = EXTERNAL_RAW_ROOT / target_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / base_name
        if dest.exists():
            stem = dest.stem
            suffix = dest.suffix
            n = 2
            while True:
                candidate = dest_dir / f"{stem} ({n}){suffix}"
                if not candidate.exists():
                    dest = candidate
                    break
                n += 1
        # Validate provenance before changing the source file.  A bad caller
        # must not leave an untracked file in Raw after the move succeeds.
        if not origin_kind or not origin_ref:
            raise ValueError("archive source provenance is required")
        self._move_file_preserving_sidecar(src, dest)
        if origin_kind == "external":
            self._mark_source_mutated(
                playlist_uid=origin_ref,
                mutation_kind="audio_moved",
            )
        rel = f"{target_dir}/{dest.name}"
        row = _read_raw_tags(
            dest,
            rel,
            target_dir,
            origin_kind=origin_kind,
            origin_ref=origin_ref,
        )
        if row is None:
            row = ExternalRawTrack(
                rel_path=rel,
                dir_name=target_dir,
                origin_kind=origin_kind,
                origin_ref=origin_ref,
                title=safe_title,
                artists=safe_artist,
                album=album or "",
                album_artist=album_artist or safe_artist,
                year=year,
                track_number=track_number,
                match_status=MATCH_UNMATCHED,
                video_id=None,
            )
        else:
            row.match_status = MATCH_UNMATCHED
            row.video_id = None
            if title:
                row.title = title
            if artists:
                row.artists = artists
        self._repository.upsert(row)
        return dest

    def ingest_matched_to_default(
        self,
        src: Path,
        *,
        relative_path: str,
        video_id: str,
        title: str,
        artist: str,
        album_artist: str = "",
        album: str = "",
        year: str | None = None,
        track_number: int | None = None,
        cover_url: str | None = None,
    ) -> Path | None:
        """Move a still-valid track into ``Organized/Default`` (id kept)."""
        if not EXTERNAL_ROOT.is_dir():
            raise RuntimeError("external library mount not available")
        if not video_id:
            raise ValueError("video_id required for Default archive")
        self.ensure_special_playlists()
        save_folder = organized_save_folder(EXTERNAL_DEFAULT_DIR)
        dest_base = EXTERNAL_ORGANIZED_ROOT / EXTERNAL_DEFAULT_DIR
        rel = relative_path.strip().replace("\\", "/").lstrip("/")
        dest = dest_base / rel
        if src.is_file():
            self._move_file_preserving_sidecar(src, dest)
        elif not dest.is_file():
            return None
        self._catalog.upsert_track(
            video_id=video_id,
            title=title or Path(rel).stem,
            artist=artist or "Unknown Artist",
            album_artist=album_artist or artist or "Unknown Artist",
            album=album or "",
            track_number=track_number,
            year=year,
            cover_url=cover_url,
        )
        self._catalog.upsert_location(
            video_id=video_id,
            save_folder=save_folder,
            relative_path=rel,
            origin="direct_migrate",
            storage_root=STORAGE_EXTERNAL,
        )
        rel_to_external = dest.resolve().relative_to(EXTERNAL_ROOT.resolve())
        self._catalog.set_canonical(
            video_id,
            storage=STORAGE_EXTERNAL,
            relative_path=str(rel_to_external),
        )
        # Archive into Default = no external hukou (Direct-like, writable).
        self._catalog.liberate_tracks([video_id])
        self._track_index.set(video_id, dest)
        return dest

    def add_one_matched_to_direct(
        self,
        dir_name: str,
        *,
        rel_path: str,
        direct_folder: str,
    ) -> dict[str, int | bool]:
        """Hardlink/copy one Organized matched track into Direct; keep external."""
        playlist = self._repository.get_playlist(dir_name)
        if playlist is None:
            raise ValueError(f"playlist not found: {dir_name}")

        normalized = rel_path.strip().replace("\\", "/").lstrip("/")
        if normalized.startswith("External/"):
            normalized = normalized[len("External/") :]
        save_folder = organized_save_folder(dir_name)
        prefix = f"{save_folder}/"
        if not normalized.startswith(prefix):
            organized_rel = normalized
        else:
            organized_rel = normalized[len(prefix) :]

        loc_rec = None
        for loc, rec in self._catalog.list_for_save_folder(save_folder):
            if loc.relative_path == organized_rel:
                loc_rec = (loc, rec)
                break
        if loc_rec is None:
            raise ValueError(f"matched track not found: {rel_path}")
        loc, rec = loc_rec

        dest_folder = sanitize_direct_folder(direct_folder)
        dest_base = DOWNLOAD_ROOT / dest_folder
        src = _location_abs_path(loc)
        dest = dest_base / loc.relative_path
        try:
            if src.is_file():
                self._link_or_copy_file_preserving_sidecar(src, dest)
            elif not dest.is_file():
                return {
                    "moved": 0,
                    "deleted_locations": 0,
                    "errors": 1,
                    "ok": False,
                }
            self._catalog.upsert_location(
                video_id=rec.video_id,
                save_folder=dest_folder,
                relative_path=loc.relative_path,
                origin="external_add",
                storage_root=STORAGE_DOWNLOAD,
            )
            self._collapse_video_inodes(rec.video_id)
        except OSError:
            logger.warning("Could not add %s to Download Center", src)
            return {
                "moved": 0,
                "deleted_locations": 0,
                "errors": 1,
                "ok": False,
            }
        return {
            "moved": 1,
            "deleted_locations": 0,
            "errors": 0,
            "ok": True,
        }

    def move_one_matched_to_direct(
        self,
        dir_name: str,
        *,
        rel_path: str,
        direct_folder: str,
    ) -> dict[str, int | bool]:
        """Move one Organized matched track into Direct (list + file)."""
        playlist = self._repository.get_playlist(dir_name)
        if playlist is None:
            raise ValueError(f"playlist not found: {dir_name}")

        normalized = rel_path.strip().replace("\\", "/").lstrip("/")
        if normalized.startswith("External/"):
            normalized = normalized[len("External/") :]
        save_folder = organized_save_folder(dir_name)
        prefix = f"{save_folder}/"
        if not normalized.startswith(prefix):
            # Allow bare organized-relative path.
            organized_rel = normalized
        else:
            organized_rel = normalized[len(prefix) :]

        loc_rec = None
        for loc, rec in self._catalog.list_for_save_folder(save_folder):
            if loc.relative_path == organized_rel:
                loc_rec = (loc, rec)
                break
        if loc_rec is None:
            raise ValueError(f"matched track not found: {rel_path}")
        loc, rec = loc_rec

        dest_folder = sanitize_direct_folder(direct_folder)
        dest_base = DOWNLOAD_ROOT / dest_folder
        src = _location_abs_path(loc)
        dest = dest_base / loc.relative_path
        moved = errors = 0
        if not src.is_file():
            logger.warning("Source missing during single move to Direct: %s", src)
            return {"moved": 0, "deleted_locations": 0, "errors": 1, "ok": False}
        try:
            self._move_file_preserving_sidecar(src, dest)
            moved = 1
            self._track_index.set(rec.video_id, dest)
            try:
                rel_dl = str(dest.resolve().relative_to(DOWNLOAD_ROOT.resolve()))
                self._catalog.set_canonical(
                    rec.video_id,
                    storage=STORAGE_DOWNLOAD,
                    relative_path=rel_dl,
                )
            except ValueError:
                pass
        except OSError:
            errors = 1
            logger.warning("Could not move %s to %s", src, dest)
            return {
                "moved": 0,
                "deleted_locations": 0,
                "errors": 1,
                "ok": False,
            }
        self._catalog.upsert_location(
            video_id=rec.video_id,
            save_folder=dest_folder,
            relative_path=loc.relative_path,
            origin="external_move",
            storage_root=STORAGE_DOWNLOAD,
        )
        self._catalog.delete_location(save_folder, loc.relative_path)
        for row in self._repository.list_matched(dir_name):
            if row.video_id == rec.video_id:
                self._repository.reset_match_state(row.rel_path)
        return {
            "moved": moved,
            "deleted_locations": 1,
            "errors": errors,
            "ok": errors == 0,
        }
