"""FastAPI dependency injection factories.

This module provides type-safe dependency injection for FastAPI routes.
Dependencies are defined as Annotated types for clean, reusable injection.

Usage in routes:
    from yubal_api.api.deps import JobStoreDep, CookiesFileDep

    @router.get("/jobs")
    async def list_jobs(job_store: JobStoreDep) -> ...:
        ...
"""

from pathlib import Path
from typing import Annotated

from fastapi import Depends

from yubal_api.api.container import Services, get_services
from yubal_api.services.external_library_service import ExternalLibraryService
from yubal_api.services.factory_reset_service import FactoryResetService
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
from yubal_api.services.subscription_membership_service import (
    SubscriptionMembershipService,
)
from yubal_api.services.subscription_service import SubscriptionService
from yubal_api.services.sync_ledger_service import SyncLedgerService
from yubal_api.services.sync_pipeline_service import SyncPipelineService
from yubal_api.services.track_metadata_service import TrackMetadataService
from yubal_api.services.track_retag_service import TrackRetagService
from yubal_api.services.wanted_service import WantedService
from yubal_api.services.wash_service import WashService
from yubal_api.settings import Settings, get_settings

# -- Settings --

SettingsDep = Annotated[Settings, Depends(get_settings)]

# -- Service dependencies (request-scoped via app.state) --

ServicesDep = Annotated[Services, Depends(get_services)]


def _get_job_store(services: ServicesDep) -> JobStore:
    """Get job store from services container."""
    return services.job_store


def _get_job_executor(services: ServicesDep) -> JobExecutor:
    """Get job executor from services container."""
    return services.job_executor


def _get_scheduler(services: ServicesDep) -> Scheduler:
    """Get scheduler from services container."""
    return services.scheduler


def _get_subscription_service(services: ServicesDep) -> SubscriptionService:
    """Get subscription service from services container."""
    return services.subscription_service


def _get_membership_service(services: ServicesDep) -> SubscriptionMembershipService:
    return services.membership_service


def _get_sync_ledger_service(services: ServicesDep) -> SyncLedgerService:
    """Get sync ledger service from services container."""
    return services.sync_ledger_service


def _get_preferences_store(services: ServicesDep) -> PreferencesStore:
    """Get preferences store from services container."""
    return services.preferences_store


def _get_operation_gate(services: ServicesDep) -> OperationGate:
    return services.operation_gate


JobStoreDep = Annotated[JobStore, Depends(_get_job_store)]
JobExecutorDep = Annotated[JobExecutor, Depends(_get_job_executor)]
SchedulerDep = Annotated[Scheduler, Depends(_get_scheduler)]
SubscriptionServiceDep = Annotated[
    SubscriptionService, Depends(_get_subscription_service)
]
MembershipServiceDep = Annotated[
    SubscriptionMembershipService, Depends(_get_membership_service)
]
SyncLedgerServiceDep = Annotated[SyncLedgerService, Depends(_get_sync_ledger_service)]
PreferencesStoreDep = Annotated[PreferencesStore, Depends(_get_preferences_store)]


def _get_sync_pipeline_service(services: ServicesDep) -> SyncPipelineService:
    return services.sync_pipeline_service


SyncPipelineServiceDep = Annotated[
    SyncPipelineService, Depends(_get_sync_pipeline_service)
]


def _get_preselect_service(services: ServicesDep) -> PreselectService:
    return services.preselect_service


PreselectServiceDep = Annotated[PreselectService, Depends(_get_preselect_service)]


def _get_wash_service(services: ServicesDep) -> WashService:
    return services.wash_service


WashServiceDep = Annotated[WashService, Depends(_get_wash_service)]
OperationGateDep = Annotated[OperationGate, Depends(_get_operation_gate)]


def _get_search_service(services: ServicesDep) -> SearchService:
    return services.search_service


SearchServiceDep = Annotated[SearchService, Depends(_get_search_service)]


def _get_track_retag_service(services: ServicesDep) -> TrackRetagService:
    return services.track_retag_service


TrackRetagServiceDep = Annotated[TrackRetagService, Depends(_get_track_retag_service)]


def _get_track_metadata_service(services: ServicesDep) -> TrackMetadataService:
    return services.track_metadata_service


TrackMetadataServiceDep = Annotated[
    TrackMetadataService, Depends(_get_track_metadata_service)
]


def _get_library_enrichment_service(
    services: ServicesDep,
) -> LibraryEnrichmentService:
    return services.library_enrichment_service


LibraryEnrichmentServiceDep = Annotated[
    LibraryEnrichmentService, Depends(_get_library_enrichment_service)
]


def _get_library_health(services: ServicesDep) -> LibraryHealthService:
    return services.library_health


LibraryHealthServiceDep = Annotated[LibraryHealthService, Depends(_get_library_health)]


def _get_external_library_service(services: ServicesDep) -> ExternalLibraryService:
    return services.external_library_service


ExternalLibraryServiceDep = Annotated[
    ExternalLibraryService, Depends(_get_external_library_service)
]


def _get_wanted_service(services: ServicesDep) -> WantedService:
    return services.wanted_service


WantedServiceDep = Annotated[WantedService, Depends(_get_wanted_service)]


def _get_library_dedup_service(services: ServicesDep) -> LibraryDedupService:
    return services.library_dedup_service


LibraryDedupServiceDep = Annotated[
    LibraryDedupService, Depends(_get_library_dedup_service)
]


def _get_library_lookup_service(services: ServicesDep) -> LibraryLookupService:
    return services.library_lookup_service


LibraryLookupServiceDep = Annotated[
    LibraryLookupService, Depends(_get_library_lookup_service)
]


def _get_library_stats_service(services: ServicesDep) -> LibraryStatsService:
    return services.library_stats_service


LibraryStatsServiceDep = Annotated[
    LibraryStatsService, Depends(_get_library_stats_service)
]


def _get_factory_reset_service(services: ServicesDep) -> FactoryResetService:
    return services.factory_reset


FactoryResetServiceDep = Annotated[
    FactoryResetService, Depends(_get_factory_reset_service)
]


def _get_job_event_bus(services: ServicesDep) -> JobEventBus:
    """Get job event bus from services container."""
    return services.job_event_bus


def _get_log_buffer(services: ServicesDep) -> LogBuffer:
    """Get log buffer from services container."""
    return services.log_buffer


JobEventBusDep = Annotated[JobEventBus, Depends(_get_job_event_bus)]
LogBufferDep = Annotated[LogBuffer, Depends(_get_log_buffer)]

# -- Settings dependencies --

CookiesFileDep = Annotated[Path, Depends(lambda: get_settings().cookies_file)]
YtdlpDirDep = Annotated[Path, Depends(lambda: get_settings().ytdlp_dir)]


def _get_playlist_info_service() -> PlaylistInfoService:
    """Get playlist info service for fetching playlist metadata."""
    settings = get_settings()
    cookies_path = settings.cookies_file if settings.cookies_file.exists() else None
    return PlaylistInfoService(cookies_path=cookies_path)


PlaylistInfoServiceDep = Annotated[
    PlaylistInfoService, Depends(_get_playlist_info_service)
]
