"""One synchronous library pipeline shared by every sync entry point.

Entry points choose only scope and trigger time. Matching backoff, metadata
verification, asset enrichment, on-disk verification, and result accounting
are owned here so playlist sync, Sync All, and scheduled sync cannot drift.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from yubal.utils.library import organized_save_folder

from yubal_api.db.external_library import EXTERNAL_ACCESS_PENDING
from yubal_api.services.external_library_service import (
    ExternalLibraryService,
    ScanResult,
    SyncPlaylistResult,
)
from yubal_api.services.library_dedup_service import LibraryDedupService
from yubal_api.services.library_enrichment_service import (
    EnrichmentSummary,
    LibraryEnrichmentService,
)
from yubal_api.services.library_health_service import LibraryHealthService
from yubal_api.services.library_stats_service import LibraryStatsService
from yubal_api.services.preferences import PreferencesStore
from yubal_api.services.sync_ledger_service import SyncLedgerService
from yubal_api.services.wanted_service import WantedService

logger = logging.getLogger(__name__)


@dataclass
class LibraryCycleResult:
    """Final, verified result of the library-side sync cycle."""

    health_ok: bool = True
    external: SyncPlaylistResult = field(default_factory=SyncPlaylistResult)
    wanted: dict[str, int] = field(default_factory=dict)
    enrichment: EnrichmentSummary = field(default_factory=EnrichmentSummary)


class SyncPipelineService:
    """Canonical scan → match → materialize → enrich → verify pipeline."""

    def __init__(
        self,
        *,
        library_health: LibraryHealthService | None = None,
        external_library_service: ExternalLibraryService | None = None,
        library_enrichment_service: LibraryEnrichmentService | None = None,
        library_dedup_service: LibraryDedupService | None = None,
        library_stats_service: LibraryStatsService | None = None,
        preferences_store: PreferencesStore | None = None,
        sync_ledger_service: SyncLedgerService | None = None,
        wanted_service: WantedService | None = None,
        media_changed: Callable[[], object] | None = None,
    ) -> None:
        self._library_health = library_health
        self._external_library_service = external_library_service
        self._library_enrichment_service = library_enrichment_service
        self._library_dedup_service = library_dedup_service
        self._library_stats_service = library_stats_service
        self._preferences_store = preferences_store
        self._sync_ledger_service = sync_ledger_service
        self._wanted_service = wanted_service
        self._media_changed = media_changed
        # A manual playlist sync and a global/scheduled cycle must never run
        # competing mutations over the same hardlinked files.
        self._lock = threading.RLock()

    def bind_media_changed(self, callback: Callable[[], object]) -> None:
        self._media_changed = callback

    def _notify_media_changed(self) -> None:
        if callable(self._media_changed):
            self._media_changed()

    def check_health(self) -> bool:
        """Refresh mount health. Returns True when the library is usable."""
        if self._library_health is None:
            return True
        try:
            snap = self._library_health.check()
            return bool(snap.ok)
        except Exception:
            logger.exception("Library health check failed")
            return False

    @staticmethod
    def _merge_external(total: SyncPlaylistResult, part: SyncPlaylistResult) -> None:
        for name in (
            "matched",
            "recovered",
            "checked",
            "errors",
            "deferred",
            "rejected",
            "meta_checked",
            "meta_verified",
            "enriched",
            "upgraded",
            "asset_errors",
        ):
            setattr(total, name, getattr(total, name) + getattr(part, name))

    def _external_enabled(self) -> bool:
        if self._external_library_service is None:
            return False
        if self._preferences_store is None:
            return True
        return bool(self._preferences_store.effective().external_library_enabled)

    def _run_external_playlist(
        self,
        dir_name: str,
        *,
        trigger: str,
        enrich: bool = True,
        raw_match: bool = True,
        verify_meta: bool = True,
        junk_match: bool = False,
    ) -> SyncPlaylistResult:
        svc = self._external_library_service
        health = self._library_health
        if svc is None or health is None or not self._external_enabled():
            return SyncPlaylistResult()

        # The domain service owns scan/match/materialization; this core owns
        # the mandatory post-materialization enrichment and final status.
        result = svc.sync_playlist(
            dir_name,
            health,
            enrich=False,
            raw_match=raw_match,
            verify_meta=verify_meta,
            junk_match=junk_match,
        )
        if enrich and self._library_enrichment_service is not None:
            view = svc.get_playlist_view(dir_name)
            budget = max(int(view.max_items), 20) if view is not None else 50
            assets = self._library_enrichment_service.enrich_library(
                budget=budget,
                reason=f"{trigger}:external:{dir_name}",
                save_folder=organized_save_folder(dir_name),
                force=False,
            )
            result.enriched += assets.enriched
            result.upgraded += assets.upgraded
            if assets.already_running:
                result.asset_errors += 1

        final_status = (
            "failed"
            if result.errors
            and not (
                result.matched
                or result.meta_verified
                or result.enriched
                or result.recovered
            )
            else "partial"
            if result.errors or result.asset_errors
            else "success"
        )
        svc.record_playlist_sync_status(dir_name, status=final_status)
        return result

    def sync_external_playlist(
        self,
        dir_name: str,
        *,
        trigger: str = "playlist",
        enrich: bool = True,
        raw_match: bool = True,
        verify_meta: bool = True,
        junk_match: bool = False,
    ) -> SyncPlaylistResult:
        """Run the canonical pipeline for one explicit playlist scope."""
        with self._lock:
            if not self.check_health():
                return SyncPlaylistResult(errors=1)
            if self._external_enabled() and self._external_library_service is not None:
                # Shared preflight for playlist-button sync as well as global
                # and scheduled sync. This also collapses legacy
                # Organized/Delete into the canonical recycle location.
                self._external_library_service.sync_playlists_from_disk()
            result = self._run_external_playlist(
                dir_name,
                trigger=trigger,
                enrich=enrich,
                raw_match=raw_match,
                verify_meta=verify_meta,
                junk_match=junk_match,
            )
            self._notify_media_changed()
            return result

    def drain_external_playlist(
        self,
        dir_name: str,
        *,
        trigger: str = "external-scan",
    ) -> SyncPlaylistResult:
        """Process one configured playlist until no eligible DB work remains.

        The database attempt fields are the durable work list. Each iteration
        remains bounded by the playlist's existing ``max_items`` setting and
        releases the shared lock between batches.
        """
        total = SyncPlaylistResult()
        svc = self._external_library_service
        if svc is None:
            return total
        while True:
            before = svc.pending_processing_count(dir_name)
            if before <= 0:
                break
            part = self.process_external_backlog_batch(
                dir_name,
                trigger=trigger,
            )
            self._merge_external(total, part)
            after = svc.pending_processing_count(dir_name)
            if after <= 0:
                break
            if after >= before:
                break
        return total

    def process_external_backlog_batch(
        self,
        dir_name: str,
        *,
        trigger: str = "background-worker",
    ) -> SyncPlaylistResult:
        """Run one verify-first external batch under the shared pipeline lock."""
        with self._lock:
            if not self.check_health():
                return SyncPlaylistResult(errors=1)
            if (
                self._external_enabled()
                and self._external_library_service is not None
            ):
                self._external_library_service.sync_playlists_from_disk()
            result = self._run_external_playlist(
                dir_name,
                trigger=trigger,
                enrich=False,
                raw_match=True,
                verify_meta=True,
                junk_match=False,
            )
            self._notify_media_changed()
            return result

    def _run_external_enabled(self, *, trigger: str) -> SyncPlaylistResult:
        total = SyncPlaylistResult()
        svc = self._external_library_service
        if svc is None or not self._external_enabled():
            return total
        svc.sync_playlists_from_disk()
        for playlist in svc.list_playlists():
            if (
                not playlist.enabled
                or getattr(playlist, "access_mode", "readonly")
                == EXTERNAL_ACCESS_PENDING
            ):
                continue
            try:
                svc.record_playlist_sync_status(playlist.dir_name, status="running")
                part = self._run_external_playlist(
                    playlist.dir_name,
                    trigger=trigger,
                    enrich=True,
                    raw_match=True,
                    verify_meta=True,
                    junk_match=False,
                )
            except Exception:
                logger.exception(
                    "Unified external pipeline failed for %s", playlist.dir_name
                )
                part = SyncPlaylistResult(errors=1)
            self._merge_external(total, part)
        return total

    def run_external_scan_and_match(
        self, *, trigger: str = "scheduler"
    ) -> SyncPlaylistResult:
        """Compatibility entry: now runs the complete enabled-playlist flow."""
        with self._lock:
            if not self.check_health():
                return SyncPlaylistResult(errors=1)
            result = self._run_external_enabled(trigger=trigger)
            self._notify_media_changed()
            return result

    def reconcile_external_inventory(
        self,
        *,
        dir_name: str | None = None,
    ) -> ScanResult | None:
        """Run the lightweight full path/stat inventory under the shared lock."""
        with self._lock:
            if not self.check_health():
                return None
            svc = self._external_library_service
            health = self._library_health
            if svc is None or health is None or not self._external_enabled():
                return None
            result = svc.discover_raw(
                health,
                enabled_only=False,
                dir_name=dir_name,
            )
            self._notify_media_changed()
            return result

    def refresh_library_summary(self) -> None:
        """Refresh exact physical totals only from the maintenance lane."""
        if self._library_stats_service is None:
            return
        with self._lock:
            self._library_stats_service.summary(force=True)

    def _run_wanted(self, *, force_ytm: bool = False) -> dict[str, int]:
        svc = self._wanted_service
        if svc is None:
            return {}
        if self._preferences_store is not None:
            prefs = self._preferences_store.effective()
            if not prefs.wanted_enabled:
                return {}
        try:
            # Scheduler/global passes observe retry/backoff.  An explicit
            # Wanted sync is user intent and may run while auto-match is off.
            return {
                key: int(value)
                for key, value in svc.run_sync_pass(force_ytm=force_ytm).items()
            }
        except Exception:
            logger.exception("Unified Wanted pipeline failed")
            return {"asset_failed": 1}

    def sync_wanted(
        self,
        *,
        trigger: str = "wanted",
        force_ytm: bool = False,
    ) -> dict[str, int]:
        """Run Wanted scope with the same post-materialization verification."""
        with self._lock:
            if not self.check_health():
                return {"asset_failed": 1}
            result = self._run_wanted(force_ytm=force_ytm)
            final = self._run_catalog_enrichment(
                trigger=trigger,
                budget=100,
            )
            result["final_enriched"] = final.enriched
            result["final_upgraded"] = final.upgraded
            result["final_failed"] = final.failed
            self._notify_media_changed()
            return result

    def _run_catalog_enrichment(
        self, *, trigger: str, budget: int | None
    ) -> EnrichmentSummary:
        svc = self._library_enrichment_service
        if svc is None:
            return EnrichmentSummary()
        try:
            return svc.enrich_library(
                budget=budget,
                reason=f"{trigger}:final",
                force=False,
            )
        except Exception:
            logger.exception("Unified final enrichment failed (%s)", trigger)
            return EnrichmentSummary(failed=1)

    def sync_catalog_folder(
        self,
        save_folder: str,
        *,
        trigger: str = "job",
        budget: int | None = 100,
    ) -> EnrichmentSummary:
        """Finalize one downloaded playlist after its job actually finishes."""
        with self._lock:
            if not self.check_health():
                return EnrichmentSummary(failed=1)
            svc = self._library_enrichment_service
            if svc is None:
                return EnrichmentSummary()
            result = svc.enrich_library(
                budget=budget,
                reason=f"{trigger}:folder",
                save_folder=save_folder,
                force=False,
            )
            if self._library_dedup_service is not None:
                self._library_dedup_service.collapse_for_folder(save_folder)
            return result

    def run_library_cycle(
        self,
        *,
        trigger: str,
        enrichment_budget: int | None = 500,
    ) -> LibraryCycleResult:
        """Run one complete global library cycle under a single lock."""
        with self._lock:
            health_ok = self.check_health()
            result = LibraryCycleResult(health_ok=health_ok)
            if not health_ok:
                result.external.errors = 1
                return result
            result.external = self._run_external_enabled(trigger=trigger)
            result.wanted = self._run_wanted()
            # This runs after external matching and Wanted fulfillment, so newly
            # materialized files are verified in the same cycle.
            result.enrichment = self._run_catalog_enrichment(
                trigger=trigger,
                budget=enrichment_budget,
            )
            self._notify_media_changed()
            return result

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
        """Legacy narrow action used by Direct-only controls."""
        svc = self._library_enrichment_service
        if svc is None:
            return

        def _run() -> None:
            try:
                svc.enrich_library(
                    budget=budget,
                    reason=reason,
                    save_folder=save_folder,
                    force=False,
                )
            except Exception:
                logger.exception("Library enrichment failed (%s)", reason)

        threading.Thread(
            target=_run,
            name=f"enrich-{reason}",
            daemon=True,
        ).start()

    def reconcile_direct(self) -> None:
        if self._sync_ledger_service is None:
            return
        try:
            self._sync_ledger_service.reconcile_direct()
        except Exception:
            logger.exception("Failed to reconcile Download Center ledger")
