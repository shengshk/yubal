"""Direct playlist auto-recover: restore missing files from the Direct list."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from yubal import (
    AudioCodec,
    CancelToken,
    DownloadConfig,
    DownloadStatus,
    MatchResult,
    PhaseStats,
    SkipReason,
    TrackMetadata,
    create_downloader,
)
from yubal.models.enums import ContentKind, VideoType
from yubal.utils.library import resolve_under_data

from yubal_api.db.track_catalog import LocationMembershipStatus, TrackRecord
from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.domain.enums import ProgressStep
from yubal_api.domain.job import ContentInfo
from yubal_api.services.preferences import PreferencesStore
from yubal_api.services.sync_service import ProgressCallback, SyncResult

logger = logging.getLogger(__name__)

DIRECT_RECOVER_URL = "yubal://direct-recover"

_UNAVAILABLE_MARKERS = (
    "video unavailable",
    "unavailable",
    "private video",
    "this video has been removed",
    "not available",
)


def is_unavailable_error(message: str | None) -> bool:
    if not message:
        return False
    lowered = message.lower()
    return any(marker in lowered for marker in _UNAVAILABLE_MARKERS)


class DirectRecoverService:
    """Restore missing Direct-list audio (hardlink or re-download)."""

    def __init__(
        self,
        *,
        data_path: Path,
        track_catalog: TrackCatalogRepository,
        preferences_store: PreferencesStore,
        cookies_path: Path | None = None,
        folder_presence: object | None = None,
        ascii_filenames: bool = False,
        cache_path: Path | None = None,
        immediate_cleanup: Callable[[], int] | None = None,
    ) -> None:
        self._data_path = data_path
        self._catalog = track_catalog
        self._preferences_store = preferences_store
        self._cookies_path = cookies_path
        self._folder_presence = folder_presence
        self._ascii_filenames = ascii_filenames
        self._cache_path = cache_path
        self._immediate_cleanup = immediate_cleanup

    def run(
        self,
        *,
        on_progress: ProgressCallback | None,
        cancel_token: CancelToken,
        max_items: int | None = None,
        audio_format: str = "opus",
        audio_quality: int = 0,
        fetch_lyrics: bool = True,
        ytmusic_lyrics_fallback: bool = True,
        qq_lyrics_fallback: bool = True,
        apply_replaygain: bool = False,
        download_ugc: bool = False,
        scrape_cooldown_hours: int = 24,
        download_cache_path: Path | None = None,
        cache_min_free_gb: float = 2.0,
        data_min_free_gb: float = 2.0,
    ) -> SyncResult:
        prefs = self._preferences_store.effective()
        folder = prefs.direct_folder
        budget = max_items if max_items is not None else prefs.direct_max_items
        budget = max(1, int(budget))
        mark_offline = prefs.direct_offline_marking_enabled
        marked_offline = 0

        rows = self._catalog.list_for_save_folder(folder, order_by_recent=True)
        root = resolve_under_data(self._data_path, folder)

        missing: list[TrackMetadata] = []
        for loc, rec in rows:
            status = (
                getattr(loc, "membership_status", None)
                or LocationMembershipStatus.ACTIVE
            )
            if status in (
                LocationMembershipStatus.OFFLINE,
                LocationMembershipStatus.BLOCKED,
            ):
                continue
            abs_path = (root / loc.relative_path).resolve()
            try:
                abs_path.relative_to(root.resolve())
            except ValueError:
                exists = False
            else:
                exists = abs_path.is_file()
            if exists:
                continue
            missing.append(self._to_metadata(rec))

        title = folder.split("/")[-1] or folder
        content_info = ContentInfo(
            title=title,
            artist="",
            track_count=len(missing),
            url=DIRECT_RECOVER_URL,
            kind=ContentKind.PLAYLIST,
        )

        if on_progress:
            on_progress(
                ProgressStep.FETCHING_INFO,
                f"Download Center recovery: {len(missing)} missing",
                2.0,
                {"content_info": content_info.model_dump()},
            )

        if not missing:
            if on_progress:
                on_progress(ProgressStep.COMPLETED, "Nothing to recover", 100.0, None)
            return SyncResult(
                success=True,
                content_info=content_info,
                download_stats=PhaseStats(),
                download_results=[],
                remote_tracks=[],
                destination=str(root),
            )

        selected = missing[:budget]
        content_info.track_count = len(selected)
        codec = AudioCodec(audio_format)
        config = DownloadConfig(
            base_path=self._data_path,
            codec=codec,
            quality=audio_quality,
            quiet=True,
            fetch_lyrics=fetch_lyrics,
            ytmusic_lyrics_fallback=ytmusic_lyrics_fallback,
            qq_lyrics_fallback=qq_lyrics_fallback,
            scrape_cooldown_hours=scrape_cooldown_hours,
            ascii_filenames=self._ascii_filenames,
            download_ugc=download_ugc,
            library_folder=folder,
            download_cache_path=download_cache_path,
            cache_min_free_gb=cache_min_free_gb,
            data_min_free_gb=data_min_free_gb,
        )
        downloader = create_downloader(config, cookies_path=self._cookies_path)
        downloader.set_library_folder(folder)
        if self._folder_presence is not None:
            downloader.set_folder_presence(self._folder_presence)

        results = []
        total = len(selected)
        skipped_by_reason: dict[SkipReason, int] = {}
        for progress in downloader.download_tracks(selected, cancel_token):
            result = progress.result
            results.append(result)
            video_id = result.video_id_used or result.track.video_id
            if result.status in (
                DownloadStatus.SUCCESS,
                DownloadStatus.HARDLINKED,
                DownloadStatus.PRESELECTED,
                DownloadStatus.SKIPPED,
            ):
                if video_id:
                    self._catalog.set_membership_status(
                        folder,
                        video_id,
                        LocationMembershipStatus.ACTIVE,
                    )
                if (
                    result.status == DownloadStatus.SKIPPED
                    and result.skip_reason is not None
                ):
                    skipped_by_reason[result.skip_reason] = (
                        skipped_by_reason.get(result.skip_reason, 0) + 1
                    )
            elif (
                mark_offline
                and result.status == DownloadStatus.FAILED
                and video_id
                and is_unavailable_error(result.error)
            ):
                self._catalog.set_membership_status(
                    folder,
                    video_id,
                    LocationMembershipStatus.OFFLINE,
                )
                marked_offline += 1
                logger.info(
                    "Marked Download Center track %s offline after "
                    "unavailable download",
                    video_id,
                )

            if on_progress:
                pct = 10.0 + (progress.current / max(total, 1)) * 80.0
                label = result.track.title if result.track else video_id
                on_progress(
                    ProgressStep.DOWNLOADING,
                    f"[{progress.current}/{total}] {label}",
                    pct,
                    None,
                )

        success_n = sum(1 for r in results if r.status == DownloadStatus.SUCCESS)
        hard_n = sum(
            1
            for r in results
            if r.status
            in (DownloadStatus.HARDLINKED, DownloadStatus.PRESELECTED)
        )
        failed_n = sum(1 for r in results if r.status == DownloadStatus.FAILED)
        stats = PhaseStats(
            success=success_n,
            hardlinked=hard_n,
            failed=failed_n,
            skipped_by_reason=skipped_by_reason,
        )

        if on_progress:
            on_progress(
                ProgressStep.COMPLETED,
                f"Recovered {success_n + hard_n}/{total}",
                100.0,
                None,
            )

        # delay=0: clean newly marked ID-invalid rows immediately (aligned
        # with subscription offline cleanup). Non-zero delays wait for scheduler.
        if (
            marked_offline
            and prefs.direct_offline_cleanup_enabled
            and int(prefs.direct_offline_cleanup_delay_hours) == 0
            and self._immediate_cleanup is not None
        ):
            try:
                cleaned = self._immediate_cleanup()
                if cleaned:
                    logger.info(
                        "Immediate Download Center ID-invalid cleanup "
                        "removed %d track(s)",
                        cleaned,
                    )
            except Exception:
                logger.exception(
                    "Immediate Download Center ID-invalid cleanup failed"
                )

        return SyncResult(
            success=True,
            content_info=content_info,
            download_stats=stats,
            download_results=results,
            remote_tracks=selected,
            destination=str(root),
        )

    @staticmethod
    def _to_metadata(rec: TrackRecord) -> TrackMetadata:
        artist = (rec.artist or "").strip() or "Unknown Artist"
        album_artist = (rec.album_artist or "").strip() or artist
        album = (rec.album or "").strip() or "Unknown Album"
        title = (rec.title or "").strip() or rec.video_id
        return TrackMetadata(
            source_video_id=rec.video_id,
            title=title,
            artists=[artist],
            album=album,
            album_artists=[album_artist],
            track_number=rec.track_number,
            year=rec.year,
            cover_url=rec.cover_url,
            video_type=VideoType.ATV,
            match_result=MatchResult.MATCHED,
        )
