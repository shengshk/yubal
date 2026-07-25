"""Ephemeral YouTube Music song search and preview caching."""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from yubal import APIConfig, AudioCodec, DownloadConfig, MatchResult, TrackMetadata
from yubal.client import YTMusicClient
from yubal.services.download_service import DownloadService, YTDLPDownloader
from yubal.services.lyrics import (
    LrclibFetcher,
    LyricsFetcher,
    LyricsService,
    YouTubeMusicLyricsFetcher,
)
from yubal.services.qq_lyrics import QQMusicLyricsFetcher
from yubal.services.replaygain import ReplayGainService
from yubal.services.track_index import TrackFileIndex
from yubal.utils.filename import clean_filename
from yubal.utils.library import resolve_under_data

from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.schemas.search import SearchSnapshotResponse, SearchTrackResponse
from yubal_api.services.meta_search import MetaHit, same_recording
from yubal_api.services.preferences import Preferences, PreferencesStore
from yubal_api.services.sync_ledger_service import SyncLedgerService

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_PREVIEW_SUFFIXES = {".webm", ".m4a", ".mp3", ".opus", ".ogg", ".aac"}


def _supplementary_meta_hits(
    ytm_rows: list[dict[str, Any]],
    meta_hits: list[MetaHit],
    *,
    limit: int,
) -> list[MetaHit]:
    """Keep only third-party recordings not already represented by YTM."""
    supplements: list[MetaHit] = []
    for hit in meta_hits:
        candidates = [
            (
                str(row.get("title") or ""),
                str(row.get("artist") or ""),
                row.get("duration_seconds"),
            )
            for row in ytm_rows
        ]
        candidates.extend(
            (item.title, item.artist, item.duration_seconds)
            for item in supplements
        )
        if any(
            same_recording(
                left_title=title,
                left_artist=artist,
                left_duration=duration if isinstance(duration, int) else None,
                right_title=hit.title,
                right_artist=hit.artist,
                right_duration=hit.duration_seconds,
            )
            for title, artist, duration in candidates
        ):
            continue
        supplements.append(hit)
        if len(supplements) >= limit:
            break
    return supplements


