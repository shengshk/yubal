"""Services container for dependency injection.

This module provides the Services container and dependency injection
utilities for accessing services from FastAPI routes via app.state.
"""

import logging
from dataclasses import dataclass

from fastapi import Request

from yubal_api.services.external_library_service import ExternalLibraryService
from yubal_api.services.job_event_bus import JobEventBus
from yubal_api.services.job_executor import JobExecutor
from yubal_api.services.job_store import JobStore
from yubal_api.services.library_dedup_service import LibraryDedupService
from yubal_api.services.library_enrichment_service import LibraryEnrichmentService
from yubal_api.services.library_health_service import LibraryHealthService
from yubal_api.services.library_lookup_service import LibraryLookupService
from yubal_api.services.library_stats_service import LibraryStatsService
from yubal_api.services.log_buffer import LogBuffer
from yubal_api.services.operation_gate import OperationGate
from yubal_api.services.playlist_info_service import PlaylistInfoService
from yubal_api.services.preferences import PreferencesStore
from yubal_api.services.preselect_service import PreselectService
from yubal_api.services.scheduler import Scheduler
from yubal_api.services.search_service import SearchService
from yubal_api.services.shutdown_coordinator import ShutdownCoordinator
from yubal_api.services.subscription_membership_service import (
    SubscriptionMembershipService,
)
from yubal_api.services.subscription_service import SubscriptionService
from yubal_api.services.sync_ledger_service import SyncLedgerService
from yubal_api.services.sync_pipeline_service import SyncPipelineService
from yubal_api.services.telegram import TelegramBotService
from yubal_api.services.track_metadata_service import TrackMetadataService
from yubal_api.services.track_retag_service import TrackRetagService
from yubal_api.services.wanted_service import WantedService
from yubal_api.services.wash_service import WashService

logger = logging.getLogger(__name__)


@dataclass
class Services:
    """Container for application services with proper lifecycle management.

    All services are created at startup and cleaned up at shutdown.
    Stored in FastAPI's app.state for proper request scoping.
    """

    job_store: JobStore
    job_executor: JobExecutor
    shutdown_coordinator: ShutdownCoordinator
    subscription_service: SubscriptionService
    membership_service: SubscriptionMembershipService
    sync_ledger_service: SyncLedgerService
    sync_pipeline_service: SyncPipelineService
    preferences_store: PreferencesStore
    scheduler: Scheduler
    job_event_bus: JobEventBus
    log_buffer: LogBuffer
    operation_gate: OperationGate
    preselect_service: PreselectService
    wash_service: WashService
    search_service: SearchService
    track_retag_service: TrackRetagService
    track_metadata_service: TrackMetadataService
    library_enrichment_service: LibraryEnrichmentService
    library_health: LibraryHealthService
    external_library_service: ExternalLibraryService
    library_dedup_service: LibraryDedupService
    library_lookup_service: LibraryLookupService
    library_stats_service: LibraryStatsService
    telegram_bot: TelegramBotService
    playlist_info: PlaylistInfoService
    wanted_service: WantedService

    def close(self) -> None:
        """Clean up resources. Called at application shutdown."""
        logger.info("Services cleaned up")
        self.log_buffer.clear()


def get_services(request: Request) -> Services:
    """Get services from request's app state (dependency injection).

    Args:
        request: FastAPI request object.

    Returns:
        Services container.

    Raises:
        RuntimeError: If services not initialized (app not running).
    """
    services = getattr(request.app.state, "services", None)
    if services is None:
        raise RuntimeError("Services not initialized. Is the app running?")
    return services
