"""Job execution orchestration service."""

import asyncio
import logging
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any
from uuid import UUID

from yubal import AudioCodec, CancelToken, cleanup_part_files
from yubal.utils.library import resolve_under_data, sanitize_save_folder

from yubal_api.api.exceptions import SubscriptionNotFoundError
from yubal_api.db.subscription_membership import SnapshotStatus
from yubal_api.domain.enums import JobSource, JobStatus, ProgressStep
from yubal_api.domain.job import ContentInfo, Job
from yubal_api.services.direct_recover_service import (
    DIRECT_RECOVER_URL,
    DirectRecoverService,
)
from yubal_api.services.operation_gate import OperationGate
from yubal_api.services.preferences import DOWNLOAD_CACHE_ROOT, PreferencesStore
from yubal_api.services.protocols import JobExecutionStore
from yubal_api.services.subscription_membership_service import (
    SubscriptionMembershipService,
)
from yubal_api.services.subscription_service import SubscriptionService
from yubal_api.services.sync_ledger_service import SyncLedgerService
from yubal_api.services.sync_service import SyncResult, SyncService

logger = logging.getLogger(__name__)

PROGRESS_COMPLETE = 100.0


class JobExecutor:
    """Orchestrates job execution lifecycle.

    This executor manages background job execution with proper cleanup and
    cancellation support. Jobs run in a thread pool to avoid blocking the
    async event loop during I/O-heavy operations (yt-dlp downloads).

    Key Responsibilities:
        - Background task lifecycle (creation, tracking, cleanup)
        - Cancellation via CancelToken registry
        - Timeout enforcement via asyncio.timeout
        - Job queue continuation (starts next pending job when one completes)
        - Progress callback wiring to update job store

    Architecture Notes:
        - Uses JobExecutionStore protocol for persistence (ISP compliance)
        - CancelToken is the single source of truth for cancellation
        - Tasks are tracked in a set to prevent garbage collection
    """

    def __init__(
        self,
        job_store: JobExecutionStore,
        base_path: Path,
        audio_format: AudioCodec = AudioCodec.OPUS,
        audio_quality: int = 0,
        cookies_path: Path | None = None,
        fetch_lyrics: bool = True,
        ytmusic_lyrics_fallback: bool = True,
        qq_lyrics_fallback: bool = True,
        apply_replaygain: bool = False,
        ascii_filenames: bool = False,
        download_ugc: bool = False,
        subscription_service: SubscriptionService | None = None,
        membership_service: SubscriptionMembershipService | None = None,
        sync_ledger_service: SyncLedgerService | None = None,
        preferences_store: PreferencesStore | None = None,
        cache_path: Path | None = None,
        job_timeout: float = 1800,
        operation_gate: OperationGate | None = None,
        folder_presence: object | None = None,
    ) -> None:
        """Initialize the job executor.

        Args:
            job_store: Store for job persistence (protocol-based for testability).
            base_path: Base directory for downloaded files.
            audio_format: Target audio format (opus, mp3, m4a).
            audio_quality: Audio quality (0 = best, 10 = worst).
            cookies_path: Optional path to cookies.txt for authenticated requests.
            fetch_lyrics: Whether to fetch lyrics from lrclib.net.
            ytmusic_lyrics_fallback: Whether to fall back to YouTube Music lyrics
                when lrclib.net has no match.
            qq_lyrics_fallback: Whether to fall back to QQ Music lyrics with
                high-confidence matching only.
            apply_replaygain: Whether to apply ReplayGain tags using rsgain.
            ascii_filenames: Whether to transliterate unicode to ASCII in filenames.
            download_ugc: Whether to download UGC tracks to Unofficial folder.
            subscription_service: Optional service to update subscription metadata.
            membership_service: Optional trusted membership reconciler.
            sync_ledger_service: Optional durable sync-center ledger.
            preferences_store: Optional store for disk-space gate before jobs.
            cache_path: Optional directory for extraction cache.
            job_timeout: Maximum execution time per job in seconds.
            operation_gate: Optional exclusive-maintenance gate (migration).
        """
        self._job_store = job_store
        self._base_path = base_path
        self._audio_format = audio_format
        self._audio_quality = audio_quality
        self._cookies_path = cookies_path
        self._fetch_lyrics = fetch_lyrics
        self._ytmusic_lyrics_fallback = ytmusic_lyrics_fallback
        self._qq_lyrics_fallback = qq_lyrics_fallback
        self._apply_replaygain = apply_replaygain
        self._ascii_filenames = ascii_filenames
        self._download_ugc = download_ugc
        self._subscription_service = subscription_service
        self._membership_service = membership_service
        self._sync_ledger_service = sync_ledger_service
        self._preferences_store = preferences_store
        self._cache_path = cache_path
        self._job_timeout = job_timeout
        self._operation_gate = operation_gate
        self._folder_presence = folder_presence
        self._direct_recover: DirectRecoverService | None = None
        if (
            preferences_store is not None
            and sync_ledger_service is not None
            and getattr(sync_ledger_service, "_track_catalog", None) is not None
        ):
            self._direct_recover = DirectRecoverService(
                data_path=base_path,
                track_catalog=sync_ledger_service._track_catalog,
                preferences_store=preferences_store,
                cookies_path=cookies_path,
                folder_presence=folder_presence,
                ascii_filenames=ascii_filenames,
                cache_path=cache_path,
                immediate_cleanup=sync_ledger_service.run_id_invalid_cleanup,
            )

        # Track background tasks to prevent GC during execution
        self._background_tasks: set[asyncio.Task[Any]] = set()
        # Scheduler work runs in a worker thread, but asyncio tasks must be
        # created on the application's event loop.
        try:
            self._event_loop: asyncio.AbstractEventLoop | None = (
                asyncio.get_running_loop()
            )
        except RuntimeError:
            self._event_loop = None
        # Map job_id -> CancelToken for cancellation support
        self._cancel_tokens: dict[str, CancelToken] = {}

    def has_active_jobs(self) -> bool:
        """Return True while any download/sync job is still running."""
        return any(not job.status.is_finished for job in self._job_store.get_all())

    def _prefs(self) -> Any:
        """Effective download preferences (UI overrides env boot defaults)."""
        if self._preferences_store is not None:
            return self._preferences_store.snapshot()
        return None

    def _effective_audio_format(self) -> AudioCodec:
        prefs = self._prefs()
        if prefs is None:
            return self._audio_format
        return AudioCodec(prefs.audio_format)

    def _effective_audio_quality(self) -> int:
        prefs = self._prefs()
        return prefs.audio_quality if prefs is not None else self._audio_quality

    def _effective_fetch_lyrics(self) -> bool:
        prefs = self._prefs()
        return prefs.fetch_lyrics if prefs is not None else self._fetch_lyrics

    def _effective_ytmusic_lyrics_fallback(self) -> bool:
        prefs = self._prefs()
        return (
            prefs.ytmusic_lyrics_fallback
            if prefs is not None
            else self._ytmusic_lyrics_fallback
        )

    def _effective_qq_lyrics_fallback(self) -> bool:
        prefs = self._prefs()
        return (
            prefs.qq_lyrics_fallback if prefs is not None else self._qq_lyrics_fallback
        )

    def _effective_scrape_cooldown_hours(self) -> int:
        prefs = self._prefs()
        if prefs is not None:
            return max(0, int(prefs.scrape_cooldown_hours))
        return 24

    def _effective_replaygain(self) -> bool:
        prefs = self._prefs()
        return prefs.replaygain if prefs is not None else self._apply_replaygain

    def _effective_download_ugc(self) -> bool:
        prefs = self._prefs()
        return prefs.download_ugc if prefs is not None else self._download_ugc

    def _effective_direct_folder(self) -> str:
        prefs = self._prefs()
        if prefs is not None:
            return prefs.direct_folder
        return "direct"

    def _effective_job_timeout(self) -> float:
        prefs = self._prefs()
        if prefs is not None:
            return float(prefs.job_timeout_seconds)
        return float(self._job_timeout)

    def _effective_download_cache_path(self) -> Path | None:
        prefs = self._prefs()
        if prefs is not None and prefs.download_cache_enabled:
            return DOWNLOAD_CACHE_ROOT
        return None

    def create_and_start_job(
        self,
        url: str,
        max_items: int | None = None,
        source: JobSource = JobSource.MANUAL,
        subscription_id: UUID | None = None,
    ) -> Job | None:
        """Create a new job and start it if ready.

        This is the primary entry point for job creation. It handles:
        - Creating the job with proper audio format from settings
        - Starting the job if a slot is available

        Args:
            url: The URL to download content from.
            max_items: Maximum number of items to download (None for all).
            source: Source of the job (manual API call or scheduler).
            subscription_id: Optional subscription that triggered this job.

        Returns:
            The created Job, or None if queue is full.

        Raises:
            InsufficientDiskSpaceError: When free space is below the configured minimum.
            MigrationInProgressError: When library migration / maintenance is running.
            LibraryUnhealthyError: When the operation gate has a bound health
                service and Download/External mounts are unsafe.
        """
        if self._operation_gate is not None:
            self._operation_gate.ensure_allowed()

        if self._preferences_store is not None:
            self._preferences_store.ensure_enough_space()
            if self._preferences_store.effective().download_cache_enabled:
                self._preferences_store.ensure_cache_enough_space()

        result = self._job_store.create(
            url, self._effective_audio_format(), max_items, source, subscription_id
        )
        if result is None:
            return None

        job, should_start = result
        if should_start:
            self.start_job(job)

        return job

    def start_job(self, job: Job) -> None:
        """Start a job as a background task.

        The task is tracked to prevent garbage collection and will
        automatically trigger the next pending job when complete.

        Args:
            job: The job to start executing.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = self._event_loop
            if loop is None or loop.is_closed():
                raise RuntimeError("job executor event loop is not available") from None
            loop.call_soon_threadsafe(self._start_job_on_loop, job)
            return

        self._event_loop = loop
        self._start_job_on_loop(job)

    def _start_job_on_loop(self, job: Job) -> None:
        """Create and track a job task on the bound application event loop."""
        loop = asyncio.get_running_loop()
        task = loop.create_task(
            self._run_job(job.id, job.url, job.max_items, job.subscription_id),
            name=f"job-{job.id[:8]}",  # Helpful for debugging
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def cancel_job(self, job_id: str) -> bool:
        """Signal cancellation for a running job.

        This sets the cancel token which will be checked during download.
        The actual job status update happens in _run_job when it detects
        the cancellation.

        Args:
            job_id: ID of the job to cancel.

        Returns:
            True if a cancel token existed (job was running), False otherwise.
        """
        token = self._cancel_tokens.get(job_id)
        if token is None:
            return False

        token.cancel()
        logger.info("Job cancellation requested: %s", job_id[:8])
        return True

    def cancel_all_jobs(self) -> int:
        """Cancel all running jobs. Used during shutdown.

        Returns:
            Number of jobs that were signalled for cancellation.
        """
        jobs = self._job_store.get_all()
        for job in jobs:
            if not job.status.is_finished:
                self._job_store.cancel(job.id)
        tokens = list(self._cancel_tokens.values())
        for token in tokens:
            token.cancel()
        return len(tokens)

    def cancel_all_jobs_and_wait(self, timeout: float = 30.0) -> int:
        """Cancel queued/running jobs and wait for active cleanup from a worker."""
        cancelled = self.cancel_all_jobs()
        loop = self._event_loop
        tasks = [task for task in self._background_tasks if not task.done()]
        if loop is None or loop.is_closed() or not tasks:
            return cancelled
        try:
            if asyncio.get_running_loop() is loop:
                return cancelled
        except RuntimeError:
            pass

        async def wait_for_cleanup() -> int:
            _done, pending = await asyncio.wait(tasks, timeout=timeout)
            return len(pending)

        future = asyncio.run_coroutine_threadsafe(wait_for_cleanup(), loop)
        try:
            pending = future.result(timeout=timeout + 1)
        except FutureTimeoutError:
            pending = len(tasks)
        if pending:
            raise TimeoutError(f"{pending} job(s) did not stop before maintenance")
        return cancelled

    def _finalize_failed_ledger(self, job_id: str, library_folder: str | None) -> None:
        """Mark the ledger row failed after a timeout/exception.

        Prevents the row from being stuck at "running" (which the UI cannot
        interpret after a restart) when a job dies before the happy path
        records its outcome.
        """
        finished = self._job_store.get(job_id)
        if self._sync_ledger_service and finished is not None:
            try:
                self._sync_ledger_service.record_job_finished(
                    finished,
                    success=False,
                    save_folder=library_folder,
                )
            except Exception:
                logger.exception("Failed to finalize ledger for job %s", job_id[:8])

    async def _run_job(
        self,
        job_id: str,
        url: str,
        max_items: int | None = None,
        subscription_id: UUID | None = None,
    ) -> None:
        """Background task that runs the sync operation."""
        cancel_token = CancelToken()
        self._cancel_tokens[job_id] = cancel_token
        snapshot_id = None
        # Track the folder so timeout/exception paths can still finalize the
        # ledger (otherwise the row stays stuck at "running" after a crash).
        library_folder: str | None = None

        try:
            # Check cancellation before starting (CancelToken is single source of truth)
            if cancel_token.is_cancelled:
                return

            async with asyncio.timeout(self._effective_job_timeout()):
                self._job_store.transition(
                    job_id,
                    JobStatus.FETCHING_INFO,
                    started_at=datetime.now(UTC),
                )

                job = self._job_store.get(job_id)
                library_folder = None
                subscription = None
                if self._subscription_service and subscription_id:
                    try:
                        subscription = self._subscription_service.get(subscription_id)
                        subscription = await asyncio.to_thread(
                            self._subscription_service.prepare_for_sync,
                            subscription,
                        )
                        library_folder = sanitize_save_folder(
                            subscription.save_folder or subscription.name,
                            ascii_filenames=self._ascii_filenames,
                        )
                        if self._membership_service is not None:
                            snapshot = self._membership_service.begin_snapshot(
                                subscription_id,
                                job_id,
                            )
                            snapshot_id = snapshot.id
                    except SubscriptionNotFoundError:
                        logger.warning(
                            "Subscription %s not found for job %s",
                            subscription_id,
                            job_id[:8],
                        )
                else:
                    library_folder = self._effective_direct_folder()

                if self._sync_ledger_service and job is not None:
                    self._sync_ledger_service.mark_job_running(
                        job, save_folder=library_folder
                    )

                # Create progress callback that updates job store
                loop = asyncio.get_running_loop()

                def on_progress(
                    step: ProgressStep,
                    _message: str,
                    progress: float | None,
                    details: dict[str, Any] | None,
                ) -> None:
                    if cancel_token.is_cancelled:
                        return

                    status = self._step_to_status(step)
                    content_info = (
                        self._parse_content_info(details) if details else None
                    )

                    # Skip terminal states - handled by result
                    if status in (JobStatus.COMPLETED, JobStatus.FAILED):
                        return

                    loop.call_soon_threadsafe(
                        partial(
                            self._job_store.transition,
                            job_id,
                            status,
                            progress=progress,
                            content_info=content_info,
                        )
                    )

                sync_service = SyncService(
                    self._base_path,
                    self._effective_audio_format().value,
                    self._cookies_path,
                    self._effective_fetch_lyrics(),
                    self._effective_ytmusic_lyrics_fallback(),
                    self._effective_qq_lyrics_fallback(),
                    self._effective_replaygain(),
                    self._ascii_filenames,
                    self._effective_download_ugc(),
                    self._cache_path,
                    self._effective_audio_quality(),
                    self._effective_scrape_cooldown_hours(),
                    download_cache_path=self._effective_download_cache_path(),
                    cache_min_free_gb=(
                        self._prefs().cache_min_free_gb
                        if self._prefs() is not None
                        else 2.0
                    ),
                    data_min_free_gb=(
                        self._prefs().min_free_gb if self._prefs() is not None else 2.0
                    ),
                    folder_presence=self._folder_presence,
                )
                if url == DIRECT_RECOVER_URL:
                    if self._direct_recover is None:
                        result = SyncResult(
                            success=False,
                            error="Download Center recovery is not configured",
                        )
                    else:
                        recover = self._direct_recover
                        result = await asyncio.to_thread(
                            recover.run,
                            on_progress=on_progress,
                            cancel_token=cancel_token,
                            max_items=max_items,
                            audio_format=self._effective_audio_format().value,
                            audio_quality=self._effective_audio_quality(),
                            fetch_lyrics=self._effective_fetch_lyrics(),
                            ytmusic_lyrics_fallback=self._effective_ytmusic_lyrics_fallback(),
                            qq_lyrics_fallback=self._effective_qq_lyrics_fallback(),
                            apply_replaygain=self._effective_replaygain(),
                            download_ugc=self._effective_download_ugc(),
                            scrape_cooldown_hours=self._effective_scrape_cooldown_hours(),
                            download_cache_path=self._effective_download_cache_path(),
                            cache_min_free_gb=(
                                self._prefs().cache_min_free_gb
                                if self._prefs() is not None
                                else 2.0
                            ),
                            data_min_free_gb=(
                                self._prefs().min_free_gb
                                if self._prefs() is not None
                                else 2.0
                            ),
                        )
                else:
                    excluded: set[str] | None = None
                    if (
                        subscription_id is not None
                        and self._membership_service is not None
                    ):
                        excluded = self._membership_service.blocked_video_ids(
                            subscription_id
                        )
                    result = await asyncio.to_thread(
                        sync_service.run,
                        url,
                        on_progress,
                        cancel_token,
                        max_items,
                        library_folder,
                        excluded,
                    )

                # Handle result (cancelled status already set by cancel_job API)
                if cancel_token.is_cancelled:
                    if self._membership_service is not None and snapshot_id is not None:
                        self._membership_service.abort_snapshot(
                            snapshot_id,
                            status=SnapshotStatus.CANCELLED,
                            error_message="cancelled",
                        )
                elif result.success:
                    self._job_store.transition(
                        job_id,
                        JobStatus.COMPLETED,
                        progress=PROGRESS_COMPLETE,
                        content_info=result.content_info,
                        download_stats=result.download_stats,
                    )
                    # Update subscription metadata with latest info from YouTube Music
                    if (
                        self._subscription_service
                        and subscription_id
                        and result.content_info
                        and result.content_info.title
                    ):
                        subscription = self._subscription_service.update(
                            subscription_id,
                            {
                                "name": result.content_info.title,
                                "thumbnail_url": result.content_info.thumbnail_url,
                            },
                        )
                    if (
                        self._membership_service is not None
                        and subscription is not None
                        and snapshot_id is not None
                        and result.remote_tracks is not None
                    ):
                        self._membership_service.apply_trusted_sync(
                            subscription,
                            snapshot_id=snapshot_id,
                            remote_tracks=result.remote_tracks,
                            unavailable_count=result.unavailable_track_count,
                            unavailable_video_ids=result.unavailable_video_ids,
                        )
                    finished = self._job_store.get(job_id)
                    if self._sync_ledger_service and finished is not None:
                        cloud = (
                            len(result.remote_tracks)
                            if result.remote_tracks is not None
                            else None
                        )
                        # Catalog newly materialized files before marking the
                        # job finished. ``record_job_finished`` invokes the
                        # canonical post-job finalizer, which must be able to
                        # see these tracks in the same cycle.
                        if result.download_results and library_folder:
                            self._sync_ledger_service.record_download_results(
                                library_folder,
                                result.download_results,
                            )
                        self._sync_ledger_service.record_job_finished(
                            finished,
                            success=True,
                            save_folder=library_folder,
                            content_info=result.content_info,
                            download_stats=result.download_stats,
                            cloud_track_count=cloud,
                        )
                else:
                    error_msg = result.error or "Unknown error"
                    logger.error("Job %s failed: %s", job_id[:8], error_msg)
                    if self._membership_service is not None and snapshot_id is not None:
                        self._membership_service.abort_snapshot(
                            snapshot_id,
                            status=SnapshotStatus.FAILED,
                            error_message=error_msg,
                        )
                    self._job_store.transition(job_id, JobStatus.FAILED)
                    finished = self._job_store.get(job_id)
                    if self._sync_ledger_service and finished is not None:
                        self._sync_ledger_service.record_job_finished(
                            finished,
                            success=False,
                            save_folder=library_folder,
                            content_info=result.content_info,
                            download_stats=result.download_stats,
                        )

        except TimeoutError:
            logger.warning(
                "Job %s timed out after %d seconds",
                job_id[:8],
                int(self._effective_job_timeout()),
            )
            cancel_token.cancel()
            if self._membership_service is not None and snapshot_id is not None:
                self._membership_service.abort_snapshot(
                    snapshot_id,
                    status=SnapshotStatus.FAILED,
                    error_message="timed out",
                )
            self._job_store.transition(job_id, JobStatus.FAILED)
            self._finalize_failed_ledger(job_id, library_folder)

        except Exception as e:
            logger.exception("Job %s failed with error: %s", job_id[:8], e)
            if self._membership_service is not None and snapshot_id is not None:
                self._membership_service.abort_snapshot(
                    snapshot_id,
                    status=SnapshotStatus.FAILED,
                    error_message=str(e),
                )
            self._job_store.transition(job_id, JobStatus.FAILED)
            self._finalize_failed_ledger(job_id, library_folder)

        finally:
            # Clean up .part files if job was cancelled
            if cancel_token.is_cancelled:
                cleaned = 0
                if library_folder:
                    try:
                        cleaned += cleanup_part_files(
                            resolve_under_data(self._base_path, library_folder)
                        )
                    except ValueError:
                        pass
                cache_path = self._effective_download_cache_path()
                if cache_path is not None:
                    cleaned += cleanup_part_files(cache_path)
                if cleaned:
                    logger.info("Cleaned up %d partial download(s)", cleaned)

            self._cancel_tokens.pop(job_id, None)

            # Release active job slot AFTER cleanup, then start next
            # This ensures no concurrent downloads
            self._job_store.release_active(job_id)
            self._start_next_pending()

    @staticmethod
    def _step_to_status(step: ProgressStep) -> JobStatus:
        """Map progress step to job status."""
        return {
            ProgressStep.FETCHING_INFO: JobStatus.FETCHING_INFO,
            ProgressStep.DOWNLOADING: JobStatus.DOWNLOADING,
            ProgressStep.IMPORTING: JobStatus.IMPORTING,
            ProgressStep.COMPLETED: JobStatus.COMPLETED,
            ProgressStep.FAILED: JobStatus.FAILED,
        }.get(step, JobStatus.DOWNLOADING)

    @staticmethod
    def _parse_content_info(details: dict[str, Any]) -> ContentInfo | None:
        """Extract content info from details dict."""
        if data := details.get("content_info"):
            try:
                return ContentInfo(**data)
            except (TypeError, ValueError) as e:
                logger.warning("Failed to parse content info: %s", e)
        return None

    def _start_next_pending(self) -> None:
        """Start the next pending job if any."""
        if next_job := self._job_store.pop_next_pending():
            self.start_job(next_job)
