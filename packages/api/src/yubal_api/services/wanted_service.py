"""Wishlist / wanted playlist service."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from yubal import AudioCodec, DownloadConfig, MatchResult, TrackMetadata
from yubal.client import YTMusicClient
from yubal.config import APIConfig
from yubal.lib.matching import has_version_marker, match_artists, match_title
from yubal.services.download_service import DownloadService
from yubal.utils.filename import build_track_path
from yubal.utils.library import (
    DIRECT_FOLDER,
    DOWNLOAD_ROOT,
    STORAGE_DOWNLOAD,
    STORAGE_ROOTS,
    WANTED_ROOT,
    ensure_wanted_layout,
    runtime_state_path,
)
from yubal.utils.normalize_text import normalize_artist_key, normalize_music_text

from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.db.wanted import WantedTrack
from yubal_api.db.wanted_repository import WantedRepository
from yubal_api.schemas.wanted import (
    WantedAddRequest,
    WantedSummary,
    WantedTrackResponse,
)
from yubal_api.services.preferences import PreferencesStore

logger = logging.getLogger(__name__)

_MATCH_TITLE_THRESHOLD = 70.0
_MATCH_ARTIST_THRESHOLD = 62.0
_LAST_SYNC_NAME = ".last_sync.json"


class WantedSearchUnavailable(RuntimeError):
    """The YTM search request did not reach a trustworthy no-result answer."""


@dataclass
class WantedEnrichmentSummary:
    scanned: int = 0
    completed: int = 0
    covers_written: int = 0
    lyrics_written: int = 0
    failed: int = 0
    sidecar_only: int = 0


class WantedService:
    """Manage wishlist items under ``/data/wanted`` (no zombie-file delete modes)."""

    def __init__(
        self,
        repo: WantedRepository,
        preferences: PreferencesStore,
        *,
        cookies_path: Path | None = None,
        sync_ledger: Any = None,
        external_library: Any = None,
        catalog: TrackCatalogRepository | None = None,
        job_executor: Any = None,
        media_changed: Callable[[], object] | None = None,
    ) -> None:
        self._repo = repo
        self._preferences = preferences
        self._cookies_path = cookies_path
        self._sync_ledger = sync_ledger
        self._external = external_library
        self._catalog = catalog
        self._job_executor = job_executor
        self._media_changed = media_changed
        self._lock = threading.Lock()
        self._client = YTMusicClient(
            config=APIConfig(search_limit=8),
            cookies_path=cookies_path,
        )
        ensure_wanted_layout()

    def bind_sync_ledger(self, sync_ledger: Any) -> None:
        self._sync_ledger = sync_ledger

    def bind_external(self, external: Any) -> None:
        self._external = external

    def bind_job_executor(self, job_executor: Any) -> None:
        self._job_executor = job_executor

    def bind_media_changed(self, callback: Callable[[], object]) -> None:
        """Register the library-summary invalidator after service construction."""
        self._media_changed = callback

    def _notify_media_changed(self) -> None:
        if callable(self._media_changed):
            self._media_changed()

    def add_from_offline(
        self,
        *,
        title: str,
        artists: str,
        album: str = "",
        source_path: Path | None = None,
        thumbnail_url: str | None = None,
    ) -> WantedTrackResponse:
        """Bypass wanted_enabled for ID-invalid salvage into wishlist."""
        title = (title or "").strip()
        artists = (artists or "").strip()
        album = (album or "").strip()
        if not title or not artists:
            raise ValueError("title and artists are required")
        title_norm = normalize_music_text(title)
        artist_norm = normalize_artist_key(artists)
        album_norm = normalize_music_text(album)
        existing = self._repo.find_by_norms(
            title_norm=title_norm, artist_norm=artist_norm, album_norm=album_norm
        )
        if existing is None:
            row = WantedTrack(
                title=title[:500],
                artists=artists[:500],
                album=album[:500],
                title_norm=title_norm[:500],
                artist_norm=artist_norm[:500],
                album_norm=album_norm[:500],
                source="id_invalid",
                thumbnail_url=thumbnail_url,
            )
            row = self._repo.add(row)
        else:
            row = existing
        if source_path is not None and source_path.is_file() and not row.relative_path:
            ensure_wanted_layout()
            dest = build_track_path(
                WANTED_ROOT,
                artists.split("&")[0].strip() or artists,
                None,
                album or "Unknown Album",
                None,
                title,
            ).with_suffix(source_path.suffix)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                try:
                    os.link(source_path, dest)
                except OSError:
                    shutil.copy2(source_path, dest)
            row.relative_path = str(dest.relative_to(WANTED_ROOT)).replace("\\", "/")
            row = self._repo.save(row)
        if row.relative_path:
            self._enrich_one_wanted(row)
        return self._to_response(row, display_index="W?" if row.relative_path else None)

    def add_from_liked_recovery(
        self,
        *,
        title: str,
        artists: str,
        album: str = "",
        source_video_id: str,
        source_path: Path | None = None,
        thumbnail_url: str | None = None,
    ) -> WantedTrackResponse:
        """Keep an ID-invalid cloud Like as a local-heart recovery item."""
        response = self.add_from_offline(
            title=title,
            artists=artists,
            album=album,
            source_path=source_path,
            thumbnail_url=thumbnail_url,
        )
        row = self._repo.get(UUID(response.id))
        if row is None:
            return response
        # Do not overwrite a pre-existing manual heart for the same tags: it
        # already has the stronger local-intent semantics.
        if row.source in {"id_invalid", "liked_recovery"}:
            row.source = "liked_recovery"
            row.source_id = (source_video_id or "")[:128]
            row.source_url = (
                f"https://music.youtube.com/watch?v={source_video_id}"
                if source_video_id
                else None
            )
            row = self._repo.save(row)
        return self._to_response(row, display_index="R?")

    def confirm_remote_like(self, video_id: str) -> int:
        """Promote confirmed local hearts into Liked Music without orphaning files."""
        video_id = (video_id or "").strip()
        if not video_id:
            return 0
        # Liked Music is fixed at ``liked``. Keep the file operation local to
        # this service so a DB row and its wanted file are retired together.
        target_folder = "liked"
        promoted = 0
        for row in self._repo.list_by_video_id(video_id):
            source = WANTED_ROOT / row.relative_path if row.relative_path else None
            try:
                destination: Path | None = None
                if source is not None and source.is_file():
                    destination = build_track_path(
                        DOWNLOAD_ROOT / target_folder,
                        row.artists.split("&")[0].strip() or row.artists,
                        None,
                        row.album or "Unknown Album",
                        None,
                        row.title,
                        video_id=video_id,
                    ).with_suffix(source.suffix)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if not destination.exists():
                        try:
                            os.link(source, destination)
                        except OSError:
                            shutil.copy2(source, destination)
                    source_lrc = source.with_suffix(".lrc")
                    destination_lrc = destination.with_suffix(".lrc")
                    if source_lrc.is_file() and not destination_lrc.exists():
                        try:
                            os.link(source_lrc, destination_lrc)
                        except OSError:
                            shutil.copy2(source_lrc, destination_lrc)
                    recorder = getattr(self._sync_ledger, "record_existing_track", None)
                    if callable(recorder):
                        recorder(
                            video_id=video_id,
                            title=row.title,
                            artist=row.artists,
                            album=row.album or row.title,
                            cover_url=row.thumbnail_url,
                            save_folder=target_folder,
                            absolute_path=destination,
                            origin="liked_local_heart",
                        )
                    if not self._unlink_wanted_file(source):
                        # The target may already have been materialised, but
                        # keeping the wanted row is safer than hiding a source
                        # file that could not be retired.
                        raise OSError(f"could not retire wanted source {source}")
                self._repo.delete(row.id)
                promoted += 1
                self._notify_media_changed()
            except Exception:
                # Keep both the row and source file retryable if promotion is
                # interrupted; do not create an invisible orphan.
                logger.exception("Could not promote confirmed heart %s", row.id)
        return promoted

    def add_from_external_meta(
        self,
        *,
        title: str,
        artists: str,
        album: str = "",
        source: str = "manual",
        source_id: str = "",
        source_url: str | None = None,
        thumbnail_url: str | None = None,
        source_path: Path | None = None,
    ) -> WantedTrackResponse:
        """Add a meta-verified external unmatched track into the wishlist."""
        prefs = self._preferences.effective()
        if not prefs.wanted_enabled:
            raise ValueError("wishlist is disabled")
        title = (title or "").strip()
        artists = (artists or "").strip()
        album = (album or "").strip()
        if not title or not artists or not album:
            raise ValueError("title, artists and album are required")
        title_norm = normalize_music_text(title)
        artist_norm = normalize_artist_key(artists)
        album_norm = normalize_music_text(album)
        existing = self._repo.find_by_norms(
            title_norm=title_norm, artist_norm=artist_norm, album_norm=album_norm
        )
        if existing is None:
            row = WantedTrack(
                title=title[:500],
                artists=artists[:500],
                album=album[:500],
                title_norm=title_norm[:500],
                artist_norm=artist_norm[:500],
                album_norm=album_norm[:500],
                source=(source or "manual")[:32],
                source_id=(source_id or "")[:128],
                source_url=source_url,
                thumbnail_url=thumbnail_url,
            )
            row = self._repo.add(row)
        else:
            row = existing
            if not row.source_url and source_url:
                row.source = (source or row.source or "manual")[:32]
                row.source_id = (source_id or row.source_id or "")[:128]
                row.source_url = source_url
                if thumbnail_url and not row.thumbnail_url:
                    row.thumbnail_url = thumbnail_url
                row = self._repo.save(row)
        if source_path is not None and source_path.is_file() and not row.relative_path:
            ensure_wanted_layout()
            dest = build_track_path(
                WANTED_ROOT,
                artists.split("&")[0].strip() or artists,
                None,
                album or "Unknown Album",
                None,
                title,
            ).with_suffix(source_path.suffix)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                try:
                    os.link(source_path, dest)
                except OSError:
                    shutil.copy2(source_path, dest)
            row.relative_path = str(dest.relative_to(WANTED_ROOT)).replace("\\", "/")
            row = self._repo.save(row)
        if row.relative_path:
            self._enrich_one_wanted(row)
        return self._to_response(row, display_index="W?" if row.relative_path else None)

    # -- list / summary -------------------------------------------------

    def summary(self) -> WantedSummary:
        rows = self._repo.list_all()
        matched_rows = [r for r in rows if r.relative_path]
        unmatched = len(rows) - len(matched_rows)
        exclusive = hardlink = 0
        for row in matched_rows:
            path = WANTED_ROOT / row.relative_path
            if not path.is_file():
                continue
            try:
                nlink = path.stat().st_nlink
            except OSError:
                nlink = 1
            if nlink > 1:
                hardlink += 1
            else:
                exclusive += 1
        last_at, last_status = self._read_last_sync()
        return WantedSummary(
            total_count=len(rows),
            local_heart_count=sum(r.source != "liked_recovery" for r in rows),
            recovery_count=sum(r.source == "liked_recovery" for r in rows),
            matched_file_count=len(matched_rows),
            unmatched_count=unmatched,
            exclusive_count=exclusive,
            shared_count=0,
            hardlink_count=hardlink,
            enabled=self._preferences.effective().wanted_enabled,
            auto_match_enabled=self._preferences.effective().wanted_auto_match_enabled,
            last_matched_at=last_at,
            last_job_status=last_status,
        )

    def _last_sync_path(self) -> Path:
        ensure_wanted_layout()
        return runtime_state_path(
            WANTED_ROOT,
            "last_sync.json",
            legacy_path=WANTED_ROOT / _LAST_SYNC_NAME,
        )

    def _read_last_sync(self) -> tuple[datetime | None, str | None]:
        path = self._last_sync_path()
        if not path.is_file():
            return None, None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None, None
        at_raw = raw.get("at")
        status = raw.get("status")
        at: datetime | None = None
        if isinstance(at_raw, str) and at_raw.strip():
            try:
                at = datetime.fromisoformat(at_raw.replace("Z", "+00:00"))
            except ValueError:
                at = None
        if status is not None:
            status = str(status)
        return at, status

    def _write_last_sync(self, *, status: str) -> None:
        path = self._last_sync_path()
        payload = {
            "at": datetime.now(UTC).isoformat(),
            "status": status,
        }
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            logger.exception("Failed to write wanted last-sync marker")

    def record_sync_result(self, *, ok: bool) -> None:
        """Persist playlist-level last-match time (manual Sync / scheduler)."""
        self._write_last_sync(status="completed" if ok else "failed")

    def list_tracks(self) -> list[WantedTrackResponse]:
        rows = self._repo.list_all()
        recovery_rows = [r for r in rows if r.source == "liked_recovery"]
        local_rows = [r for r in rows if r.source != "liked_recovery"]
        out: list[WantedTrackResponse] = []
        for i, row in enumerate(recovery_rows, start=1):
            out.append(self._to_response(row, display_index=f"R{i}"))
        for i, row in enumerate(local_rows, start=1):
            out.append(self._to_response(row, display_index=f"H{i}"))
        return out

    # -- add ------------------------------------------------------------

    def add(self, body: WantedAddRequest) -> WantedTrackResponse:
        prefs = self._preferences.effective()
        if not prefs.wanted_enabled:
            raise ValueError("wishlist is disabled")
        title = (body.title or "").strip()
        artists = (body.artists or "").strip()
        album = (body.album or "").strip()
        if not title or not artists:
            raise ValueError("title and artists are required")
        title_norm = normalize_music_text(title)
        artist_norm = normalize_artist_key(artists)
        album_norm = normalize_music_text(album)
        existing = self._repo.find_by_norms(
            title_norm=title_norm, artist_norm=artist_norm, album_norm=album_norm
        )
        if existing is not None:
            return self._to_response(
                existing,
                display_index="W?" if existing.relative_path else None,
            )
        row = WantedTrack(
            title=title[:500],
            artists=artists[:500],
            album=album[:500],
            title_norm=title_norm[:500],
            artist_norm=artist_norm[:500],
            album_norm=album_norm[:500],
            source=(body.source or "manual")[:32],
            source_id=(body.source_id or "")[:128],
            source_url=body.source_url,
            thumbnail_url=body.thumbnail_url,
            duration_seconds=body.duration_seconds,
        )
        row = self._repo.add(row)
        # Best-effort immediate local hardlink match
        try:
            self.try_link_local(row.id)
            refreshed = self._repo.get(row.id)
            if refreshed is not None:
                row = refreshed
        except Exception as exc:
            logger.info("Wanted local link after add failed: %s", exc)
        return self._to_response(row, display_index="W?" if row.relative_path else None)

    # -- delete (no zombie options) -------------------------------------

    def delete_track(self, track_id: UUID, *, mode: str) -> None:
        """Delete one wishlist item.

        Modes:
        - ``remove``: unmatched tag-only row
        - ``wipe_list``: drop wish + unlink wanted file (other hardlinks stay)
        - ``to_raw_delete``: drop wish + move file into Raw/Delete
        """
        row = self._repo.get(track_id)
        if row is None:
            raise FileNotFoundError("wanted track not found")
        with self._lock:
            self._cancel_pending_remote_like(row)
            has_file = (
                bool(row.relative_path) and (WANTED_ROOT / row.relative_path).is_file()
            )
            if not row.relative_path or not has_file:
                if mode not in {"remove", "wipe_list", "to_raw_delete"}:
                    raise ValueError("invalid delete mode")
                self._repo.delete(track_id)
                return
            path = WANTED_ROOT / row.relative_path
            if mode == "wipe_list":
                if not self._unlink_wanted_file(path):
                    raise OSError(f"could not remove wanted file {path}")
                self._repo.delete(track_id)
                self._notify_media_changed()
                return
            if mode == "to_raw_delete":
                self._move_to_raw_delete(path, row=row)
                self._repo.delete(track_id)
                self._notify_media_changed()
                return
            raise ValueError("invalid delete mode for matched file")

    def delete_playlist(self, *, mode: str) -> dict:
        """Bulk delete wishlist.

        Modes:
        - ``wipe_list``: drop all wishes + unlink wanted files
        - ``to_raw_delete``: drop all wishes; matched files → Raw/Delete
        """
        rows = self._repo.list_all()
        removed = 0
        with self._lock:
            # Do all remote unlikes first. A failed write must not make local
            # intent disappear while the cloud Like remains active.
            for row in rows:
                self._cancel_pending_remote_like(row)
            if mode == "wipe_list":
                for row in rows:
                    if row.relative_path:
                        path = WANTED_ROOT / row.relative_path
                        if not self._unlink_wanted_file(path):
                            logger.error(
                                "Keeping wanted row after unlink failure: %s",
                                row.id,
                            )
                            continue
                    self._repo.delete(row.id)
                    removed += 1
            elif mode == "to_raw_delete":
                for row in rows:
                    if row.relative_path:
                        path = WANTED_ROOT / row.relative_path
                        self._move_to_raw_delete(path, row=row)
                    self._repo.delete(row.id)
                    removed += 1
            else:
                raise ValueError("invalid playlist delete mode")
        if removed:
            self._notify_media_changed()
        return {"removed": removed}

    def _unlink_wanted_file(self, path: Path) -> bool:
        """Remove the wanted hardlink (and sibling .lrc when linked)."""
        if not path.is_file():
            return True
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Failed to unlink wanted file %s", path)
            return False
        lrc = path.with_suffix(".lrc")
        if lrc.is_file():
            try:
                lrc.unlink(missing_ok=True)
            except OSError:
                logger.warning("Could not remove wanted lyric sidecar %s", lrc)
        return True

    def _cancel_pending_remote_like(self, row: WantedTrack) -> None:
        """Undo the optimistic Like when a locally-originated heart is removed."""
        if row.video_id and row.source != "liked_recovery":
            self._client.rate_song(row.video_id, liked=False)

    # -- local hardlink (strict triple) ---------------------------------

    def try_link_local(
        self,
        track_id: UUID,
        *,
        _catalog_index: dict[tuple[str, str, str], list[Path]] | None = None,
    ) -> WantedTrackResponse:
        row = self._repo.get(track_id)
        if row is None:
            raise FileNotFoundError("wanted track not found")
        if row.relative_path and (WANTED_ROOT / row.relative_path).is_file():
            return self._to_response(row, display_index="W?")
        hit = self._find_strict_local(
            title_norm=row.title_norm,
            artist_norm=row.artist_norm,
            album_norm=row.album_norm,
            catalog_index=_catalog_index,
        )
        if hit is None:
            raise FileNotFoundError("no strict local match")
        ensure_wanted_layout()
        dest = build_track_path(
            WANTED_ROOT,
            row.artists.split("&")[0].strip() or row.artists,
            None,
            row.album or "Unknown Album",
            None,
            row.title,
        ).with_suffix(hit.suffix)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            try:
                os.link(hit, dest)
            except OSError:
                shutil.copy2(hit, dest)
            lrc = hit.with_suffix(".lrc")
            if lrc.is_file():
                dest_lrc = dest.with_suffix(".lrc")
                if not dest_lrc.exists():
                    try:
                        os.link(lrc, dest_lrc)
                    except OSError:
                        pass
        rel = str(dest.relative_to(WANTED_ROOT)).replace("\\", "/")
        row.relative_path = rel
        row = self._repo.save(row)
        self._notify_media_changed()
        return self._to_response(row, display_index="W?")

    def match_local_batch(self, *, limit: int = 50) -> int:
        linked = 0
        catalog_index = self._build_local_catalog_index()
        for row in self._repo.list_all():
            if linked >= limit:
                break
            if row.relative_path:
                continue
            try:
                self.try_link_local(row.id, _catalog_index=catalog_index)
                linked += 1
            except FileNotFoundError:
                continue
            except Exception as exc:
                logger.info("Wanted local match skip %s: %s", row.id, exc)
        if linked:
            self._notify_media_changed()
        return linked

    def run_sync_pass(
        self, *, force_ytm: bool = False, limit: int | None = None
    ) -> dict:
        """Run the shared Wanted stages: link → enrich/verify → YTM fulfill."""
        prefs = self._preferences.effective()
        cap = limit if limit is not None else prefs.wanted_max_items
        ok = True
        linked = 0
        assets = WantedEnrichmentSummary()
        ytm: dict = {"matched": 0, "failed": 0}
        try:
            linked = self.match_local_batch(limit=min(50, max(1, cap)))
        except Exception as exc:
            ok = False
            logger.warning("Wanted local match failed: %s", exc)
        try:
            assets = self.enrich_existing_files(limit=min(50, max(1, cap)))
        except Exception as exc:
            ok = False
            logger.warning("Wanted asset enrichment failed: %s", exc)
        try:
            ytm = self.match_ytm_batch(limit=min(25, max(1, cap)), force=force_ytm)
        except Exception as exc:
            ok = False
            logger.warning("Wanted YTM match failed: %s", exc)
        self.record_sync_result(ok=ok)
        return {
            "linked": linked,
            "asset_scanned": assets.scanned,
            "asset_completed": assets.completed,
            "covers_written": assets.covers_written,
            "lyrics_written": assets.lyrics_written,
            "asset_failed": assets.failed,
            "sidecar_only": assets.sidecar_only,
            **ytm,
        }

    def enrich_existing_files(self, *, limit: int = 50) -> WantedEnrichmentSummary:
        """Complete cover/lyrics for Wanted files, even before a YTM ID exists."""
        summary = WantedEnrichmentSummary()
        for row in self._repo.list_all():
            if summary.scanned >= limit:
                break
            if not row.relative_path:
                continue
            self._enrich_one_wanted(row, summary=summary)
        return summary

    def _enrich_one_wanted(
        self,
        row: WantedTrack,
        *,
        summary: WantedEnrichmentSummary | None = None,
    ) -> WantedEnrichmentSummary:
        result = summary or WantedEnrichmentSummary()
        if not row.relative_path:
            return result
        path = WANTED_ROOT / row.relative_path
        if not path.is_file():
            return result

        result.scanned += 1
        before_cover = before_lyrics = False
        duration = row.duration_seconds
        try:
            from mediafile import MediaFile

            audio = MediaFile(path)
            before_cover = bool(audio.images)
            before_lyrics = bool(audio.lyrics and str(audio.lyrics).strip())
            if not duration and audio.length:
                duration = max(1, round(float(audio.length)))
        except Exception:
            logger.debug("Could not probe Wanted file %s", path, exc_info=True)
        before_lyrics = before_lyrics or path.with_suffix(".lrc").is_file()

        allow_embed = True
        checker = getattr(self._external, "inode_allows_asset_embedding", None)
        if callable(checker):
            allow_embed = bool(checker(path))
        if not allow_embed:
            result.sidecar_only += 1

        prefs = self._preferences.effective()
        try:
            codec = AudioCodec(prefs.audio_format)
        except ValueError:
            codec = AudioCodec.OPUS
        service = DownloadService(
            DownloadConfig(
                base_path=WANTED_ROOT,
                codec=codec,
                quality=prefs.audio_quality,
                fetch_lyrics=prefs.fetch_lyrics,
                # A Wanted item has no trustworthy YTM ID yet. LRCLIB and QQ
                # can still resolve by verified tags + actual duration.
                ytmusic_lyrics_fallback=False,
                qq_lyrics_fallback=prefs.qq_lyrics_fallback,
                scrape_cooldown_hours=prefs.scrape_cooldown_hours,
                cover_excellence_px=int(getattr(prefs, "cover_excellence_px", 0) or 0),
                cover_probe_fresh_days=int(
                    getattr(prefs, "cover_probe_fresh_days", 7) or 7
                ),
                cover_download_fresh_days=int(
                    getattr(prefs, "cover_download_fresh_days", 30) or 30
                ),
            ),
            cookies_path=self._cookies_path,
            ytmusic_client=None,
        )
        metadata = TrackMetadata(
            # Stable local key gives provider misses a real cooldown without
            # pretending that the MusicBrainz/source ID is a YTM video ID.
            source_video_id=f"wanted-{row.id.hex[:25]}",
            title=row.title,
            artists=[row.artists],
            album=row.album or row.title,
            album_artists=[row.artists],
            cover_url=row.thumbnail_url,
            duration_seconds=duration,
            match_result=MatchResult.MATCHED,
        )
        outcome = service.enrich_file(
            path,
            metadata,
            rewrite_metadata=False,
            embed_assets=allow_embed,
        )
        after_lyrics = outcome.has_lyrics_embedded or outcome.has_lyrics_sidecar
        if outcome.has_embedded_cover and not before_cover:
            result.covers_written += 1
        if after_lyrics and not before_lyrics:
            result.lyrics_written += 1
        if outcome.error:
            result.failed += 1
        else:
            result.completed += 1
        return result

    # -- YTM match → Direct ---------------------------------------------

    def match_ytm_one(self, track_id: UUID) -> dict:
        row = self._repo.get(track_id)
        if row is None:
            raise FileNotFoundError("wanted track not found")
        try:
            video_id = self._search_ytm_video_id(row)
        except WantedSearchUnavailable:
            # A transport/provider failure is not evidence that the local
            # heart has no YTM match. Keep its retry budget untouched.
            return {"matched": False, "video_id": None, "unavailable": True}
        if not video_id:
            row.match_fail_count = int(row.match_fail_count or 0) + 1
            row.match_next_eligible_at = datetime.now(UTC) + timedelta(
                days=min(7, row.match_fail_count)
            )
            self._repo.save(row)
            return {"matched": False, "video_id": None}
        return self._fulfill_to_direct(row, video_id)

    def match_ytm_batch(self, *, limit: int = 25, force: bool = False) -> dict:
        prefs = self._preferences.effective()
        if not prefs.wanted_enabled:
            return {"matched": 0, "failed": 0}
        # Scheduled pass respects auto-match; manual Sync always runs.
        if not force and not prefs.wanted_auto_match_enabled:
            return {"matched": 0, "failed": 0}
        matched = failed = unavailable = 0
        for row in self._repo.list_matchable(limit=limit):
            try:
                result = self.match_ytm_one(row.id)
                if result.get("matched"):
                    matched += 1
                elif result.get("unavailable"):
                    unavailable += 1
                else:
                    failed += 1
            except Exception as exc:
                logger.warning("Wanted YTM match failed %s: %s", row.id, exc)
                failed += 1
        return {"matched": matched, "failed": failed, "unavailable": unavailable}

    def ingest_from_id_invalid(
        self,
        *,
        title: str,
        artists: str,
        album: str = "",
        source_path: Path | None = None,
        thumbnail_url: str | None = None,
    ) -> WantedTrackResponse:
        """Create/update a wishlist entry from an ID-invalid cleanup."""
        body = WantedAddRequest(
            title=title,
            artists=artists,
            album=album or "",
            source="id_invalid",
            thumbnail_url=thumbnail_url,
        )
        # Force-enable path: add() checks wanted_enabled — callers should gate.
        resp = self.add(body)
        if source_path is not None and source_path.is_file():
            row = self._repo.get(UUID(resp.id))
            if row is not None and not row.relative_path:
                ensure_wanted_layout()
                dest = build_track_path(
                    WANTED_ROOT,
                    artists.split("&")[0].strip() or artists,
                    None,
                    album or "Unknown Album",
                    None,
                    title,
                ).with_suffix(source_path.suffix)
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists():
                    try:
                        os.link(source_path, dest)
                    except OSError:
                        shutil.copy2(source_path, dest)
                row.relative_path = str(dest.relative_to(WANTED_ROOT)).replace(
                    "\\", "/"
                )
                row = self._repo.save(row)
                return self._to_response(row, display_index="W?")
        return resp

    # -- internals ------------------------------------------------------

    def _fulfill_to_direct(self, row: WantedTrack, video_id: str) -> dict:
        """Promote a matched heart to YTM Like, otherwise fulfill to Direct."""
        from yubal_api.services.sync_ledger_service import SyncLedgerService

        # Every legacy Wanted source now means a local heart.  Keep this
        # explicit escape hatch only for a future non-heart acquisition flow.
        if row.source != "direct_fulfill":
            self._client.rate_song(video_id, liked=True)
            row.video_id = video_id
            row.match_fail_count = 0
            row.match_next_eligible_at = None
            self._repo.save(row)
            return {
                "matched": True,
                "video_id": video_id,
                "awaiting_liked_sync": True,
            }

        direct_folder = self._preferences.effective().direct_folder or DIRECT_FOLDER
        if row.relative_path and (WANTED_ROOT / row.relative_path).is_file():
            src = WANTED_ROOT / row.relative_path
            allow_embed = True
            checker = getattr(self._external, "inode_allows_asset_embedding", None)
            if callable(checker):
                allow_embed = bool(checker(src))
            dest = build_track_path(
                DOWNLOAD_ROOT / direct_folder,
                row.artists.split("&")[0].strip() or row.artists,
                None,
                row.album or "Unknown Album",
                None,
                row.title,
            ).with_suffix(src.suffix)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                try:
                    os.link(src, dest)
                except OSError:
                    shutil.copy2(src, dest)
            src_lrc = src.with_suffix(".lrc")
            dest_lrc = dest.with_suffix(".lrc")
            if src_lrc.is_file() and not dest_lrc.exists():
                try:
                    os.link(src_lrc, dest_lrc)
                except OSError:
                    shutil.copy2(src_lrc, dest_lrc)
            recorder = getattr(self._sync_ledger, "record_existing_track", None)
            if callable(recorder):
                recorder(
                    video_id=video_id,
                    title=row.title,
                    artist=row.artists,
                    album=row.album or row.title,
                    cover_url=row.thumbnail_url,
                    save_folder=direct_folder,
                    absolute_path=dest,
                    origin="wanted_fulfill",
                    immutable=not allow_embed,
                )
            # Drop wanted link (may be sole or shared)
            if not self._unlink_wanted_file(src):
                raise OSError(f"could not retire wanted source {src}")
            try:
                if src_lrc.is_file():
                    src_lrc.unlink(missing_ok=True)
            except OSError:
                pass
            if self._sync_ledger is not None and isinstance(
                self._sync_ledger, SyncLedgerService
            ):
                try:
                    self._sync_ledger.reconcile_direct()
                except Exception as exc:
                    logger.info("reconcile_direct after wanted fulfill: %s", exc)
        else:
            # Tag-only: queue a Direct download for the matched YTM id.
            url = f"https://music.youtube.com/watch?v={video_id}"
            if self._job_executor is not None:
                try:
                    self._job_executor.create_and_start_job(url, max_items=1)
                except Exception as exc:
                    logger.warning(
                        "Wanted fulfill enqueue failed for %s: %s", video_id, exc
                    )
                    raise
            else:
                logger.info(
                    "Wanted fulfill %s → Direct download needed for %s (no executor)",
                    row.id,
                    video_id,
                )
        self._repo.delete(row.id)
        self._notify_media_changed()
        return {"matched": True, "video_id": video_id}

    def _search_ytm_video_id(self, row: WantedTrack) -> str | None:
        query = f"{row.artists} {row.title}"
        if row.album:
            query = f"{query} {row.album}"
        try:
            results = self._client.search_songs(query)[:8]
        except Exception as exc:
            logger.warning("YTM search for wanted failed: %s", exc)
            raise WantedSearchUnavailable(query) from exc
        best_id = None
        best_score = -1.0
        target_artists = {
            part.strip()
            for part in re.split(r"\s*/\s*|\s*&\s*|\s*;\s*", row.artists)
            if part.strip()
        }
        for result in results:
            candidate_artists = {a.name for a in result.artists if a.name}
            title_match = match_title(row.title, result.title)
            title_score = max(
                title_match.similarity,
                title_match.base_similarity,
            )
            artist_score = match_artists(
                target_artists,
                candidate_artists,
            ).best_score
            if title_score < _MATCH_TITLE_THRESHOLD:
                continue
            if artist_score < _MATCH_ARTIST_THRESHOLD:
                continue
            if has_version_marker(result.title) and not has_version_marker(row.title):
                continue
            score = title_score * 0.7 + artist_score * 0.3
            if score > best_score:
                best_score = score
                best_id = result.video_id
        return best_id

    def _find_strict_local(
        self,
        *,
        title_norm: str,
        artist_norm: str,
        album_norm: str,
        catalog_index: dict[tuple[str, str, str], list[Path]] | None = None,
    ) -> Path | None:
        """Resolve exact normalized metadata from DB indexes, never a full disk scan."""
        if not title_norm or not artist_norm:
            return None
        index = catalog_index
        if index is None:
            index = self._build_local_catalog_index()
        for path in index.get((title_norm, artist_norm, album_norm), []):
            if path.is_file():
                return path
        finder = getattr(self._external, "find_strict_raw_path", None)
        if callable(finder):
            return finder(
                title_norm=title_norm,
                artist_norm=artist_norm,
                album_norm=album_norm,
            )
        return None

    def _build_local_catalog_index(
        self,
    ) -> dict[tuple[str, str, str], list[Path]]:
        """Build one in-memory metadata map for an entire Wanted batch."""
        index: dict[tuple[str, str, str], list[Path]] = {}
        if self._catalog is None:
            return index
        for rows in self._catalog.list_all_by_video_id().values():
            record = rows[0][1]
            key = (
                normalize_music_text(record.title),
                normalize_artist_key(record.artist or record.album_artist),
                normalize_music_text(record.album or ""),
            )
            if not key[0] or not key[1]:
                continue
            paths = index.setdefault(key, [])
            for location, _ in rows:
                root = STORAGE_ROOTS.get(location.storage_root or STORAGE_DOWNLOAD)
                if root is not None:
                    paths.append(root / location.save_folder / location.relative_path)
        return index

    @staticmethod
    def _read_tags(path: Path) -> tuple[str, str, str] | None:
        try:
            from mutagen import File as MutagenFile  # type: ignore
        except ImportError:
            return None
        try:
            audio = MutagenFile(path, easy=True)
            if audio is None:
                return None
            title = (audio.get("title") or [""])[0]
            artists = (audio.get("artist") or [""])[0]
            album = (audio.get("album") or [""])[0]
            return (
                normalize_music_text(str(title)),
                normalize_artist_key(str(artists)),
                normalize_music_text(str(album)),
            )
        except Exception:
            return None

    def _move_to_raw_delete(self, path: Path, *, row: WantedTrack) -> None:
        """Archive a wanted file through the provenance-aware external ingress."""
        if not path.is_file():
            return
        if self._external is None:
            raise RuntimeError("external archive service is unavailable")
        dest = self._external.ingest_file_to_raw_delete(  # type: ignore[attr-defined]
            path,
            origin_kind="wanted",
            origin_ref=str(row.id),
            title=row.title,
            artists=row.artists,
            album=row.album or "",
        )
        if dest is None:
            raise RuntimeError("wanted file could not be moved to recycle center")

    @staticmethod
    def _to_response(
        row: WantedTrack, *, display_index: str | None
    ) -> WantedTrackResponse:
        has_file = (
            bool(row.relative_path) and (WANTED_ROOT / row.relative_path).is_file()
        )
        return WantedTrackResponse(
            id=str(row.id),
            display_index=display_index if has_file else None,
            title=row.title,
            artists=row.artists,
            album=row.album or None,
            source=row.source,
            source_id=row.source_id or None,
            source_url=row.source_url,
            thumbnail_url=row.thumbnail_url,
            duration_seconds=row.duration_seconds,
            relative_path=row.relative_path if has_file else None,
            has_file=has_file,
            video_id=row.video_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
