"""Library enrichment pass: re-scrape non-premium tracks to upgrade quality.

Walks the track catalog, computes each track's derived tier, and re-runs the
normal cover/lyrics/tagging helpers on every track that is not already
``premium`` (最优). Premium tracks are skipped while their cover comparison
is still fresh (or permanently sealed by the excellence threshold). When the
shelf life expires the tier falls back to ``complete`` and the track is
eligible again. Draft (半成品) tracks are processed first, then complete
(成品) tracks, ordered by the oldest enrichment attempt so each pass rotates
through the backlog.

Remote source throttling for lyrics misses still uses scrape_cooldown_hours.
Cover re-checks use premium shelf life (probe 7d / download 30d).
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel
from yubal import AudioCodec, DownloadConfig, MatchResult, TrackMetadata
from yubal.client import YTMusicClient
from yubal.services.download_service import DownloadService

from yubal_api.db.track_catalog import TrackLocation, TrackRecord
from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.domain.track_quality import (
    TIER_DRAFT,
    TIER_PREMIUM,
    compute_track_tier,
    lyrics_are_synced,
)
from yubal_api.services.preferences import Preferences, PreferencesStore

logger = logging.getLogger(__name__)


class EnrichmentSummary(BaseModel):
    """Outcome of a single enrichment pass."""

    scanned: int = 0
    enriched: int = 0
    upgraded: int = 0
    failed: int = 0
    skipped_premium: int = 0
    skipped_immutable: int = 0
    already_running: bool = False


def _has_lyrics(record: TrackRecord) -> bool:
    return bool(
        record.has_lyrics_embedded
        or record.has_lyrics_sidecar
        or (record.lyrics and record.lyrics.strip())
    )


def _has_synced_lyrics(record: TrackRecord, path: Path | None = None) -> bool:
    if lyrics_are_synced(record.lyrics):
        return True
    if path is None:
        return False
    try:
        lrc = path.with_suffix(".lrc")
        if lrc.is_file():
            return lyrics_are_synced(
                lrc.read_text(encoding="utf-8", errors="ignore")
            )
    except OSError:
        pass
    return False


class LibraryEnrichmentService:
    """Re-scrape and upgrade library tracks that are below the premium tier."""

    def __init__(
        self,
        *,
        catalog: TrackCatalogRepository,
        data_path: Path,
        preferences: PreferencesStore,
        cookies_path: Path | None,
    ) -> None:
        self._catalog = catalog
        self._data_path = data_path
        self._preferences = preferences
        self._cookies_path = cookies_path
        self._client: YTMusicClient | None = None
        self._client_tried = False
        self._lock = threading.Lock()

    def enrich_library(
        self,
        *,
        budget: int | None = 50,
        reason: str = "manual",
        save_folder: str | None = None,
        force: bool = False,
    ) -> EnrichmentSummary:
        """Run one enrichment pass. Single-flight: concurrent calls are skipped.

        ``save_folder`` limits to catalog locations under that folder.
        ``force`` skips scrape/cover freshness cooldowns (single-track / explicit).
        """
        if not self._lock.acquire(blocking=False):
            logger.info("Library enrichment already running; skipped (%s)", reason)
            return EnrichmentSummary(already_running=True)
        try:
            return self._run(
                budget=budget,
                reason=reason,
                save_folder=save_folder,
                force=force,
            )
        finally:
            self._lock.release()

    def enrich_track(
        self, video_id: str, *, force: bool = True
    ) -> EnrichmentSummary:
        """Fill or upgrade one non-premium track by video_id.

        Single-track UI actions pass ``force=True`` (scrape/cover cooldown exempt).
        """
        if not video_id:
            return EnrichmentSummary()
        if not self._lock.acquire(blocking=False):
            logger.info(
                "Library enrichment already running; skipped single %s", video_id
            )
            return EnrichmentSummary(already_running=True)
        try:
            return self._run_one(video_id, force=force)
        finally:
            self._lock.release()

    def _run(
        self,
        *,
        budget: int | None,
        reason: str,
        save_folder: str | None = None,
        force: bool = False,
    ) -> EnrichmentSummary:
        summary = EnrichmentSummary()
        grouped = self._catalog.list_all_by_video_id()
        if not grouped:
            return summary

        folder = (save_folder or "").strip().replace("\\", "/").rstrip("/")
        candidates: list[tuple[TrackRecord, Path, str, bool]] = []
        from yubal.services.scrape_state import ScrapeStateStore

        scrape_store = ScrapeStateStore(self._data_path)
        for rows in grouped.values():
            if folder:
                rows = [
                    (loc, rec)
                    for loc, rec in rows
                    if (loc.save_folder or "").strip().replace("\\", "/").rstrip("/")
                    == folder
                ]
                if not rows:
                    continue
            record = rows[0][1]
            path = self._first_existing_path(rows)
            if path is None:
                continue
            tier = compute_track_tier(
                title=record.title,
                artist=record.artist,
                has_embedded_cover=record.has_embedded_cover,
                has_lyrics=_has_lyrics(record),
                cover_source=record.cover_source,
                has_synced_lyrics=_has_synced_lyrics(record, path),
                **self._cover_tier_kwargs(
                    record.video_id, store=scrape_store, force=force
                ),
            )
            if tier == TIER_PREMIUM and not force:
                summary.skipped_premium += 1
                continue
            # Readonly External: asset-only enrich (no tag rewrite).
            candidates.append((record, path, tier, not record.immutable))

        # Draft first, then oldest enrichment attempt (never-tried first).
        candidates.sort(
            key=lambda c: (
                0 if c[2] == TIER_DRAFT else 1,
                c[0].last_enriched_at.timestamp() if c[0].last_enriched_at else 0.0,
            )
        )
        if budget is not None:
            candidates = candidates[:budget]
        if not candidates:
            return summary

        service = self._build_download_service(force=force)
        for record, path, tier, rewrite_metadata in candidates:
            self._enrich_one(
                service,
                record,
                path,
                tier,
                summary,
                rewrite_metadata=rewrite_metadata,
            )

        logger.info(
            "Library enrichment (%s): scanned=%d enriched=%d upgraded=%d "
            "failed=%d skipped_premium=%d skipped_immutable=%d",
            reason,
            summary.scanned,
            summary.enriched,
            summary.upgraded,
            summary.failed,
            summary.skipped_premium,
            summary.skipped_immutable,
        )
        return summary

    def _run_one(self, video_id: str, *, force: bool = False) -> EnrichmentSummary:
        summary = EnrichmentSummary()
        record = self._catalog.get_track(video_id)
        if record is None:
            return summary
        locs = self._catalog.list_locations_for_video(video_id)
        if not locs:
            return summary
        path = self._first_existing_path([(loc, record) for loc in locs])
        if path is None:
            return summary
        # Immutable (readonly External): still allow lyrics/cover asset upgrades
        # without rewriting title/artist/album tags.
        rewrite_metadata = not record.immutable
        tier = compute_track_tier(
            title=record.title,
            artist=record.artist,
            has_embedded_cover=record.has_embedded_cover,
            has_lyrics=_has_lyrics(record),
            cover_source=record.cover_source,
            has_synced_lyrics=_has_synced_lyrics(record, path),
            **self._cover_tier_kwargs(record.video_id, force=force),
        )
        if tier == TIER_PREMIUM and not force:
            summary.skipped_premium = 1
            return summary
        service = self._build_download_service(force=force)
        self._enrich_one(
            service, record, path, tier, summary, rewrite_metadata=rewrite_metadata
        )
        logger.info(
            "Single-track enrichment %s: enriched=%d upgraded=%d failed=%d force=%s",
            video_id,
            summary.enriched,
            summary.upgraded,
            summary.failed,
            force,
        )
        return summary

    def _enrich_one(
        self,
        service: DownloadService,
        record: TrackRecord,
        path: Path,
        tier: str,
        summary: EnrichmentSummary,
        *,
        rewrite_metadata: bool = False,
    ) -> None:
        summary.scanned += 1
        outcome = service.enrich_file(
            path,
            self._build_metadata(record, path),
            rewrite_metadata=rewrite_metadata,
        )
        lyrics_text = outcome.lyrics or record.lyrics
        new_tier = compute_track_tier(
            title=record.title,
            artist=record.artist,
            has_embedded_cover=outcome.has_embedded_cover,
            has_lyrics=outcome.has_lyrics_embedded or outcome.has_lyrics_sidecar,
            cover_source=outcome.cover_source,
            has_synced_lyrics=lyrics_are_synced(lyrics_text)
            or _has_synced_lyrics(record, path),
            **self._cover_tier_kwargs(record.video_id),
        )
        try:
            self._catalog.update_asset_state(
                video_id=record.video_id,
                has_embedded_cover=outcome.has_embedded_cover,
                has_lyrics_embedded=outcome.has_lyrics_embedded,
                has_lyrics_sidecar=outcome.has_lyrics_sidecar,
                lyrics=outcome.lyrics,
                cover_source=outcome.cover_source,
                lyrics_source=outcome.lyrics_source,
                last_enriched_at=datetime.now(UTC),
                last_enrich_error=outcome.error,
            )
        except Exception:
            logger.exception(
                "Failed to persist enrichment for %s", record.video_id
            )
        if outcome.error:
            summary.failed += 1
        else:
            summary.enriched += 1
            if new_tier != tier:
                summary.upgraded += 1

    def _first_existing_path(
        self, rows: list[tuple[TrackLocation, TrackRecord]]
    ) -> Path | None:
        from yubal.utils.library import (
            STORAGE_DOWNLOAD,
            STORAGE_ROOTS,
            resolve_storage_path,
        )

        for loc, _record in rows:
            storage = loc.storage_root or STORAGE_DOWNLOAD
            try:
                if storage in STORAGE_ROOTS:
                    candidate = resolve_storage_path(
                        storage, f"{loc.save_folder}/{loc.relative_path}"
                    )
                else:
                    candidate = (
                        self._data_path / loc.save_folder / loc.relative_path
                    )
            except ValueError:
                continue
            if candidate.is_file():
                return candidate
        return None

    def _build_metadata(
        self, record: TrackRecord, path: Path | None = None
    ) -> TrackMetadata:
        artist = record.artist or "Unknown Artist"
        album_artist = record.album_artist or artist
        duration: int | None = None
        if path is not None and path.is_file():
            try:
                from mediafile import MediaFile

                length = MediaFile(path).length
                if length is not None and float(length) > 0:
                    duration = max(1, int(round(float(length))))
            except Exception:
                logger.debug(
                    "Could not read duration for enrich %s", path, exc_info=True
                )
        return TrackMetadata(
            source_video_id=record.video_id,
            title=record.title or "Unknown Track",
            artists=[artist],
            album=record.album or record.title or "Unknown Album",
            album_artists=[album_artist],
            track_number=record.track_number,
            year=record.year,
            cover_url=record.cover_url,
            duration_seconds=duration,
            match_result=MatchResult.MATCHED,
        )

    def _cover_tier_kwargs(
        self,
        video_id: str,
        *,
        store: object | None = None,
        force: bool = False,
    ) -> dict:
        prefs = self._preferences.effective()
        excellence = int(getattr(prefs, "cover_excellence_px", 0) or 0)
        # force: treat cover shelf as expired so premium/complete re-checks run.
        probe_days = 0 if force else int(getattr(prefs, "cover_probe_fresh_days", 7) or 7)
        download_days = (
            0
            if force
            else int(getattr(prefs, "cover_download_fresh_days", 30) or 30)
        )
        if not video_id:
            return {
                "cover_excellence_px": excellence,
                "cover_probe_fresh_days": probe_days,
                "cover_download_fresh_days": download_days,
            }
        from yubal.services.scrape_state import ScrapeStateStore

        scrape_store = store or ScrapeStateStore(self._data_path)
        state = scrape_store.get(video_id)  # type: ignore[union-attr]
        return {
            "cover_compared_at": None if force else state.effective_compared_at(),
            "cover_check_kind": state.effective_check_kind(),
            "cover_width": state.cover_width,
            "cover_height": state.cover_height,
            "cover_excellence_px": excellence,
            "cover_probe_fresh_days": probe_days,
            "cover_download_fresh_days": download_days,
        }

    def _codec(self, prefs: Preferences) -> AudioCodec:
        try:
            return AudioCodec(prefs.audio_format)
        except ValueError:
            return AudioCodec.OPUS

    def _ytmusic_client(self, prefs: Preferences) -> YTMusicClient | None:
        if not prefs.ytmusic_lyrics_fallback:
            return None
        if self._client is None and not self._client_tried:
            self._client_tried = True
            try:
                from yubal import APIConfig

                self._client = YTMusicClient(
                    config=APIConfig(search_limit=10),
                    cookies_path=self._cookies_path,
                )
            except Exception:
                logger.exception("Could not init YTMusic client for enrichment")
                self._client = None
        return self._client

    def _build_download_service(self, *, force: bool = False) -> DownloadService:
        prefs = self._preferences.effective()
        config = DownloadConfig(
            base_path=self._data_path,
            codec=self._codec(prefs),
            quality=prefs.audio_quality,
            fetch_lyrics=prefs.fetch_lyrics,
            ytmusic_lyrics_fallback=prefs.ytmusic_lyrics_fallback,
            qq_lyrics_fallback=prefs.qq_lyrics_fallback,
            scrape_cooldown_hours=0 if force else prefs.scrape_cooldown_hours,
            cover_excellence_px=int(getattr(prefs, "cover_excellence_px", 0) or 0),
            cover_probe_fresh_days=(
                0
                if force
                else int(getattr(prefs, "cover_probe_fresh_days", 7) or 7)
            ),
            cover_download_fresh_days=(
                0
                if force
                else int(getattr(prefs, "cover_download_fresh_days", 30) or 30)
            ),
            library_folder=prefs.direct_folder,
        )
        return DownloadService(
            config,
            cookies_path=self._cookies_path,
            ytmusic_client=self._ytmusic_client(prefs),
        )
