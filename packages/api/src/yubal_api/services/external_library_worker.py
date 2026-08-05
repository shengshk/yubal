"""Continuous background processor for external music libraries.

Runs independently of the subscription scheduler so tag indexing and
metadata verification keep moving even when the scheduler loop restarts or
when only external-library work is due.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from yubal_api.db.external_library import EXTERNAL_ACCESS_PENDING

if TYPE_CHECKING:
    from yubal_api.services.external_library_service import ExternalLibraryService
    from yubal_api.services.preferences import PreferencesStore
    from yubal_api.services.sync_pipeline_service import SyncPipelineService

logger = logging.getLogger(__name__)

_IDLE_SECONDS = 15.0
_BUSY_PAUSE_SECONDS = 0.5


class ExternalLibraryWorker:
    """Drain external-library backlogs in a dedicated daemon thread."""

    def __init__(
        self,
        *,
        pipeline: SyncPipelineService,
        external_service: ExternalLibraryService,
        preferences: PreferencesStore,
    ) -> None:
        self._pipeline = pipeline
        self._external = external_service
        self._preferences = preferences
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="external-library-worker",
            daemon=True,
        )
        self._thread.start()
        logger.info("External library worker started")

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._thread = None

    def _enabled(self) -> bool:
        return bool(self._preferences.effective().external_library_enabled)

    def _candidate_dirs(self) -> list[str]:
        dirs: list[tuple[int, str]] = []
        for playlist in self._external.list_playlists():
            if not playlist.enabled:
                continue
            if playlist.access_mode == EXTERNAL_ACCESS_PENDING:
                continue
            pending = self._external.pending_processing_count(playlist.dir_name)
            if pending > 0:
                dirs.append((pending, playlist.dir_name))
        dirs.sort(reverse=True)
        return [dir_name for _, dir_name in dirs]

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self._enabled():
                if self._stop.wait(_IDLE_SECONDS):
                    break
                continue

            candidates = self._candidate_dirs()
            if not candidates:
                if self._stop.wait(_IDLE_SECONDS):
                    break
                continue

            progressed = False
            for dir_name in candidates:
                if self._stop.is_set():
                    break
                try:
                    before = self._external.pending_processing_count(dir_name)
                    if before <= 0:
                        continue
                    self._external.record_playlist_sync_status(
                        dir_name,
                        status="running",
                    )
                    result = self._pipeline.process_external_backlog_batch(
                        dir_name,
                        trigger="background-worker",
                    )
                    after = self._external.pending_processing_count(dir_name)
                    if after < before or (
                        result.meta_verified
                        or result.matched
                        or result.enriched
                        or result.recovered
                    ):
                        progressed = True
                    status = "failed" if result.errors and not (
                        result.meta_verified or result.matched or result.recovered
                    ) else "success"
                    self._external.record_playlist_sync_status(dir_name, status=status)
                except Exception:
                    logger.exception(
                        "External library worker failed for %s",
                        dir_name,
                    )
                    self._external.record_playlist_sync_status(
                        dir_name,
                        status="failed",
                    )

            pause = _BUSY_PAUSE_SECONDS if progressed else _IDLE_SECONDS
            if self._stop.wait(pause):
                break