class SearchService:
    """Stores one search snapshot and lightweight, non-library preview files."""

    def __init__(
        self,
        *,
        state_path: Path,
        preview_root: Path,
        data_path: Path,
        cookies_path: Path | None,
        preferences: PreferencesStore,
        track_catalog: TrackCatalogRepository,
        sync_ledger: SyncLedgerService | None = None,
    ) -> None:
        self._state_path = state_path
        self._preview_root = preview_root
        self._data_path = data_path
        self._cookies_path = cookies_path
        self._preferences = preferences
        self._track_catalog = track_catalog
        self._sync_ledger = sync_ledger
        self._index = TrackFileIndex(data_path)
        self._client = YTMusicClient(
            config=APIConfig(search_limit=5),
            cookies_path=cookies_path,
        )
        self._state_lock = threading.Lock()
        self._preview_lock = threading.Lock()

    def search(self, query: str) -> SearchSnapshotResponse | None:
        """Search 5 YTM + up to 5 meta (wishlist) songs; replace previous snapshot."""
        normalized = self._validate_query(query)
        logger.info("Online search: %r", normalized)
        results = self._client.search_songs(normalized)[:5]
        prefs = self._preferences.effective()
        meta_hits: list[MetaHit] = []
        if prefs.wanted_enabled and (
            prefs.wanted_source_musicbrainz
            or prefs.wanted_source_qq
            or prefs.wanted_source_discogs
            or prefs.wanted_source_lastfm
        ):
            from yubal_api.services.meta_search import search_meta_sources

            meta_hits = search_meta_sources(
                normalized,
                # Fetch deeper than the display limit so duplicates already
                # represented by YTM can be removed without starving the
                # useful third-party supplement list.
                limit=10,
                enable_musicbrainz=prefs.wanted_source_musicbrainz,
                enable_qq=prefs.wanted_source_qq,
                enable_discogs=prefs.wanted_source_discogs,
                enable_lastfm=prefs.wanted_source_lastfm,
                lastfm_api_key=prefs.lastfm_api_key,
            )

        if not results and not meta_hits:
            logger.info(
                "Online search: %r returned no songs (kept previous)", normalized
            )
            return None

        searched_at = datetime.now(UTC)
        tracks: list[dict[str, Any]] = []
        for rank, result in enumerate(results, start=1):
            artists = " & ".join(a.name for a in result.artists if a.name)
            tracks.append(
                {
                    "rank": rank,
                    "video_id": result.video_id,
                    "title": result.title,
                    "artist": artists or "Unknown Artist",
                    "album": result.album.name if result.album else None,
                    "thumbnail_url": (
                        result.thumbnails[-1].url if result.thumbnails else None
                    ),
                    "duration_seconds": result.duration_seconds,
                    "result_kind": "ytm",
                    "source": "ytm",
                    "source_id": result.video_id,
                    "source_url": (
                        f"https://music.youtube.com/watch?v={result.video_id}"
                    ),
                    "wishable": False,
                }
            )
        meta_hits = _supplementary_meta_hits(tracks, meta_hits, limit=5)
        for offset, hit in enumerate(meta_hits, start=1):
            tracks.append(
                {
                    "rank": len(results) + offset,
                    "video_id": "",
                    "title": hit.title,
                    "artist": hit.artist,
                    "album": hit.album,
                    "thumbnail_url": hit.thumbnail_url,
                    "duration_seconds": hit.duration_seconds,
                    "result_kind": "meta",
                    "source": hit.source,
                    "source_id": hit.source_id,
                    "source_url": hit.source_url,
                    "wishable": True,
                }
            )

        payload = {
            "query": normalized,
            "searched_at": searched_at.isoformat(),
            "tracks": tracks,
        }
        with self._state_lock:
            self._clear_preview_cache_unlocked()
            self._write_state_unlocked(payload)
        response = self._to_response(payload)
        logger.info(
            "Online search: %r returned %d songs (%d matched locally)",
            normalized,
            response.total_count,
            response.matched_count,
        )
        return response

    def current(self) -> SearchSnapshotResponse | None:
        """Return the active snapshot, deleting it and previews when expired."""
        with self._state_lock:
            payload = self._read_state_unlocked()
            if payload is None:
                return None
            if self._is_expired(payload):
                self._delete_state_unlocked()
                self._clear_preview_cache_unlocked()
                return None
        return self._to_response(payload)

    def delete(self) -> bool:
        """Delete the current snapshot and all preview files."""
        with self._state_lock:
            existed = self._state_path.is_file()
            self._delete_state_unlocked()
            self._clear_preview_cache_unlocked()
        if existed:
            logger.info("Cleared online search results and preview cache")
        return existed

    def prepare_preview(self, video_id: str) -> Path:
        """Download an unmatched result to temporary preview storage."""
        snapshot = self.current()
        tracks = snapshot.tracks if snapshot is not None else []
        track = next((t for t in tracks if t.video_id == video_id), None)
        if track is None:
            raise FileNotFoundError("search result not found")
        if track.matched:
            raise ValueError("local tracks do not need a preview")

        with self._preview_lock:
            existing = self.preview_file(video_id)
            if existing is not None:
                return existing
            self._preview_root.mkdir(parents=True, exist_ok=True)
            prefs = self._preferences.effective()
            try:
                codec = AudioCodec(prefs.audio_format)
            except ValueError:
                codec = AudioCodec.OPUS
            downloader = YTDLPDownloader(
                DownloadConfig(
                    base_path=self._preview_root,
                    codec=codec,
                    quality=prefs.audio_quality,
                    fetch_lyrics=False,
                    ytmusic_lyrics_fallback=False,
                    qq_lyrics_fallback=False,
                ),
                cookies_path=self._cookies_path,
            )
            label = f"{track.artist} - {track.title}"
            logger.info("Preparing preview: %s (%s)", label, video_id)
            try:
                # Downloader appends the codec extension to this stem.
                downloader.download(video_id, self._preview_root / video_id)
            except Exception as exc:
                logger.warning("Preview failed for %s (%s): %s", label, video_id, exc)
                raise
            created = self.preview_file(video_id)
            if created is None:
                raise RuntimeError("preview download produced no playable file")
            logger.info("Preview ready: %s (%s)", label, video_id)
            return created

    def promote_preview(self, video_id: str) -> SearchSnapshotResponse:
        """Import a cached preview into Direct without re-downloading."""
        snapshot = self.current()
        tracks = snapshot.tracks if snapshot is not None else []
        track = next((t for t in tracks if t.video_id == video_id), None)
        if track is None:
            raise FileNotFoundError("search result not found")
        if track.matched and track.local_path:
            return snapshot

        with self._preview_lock:
            preview = self.preview_file(video_id)
            if preview is None:
                raise FileNotFoundError("preview not cached")

            prefs = self._preferences.effective()
            direct_folder = prefs.direct_folder
            artist = clean_filename(track.artist) or "Unknown Artist"
            title = clean_filename(track.title) or "Unknown Track"
            safe_id = clean_filename(video_id) or video_id
            relative = f"{artist} - {title} [{safe_id}]{preview.suffix.lower()}"
            dest = resolve_under_data(self._data_path, f"{direct_folder}/{relative}")
            dest.parent.mkdir(parents=True, exist_ok=True)

            if not dest.exists():
                try:
                    shutil.copy2(preview, dest)
                except OSError as exc:
                    raise RuntimeError(f"Could not import preview: {exc}") from exc

            # 方案 3: reuse the normal download post-processing on the cached
            # audio (tags + best cover + lyrics), then ReplayGain, so imported
            # previews reach the same quality as an input-URL direct download.
            meta = self._track_metadata(track)
            outcome = None
            enrich_error: str | None = None
            try:
                downloader = self._build_download_service(prefs)
                outcome = downloader.enrich_file(
                    dest,
                    meta,
                    rewrite_metadata=True,
                    respect_lyrics_cooldown=False,
                )
            except Exception as exc:
                logger.exception(
                    "Post-processing failed for imported preview %s", video_id
                )
                enrich_error = str(exc) or exc.__class__.__name__
            if prefs.replaygain:
                try:
                    codec = self._codec(prefs)
                    ReplayGainService().apply_replaygain(
                        [dest], codec, album_mode=False
                    )
                except Exception:
                    logger.exception(
                        "ReplayGain failed for imported preview %s", video_id
                    )

            # Write the full catalog row exactly like a normal download so
            # lyrics/cover flags reflect the finalized file.
            try:
                self._track_catalog.record_from_download(
                    video_id=video_id,
                    title=track.title,
                    artist=track.artist,
                    album_artist=track.artist,
                    album=track.album or "",
                    track_number=None,
                    year=None,
                    cover_url=track.thumbnail_url,
                    save_folder=direct_folder,
                    absolute_path=dest,
                    data_root=self._data_path,
                    origin="direct",
                )
            except Exception:
                logger.exception(
                    "Catalog write failed for imported preview %s", video_id
                )
                self._track_catalog.upsert_track(
                    video_id=video_id,
                    title=track.title,
                    artist=track.artist,
                    album_artist=track.artist,
                    album=track.album or "",
                    cover_url=track.thumbnail_url,
                )
                self._track_catalog.upsert_location(
                    video_id=video_id,
                    save_folder=direct_folder,
                    relative_path=relative,
                    origin="direct",
                )

            # Record enrichment provenance + failures so a failed cover/lyrics
            # fetch is retried by the library enrichment pass instead of leaving
            # a silent gap (cover_url present but nothing embedded).
            now = datetime.now(UTC)
            try:
                if outcome is not None:
                    self._track_catalog.update_asset_state(
                        video_id=video_id,
                        has_embedded_cover=outcome.has_embedded_cover,
                        has_lyrics_embedded=outcome.has_lyrics_embedded,
                        has_lyrics_sidecar=outcome.has_lyrics_sidecar,
                        lyrics=outcome.lyrics,
                        cover_source=outcome.cover_source,
                        lyrics_source=outcome.lyrics_source,
                        last_enriched_at=now,
                        last_enrich_error=outcome.error,
                    )
                else:
                    self._track_catalog.mark_enriched(
                        video_id, at=now, error=enrich_error
                    )
            except Exception:
                logger.exception(
                    "Failed to record enrichment state for %s", video_id
                )
            self._index.set(video_id, dest)
            if self._sync_ledger is not None:
                self._sync_ledger.reconcile_direct(direct_folder)

            logger.info(
                "Imported preview to Download Center: %s - %s (%s)",
                track.artist,
                track.title,
                video_id,
            )
            updated = self.current()
            if updated is None:
                raise RuntimeError("search snapshot missing after import")
            return updated

    def preview_lyrics(self, video_id: str) -> tuple[str, str] | None:
        """Fetch lyrics on demand for a search result being previewed.

        Previews are downloaded without lyrics for speed; this resolves them
        live using the same source chain a normal download uses (prefer synced).
        Returns ``(lrc_text, source)`` or ``None`` when unavailable.
        """
        snapshot = self.current()
        tracks = snapshot.tracks if snapshot is not None else []
        track = next((t for t in tracks if t.video_id == video_id), None)
        if track is None:
            return None

        duration = track.duration_seconds
        if not duration:
            preview = self.preview_file(video_id)
            if preview is not None and preview.is_file():
                try:
                    from mediafile import MediaFile

                    length = MediaFile(preview).length
                    if length is not None and float(length) > 0:
                        duration = max(1, round(float(length)))
                except Exception:
                    logger.debug(
                        "Could not read preview duration for %s",
                        video_id,
                        exc_info=True,
                    )
        # YTM can run with video_id alone; lrclib/QQ need duration.
        if not duration and not video_id:
            return None

        prefs = self._preferences.effective()
        service = self._build_lyrics_service(prefs)
        if service is None:
            return None
        try:
            lyrics, source, _ = service.fetch_lyrics(
                title=track.title,
                artist=track.artist,
                duration_seconds=int(duration or 0),
                video_id=video_id,
            )
        except Exception:
            logger.exception("Preview lyrics fetch failed for %s", video_id)
            return None
        if not lyrics:
            return None
        return lyrics, source or "lrclib"

    def _codec(self, prefs: Preferences) -> AudioCodec:
        try:
            return AudioCodec(prefs.audio_format)
        except ValueError:
            return AudioCodec.OPUS

    def _track_metadata(self, track: SearchTrackResponse) -> TrackMetadata:
        """Build a TrackMetadata from a search result for post-processing."""
        artist = track.artist or "Unknown Artist"
        return TrackMetadata(
            source_video_id=track.video_id,
            title=track.title or "Unknown Track",
            artists=[artist],
            album=track.album or track.title or "Unknown Album",
            album_artists=[artist],
            cover_url=track.thumbnail_url,
            duration_seconds=track.duration_seconds,
            match_result=MatchResult.MATCHED,
        )

    def _build_download_service(self, prefs: Preferences) -> DownloadService:
        config = DownloadConfig(
            base_path=self._data_path,
            codec=self._codec(prefs),
            quality=prefs.audio_quality,
            fetch_lyrics=prefs.fetch_lyrics,
            ytmusic_lyrics_fallback=prefs.ytmusic_lyrics_fallback,
            qq_lyrics_fallback=prefs.qq_lyrics_fallback,
            scrape_cooldown_hours=prefs.scrape_cooldown_hours,
            cover_excellence_px=int(getattr(prefs, "cover_excellence_px", 0) or 0),
            library_folder=prefs.direct_folder,
        )
        return DownloadService(
            config,
            cookies_path=self._cookies_path,
            ytmusic_client=self._client,
        )

    def _build_lyrics_service(self, prefs: Preferences) -> LyricsService | None:
        if not prefs.fetch_lyrics:
            return None
        fetchers: list[LyricsFetcher] = [LrclibFetcher()]
        if prefs.ytmusic_lyrics_fallback:
            fetchers.append(YouTubeMusicLyricsFetcher(self._client))
        if prefs.qq_lyrics_fallback:
            fetchers.append(QQMusicLyricsFetcher())
        return LyricsService(fetchers=fetchers)

    def preview_file(self, video_id: str) -> Path | None:
        """Return a cached preview path for a current search result."""
        if not video_id or "/" in video_id or "\\" in video_id:
            return None
        try:
            candidates = sorted(self._preview_root.glob(f"{video_id}.*"))
        except OSError:
            return None
        return next(
            (
                path
                for path in candidates
                if path.is_file() and path.suffix.lower() in _PREVIEW_SUFFIXES
            ),
            None,
        )

    def _to_response(self, payload: dict[str, Any]) -> SearchSnapshotResponse:
        searched_at = self._searched_at(payload)
        ttl = self._preferences.effective().search_result_ttl_hours
        expires_at = searched_at + timedelta(hours=ttl)
        raw_tracks = payload.get("tracks")
        tracks_data = raw_tracks if isinstance(raw_tracks, list) else []
        video_ids = [
            str(item.get("video_id", ""))
            for item in tracks_data
            if isinstance(item, dict) and item.get("video_id")
        ]
        paths = self._track_catalog.resolve_existing_paths(
            video_ids,
            data_root=self._data_path,
        )
        tracks: list[SearchTrackResponse] = []
        for item in tracks_data:
            if not isinstance(item, dict):
                continue
            video_id = str(item.get("video_id", "") or "")
            result_kind = str(
                item.get("result_kind") or ("ytm" if video_id else "meta")
            )
            wishable = bool(item.get("wishable")) or result_kind == "meta"
            if result_kind == "ytm" and not video_id:
                continue
            local_path = paths.get(video_id) if video_id else None
            matched = local_path is not None
            tracks.append(
                SearchTrackResponse(
                    rank=int(item.get("rank", len(tracks) + 1)),
                    video_id=video_id,
                    title=str(item.get("title", "Unknown")),
                    artist=str(item.get("artist", "Unknown Artist")),
                    album=str(item["album"]) if item.get("album") else None,
                    thumbnail_url=(
                        str(item["thumbnail_url"])
                        if item.get("thumbnail_url")
                        else None
                    ),
                    duration_seconds=(
                        int(item["duration_seconds"])
                        if item.get("duration_seconds") is not None
                        else None
                    ),
                    matched=matched,
                    local_path=local_path,
                    preview_cached=(
                        bool(video_id)
                        and not matched
                        and self.preview_file(video_id) is not None
                    ),
                    result_kind=result_kind,
                    source=str(item["source"]) if item.get("source") else None,
                    source_id=str(item["source_id"]) if item.get("source_id") else None,
                    source_url=(
                        str(item["source_url"]) if item.get("source_url") else None
                    ),
                    wishable=wishable,
                )
            )
        return SearchSnapshotResponse(
            query=str(payload.get("query", "")),
            searched_at=searched_at,
            expires_at=expires_at,
            total_count=len(tracks),
            matched_count=sum(1 for track in tracks if track.matched),
            cached_count=sum(1 for track in tracks if track.preview_cached),
            tracks=tracks,
        )

    @staticmethod
    def _validate_query(query: str) -> str:
        normalized = " ".join(query.strip().split())
        if not normalized or len(normalized) > 200:
            raise ValueError("search query must contain 1 to 200 characters")
        if _URL_RE.search(normalized):
            raise ValueError("online search accepts text, not URLs")
        if any(ord(char) < 32 for char in normalized):
            raise ValueError("search query contains control characters")
        return normalized

    def _is_expired(self, payload: dict[str, Any]) -> bool:
        searched_at = self._searched_at(payload)
        ttl = self._preferences.effective().search_result_ttl_hours
        return datetime.now(UTC) >= searched_at + timedelta(hours=ttl)

    @staticmethod
    def _searched_at(payload: dict[str, Any]) -> datetime:
        raw = str(payload.get("searched_at", ""))
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return datetime.fromtimestamp(0, tz=UTC)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _read_state_unlocked(self) -> dict[str, Any] | None:
        if not self._state_path.is_file():
            return None
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Could not read search result state", exc_info=True)
            return None
        return raw if isinstance(raw, dict) else None

    def _write_state_unlocked(self, payload: dict[str, Any]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._state_path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(self._state_path)

    def _delete_state_unlocked(self) -> None:
        self._state_path.unlink(missing_ok=True)

    def _clear_preview_cache_unlocked(self) -> None:
        if self._preview_root.exists():
            shutil.rmtree(self._preview_root, ignore_errors=True)
