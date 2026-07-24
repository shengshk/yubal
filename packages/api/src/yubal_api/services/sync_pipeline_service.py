"""Shared library steps for Sync All and the scheduled sync cycle.

Subscription job creation stays on ``Scheduler``; this service owns the
common health → enrich → external scan/match → hardlink-collapse tail
(and can run those steps alone). Prefer ``Scheduler.run_unified_sync`` for
the full pipeline so Sync All and cron stay identical aside from which
subscriptions are enqueued.
"""

from __future__ import annotations

import logging
import threading

from yubal_api.services.external_library_service import ExternalLibraryService
from yubal_api.services.library_dedup_service import LibraryDedupService
from yubal_api.services.library_enrichment_service import LibraryEnrichmentService
from yubal_api.services.library_health_service import LibraryHealthService
from yubal_api.services.preferences import PreferencesStore
from yubal_api.services.sync_ledger_service import SyncLedgerService

logger = logging.getLogger(__name__)


class SyncPipelineService:
    """Library-side steps shared by Sync All and the scheduler."""

    def __init__(
        self,
        *,
        library_health: LibraryHealthService | None = None,
        external_library_service: ExternalLibraryService | None = None,
        library_enrichment_service: LibraryEnrichmentService | None = None,
        library_dedup_service: LibraryDedupService | None = None,
        preferences_store: PreferencesStore | None = None,
        sync_ledger_service: SyncLedgerService | None = None,
    ) -> None:
        self._library_health = library_health
        self._external_library_service = external_library_service
        self._library_enrichment_service = library_enrichment_service
        self._library_dedup_service = library_dedup_service
        self._preferences_store = preferences_store
        self._sync_ledger_service = sync_ledger_service

    def check_health(self) -> bool:
        """Refresh mount health. Returns True when the library is ok."""
        if self._library_health is None:
            return True
        try:
            snap = self._library_health.check()
            return bool(snap.ok)
        except Exception:
            logger.exception("Library health check failed")
            return False

    def run_external_scan_and_match(self) -> None:
        """Scan enabled Raw dirs and match non-junk rows (respects backoff)."""
        svc = self._external_library_service
        health = self._library_health
        if svc is None or health is None:
            return
        prefs = (
            self._preferences_store.effective()
            if self._preferences_store is not None
            else None
        )
        if prefs is not None and not prefs.external_library_enabled:
            return
        if not health.current().ok:
            logger.warning(
                "Skipping external scan/match: library unhealthy (%s)",
                health.current().status,
            )
            return
        try:
            svc.scan_raw(health, enabled_only=True)
            svc.match_batch(
                health,
                limit=25,
                ignore_backoff=False,
                include_junk=False,
                enabled_only=True,
            )
        except Exception:
            logger.exception("External scan/match pipeline step failed")

    def collapse_divergent_copies(self) -> None:
        """Hardlink same-video_id copies left from earlier copy fallbacks."""
        svc = self._library_dedup_service
        if svc is None:
            return
        try:
            svc.collapse_divergent_copies()
        except Exception:
            logger.exception("Library hardlink collapse failed")

    def spawn_enrichment(
        self,
        *,
        budget: int | None,
        reason: str,
        save_folder: str | None = None,
    ) -> None:
        """Run a library enrichment pass in a daemon thread (never blocks)."""
        svc = self._library_enrichment_service
        if svc is None:
            return

        def _run() -> None:
            try:
                svc.enrich_library(
                    budget=budget, reason=reason, save_folder=save_folder
                )
            except Exception:
                logger.exception("Library enrichment failed (%s)", reason)

        threading.Thread(
            target=_run, name=f"enrich-{reason}", daemon=True
        ).start()

    def reconcile_direct(self) -> None:
        if self._sync_ledger_service is None:
            return
        try:
            self._sync_ledger_service.reconcile_direct()
        except Exception:
            logger.exception("Failed to reconcile Direct ledger")
