"""Orchestrate exclusive library-layout migration with job/scheduler freeze."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from pathlib import Path

from yubal_api.api.exceptions import MigrationFailedError
from yubal_api.services.job_executor import JobExecutor
from yubal_api.services.job_store import JobStore
from yubal_api.services.library_migrator import (
    MigrationResult,
    find_m3u_for_folder,
    run_layout_migration,
)
from yubal_api.services.operation_gate import OperationGate
from yubal_api.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)

JOB_DRAIN_TIMEOUT_SECONDS = 120.0
JOB_DRAIN_POLL_SECONDS = 0.5


class LayoutMigrationService:
    def __init__(
        self,
        *,
        data_path: Path,
        gate: OperationGate,
        job_store: JobStore,
        job_executor: JobExecutor,
        subscription_service: SubscriptionService,
    ) -> None:
        self._data_path = data_path
        self._gate = gate
        self._job_store = job_store
        self._job_executor = job_executor
        self._subscription_service = subscription_service

    def migrate_sync(self, *, from_layout: str, to_layout: str) -> MigrationResult:
        """Block jobs/scheduler, drain queue, migrate, then unlock.

        Must run in a worker thread (it is blocking).
        """
        if not self._gate.acquire(f"library migration {from_layout}→{to_layout}"):
            raise MigrationFailedError("Another maintenance operation is already running")

        try:
            cancelled = self._job_executor.cancel_all_jobs()
            logger.info("Migration: cancelled %s job token(s)", cancelled)
            self._drain_jobs()

            playlist_folders: list[tuple[str, Path | None]] = []
            for sub in self._subscription_service.list():
                folder = sub.save_folder or sub.name
                m3u = find_m3u_for_folder(self._data_path, folder)
                playlist_folders.append((folder, m3u))

            # Always include Direct as a destination bucket for leftovers
            if not any(name == "direct" for name, _ in playlist_folders):
                playlist_folders.append(
                    ("direct", find_m3u_for_folder(self._data_path, "direct"))
                )

            try:
                result = run_layout_migration(
                    self._data_path,
                    from_layout=from_layout,
                    to_layout=to_layout,
                    playlist_folders=playlist_folders,
                )
            except Exception as e:
                logger.exception("Library migration failed")
                raise MigrationFailedError(str(e)) from e

            logger.info(
                "Library migration %s→%s done: %s",
                from_layout,
                to_layout,
                result.message,
            )
            return result
        finally:
            self._gate.release()

    async def migrate(self, *, from_layout: str, to_layout: str) -> MigrationResult:
        return await asyncio.to_thread(
            self.migrate_sync,
            from_layout=from_layout,
            to_layout=to_layout,
        )

    def _drain_jobs(self) -> None:
        deadline = time.monotonic() + JOB_DRAIN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            active = [
                j
                for j in self._job_store.get_all()
                if not j.status.is_finished
            ]
            if not active:
                return
            # Re-signal cancel in case new transitions raced
            self._job_executor.cancel_all_jobs()
            time.sleep(JOB_DRAIN_POLL_SECONDS)
        still = [
            j.id[:8]
            for j in self._job_store.get_all()
            if not j.status.is_finished
        ]
        raise MigrationFailedError(
            f"Timed out waiting for jobs to stop: {', '.join(still) or 'unknown'}"
        )


def migration_result_payload(result: MigrationResult) -> dict:
    return {
        "from_layout": result.from_layout,
        "to_layout": result.to_layout,
        "moved": result.moved,
        "linked": result.linked,
        "skipped_same": result.skipped_same,
        "conflict_count": len(result.conflicts),
        "conflicts": [asdict(c) for c in result.conflicts],
        "errors_dir": result.errors_dir,
        "message": result.message,
    }
