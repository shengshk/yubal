"""Sync ledger endpoints for the Sync Center."""

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from yubal_api.api.deps import (
    LibraryEnrichmentServiceDep,
    MembershipServiceDep,
    SchedulerDep,
    SyncLedgerServiceDep,
)
from yubal_api.db.subscription_membership import MembershipStatus
from yubal_api.db.sync_ledger import LedgerKind, SyncLedgerEntry
from yubal_api.schemas.sync_ledger import (
    SyncLedgerListResponse,
    SyncLedgerResponse,
    SyncTrackListResponse,
)
from yubal_api.services.library_enrichment_service import EnrichmentSummary

router = APIRouter(prefix="/sync-ledger", tags=["sync-ledger"])


class DirectUpdateRequest(BaseModel):
    enabled: bool | None = None
    max_items: int | None = Field(default=None, ge=1, le=10000)
    sync_jitter_seconds: int | None = Field(default=None, ge=0, le=600)
    offline_marking_enabled: bool | None = None
    offline_cleanup_enabled: bool | None = None
    offline_cleanup_action: str | None = Field(
        default=None, pattern="^(delete|archive|to_wanted)$"
    )
    offline_cleanup_delay_hours: int | None = Field(default=None, ge=0, le=8760)


def _direct_response(
    entry: SyncLedgerEntry, service: SyncLedgerServiceDep
) -> SyncLedgerResponse:
    body = SyncLedgerResponse.model_validate(entry)
    if entry.kind != LedgerKind.DIRECT:
        return body
    policy = service.direct_policy()
    return body.model_copy(
        update={
            "enabled": policy["enabled"],
            "max_items": policy["max_items"],
            "sync_jitter_seconds": policy["sync_jitter_seconds"],
            "offline_marking_enabled": policy["offline_marking_enabled"],
            "offline_cleanup_enabled": policy["offline_cleanup_enabled"],
            "offline_cleanup_action": policy["offline_cleanup_action"],
            "offline_cleanup_delay_hours": policy["offline_cleanup_delay_hours"],
            "offline_count": service.direct_offline_count(),
            "blocked_count": service.direct_blocked_count(),
        }
    )


@router.get("", response_model=SyncLedgerListResponse)
def list_sync_ledger(
    service: SyncLedgerServiceDep,
    membership: MembershipServiceDep,
) -> SyncLedgerListResponse:
    """List durable sync ledger rows without scanning media folders."""
    items = service.list(reconcile=False)
    responses: list[SyncLedgerResponse] = []
    for item in items:
        response = _direct_response(item, service)
        summary = service.folder_track_summary(item.save_folder)
        updates: dict[str, int | str | None] = {
            "missing_count": summary.missing_active_count,
            "cover_track_path": summary.cover_track_path,
        }
        if item.kind == LedgerKind.SUBSCRIPTION and item.subscription_id is not None:
            members = membership.list_membership(item.subscription_id)
            updates.update(
                {
                    "offline_count": sum(
                        row.membership_status == MembershipStatus.OFFLINE
                        for row in members
                    ),
                    "id_invalid_count": sum(
                        row.membership_status == MembershipStatus.ID_INVALID
                        for row in members
                    ),
                    "blocked_count": sum(
                        row.membership_status == MembershipStatus.BLOCKED
                        for row in members
                    ),
                    "missing_count": sum(
                        row.membership_status == MembershipStatus.ACTIVE
                        and row.catalog_video_id not in summary.present_video_ids
                        for row in members
                    ),
                }
            )
        responses.append(response.model_copy(update=updates))
    return SyncLedgerListResponse(items=responses)


@router.get("/tracks", response_model=SyncTrackListResponse)
def list_sync_tracks(
    service: SyncLedgerServiceDep,
    save_folder: str = Query(min_length=1, max_length=400),
) -> SyncTrackListResponse:
    """List tracks under a save folder (m3u order, else disk scan)."""
    try:
        folder, items = service.list_tracks(save_folder)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return SyncTrackListResponse(save_folder=folder, total=len(items), items=items)


@router.delete("/direct/track", response_model=SyncLedgerResponse)
def delete_direct_track(
    service: SyncLedgerServiceDep,
    relative_path: str = Query(min_length=1, max_length=800),
    mode: str = Query(
        default="keep_list",
        pattern="^(keep_list|wipe_list|block|migrate_to_external|migrate_to_wanted)$",
    ),
) -> SyncLedgerResponse:
    """Delete or migrate one Direct-download audio file.

    keep_list: file gone, catalog row kept for auto-recover.
    wipe_list: also remove catalog membership.
    block: delete file + ban auto-recover (禁止回补).
    migrate_to_external: move file+list into Organized/Default.
    migrate_to_wanted: strip id, hardlink into wishlist, drop list+file.
    """
    try:
        entry = service.delete_direct_track(relative_path, mode=mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _direct_response(entry, service)


@router.post("/direct/tracks/{video_id}/unblock", response_model=SyncLedgerResponse)
def unblock_direct_track(
    video_id: str,
    service: SyncLedgerServiceDep,
) -> SyncLedgerResponse:
    try:
        entry = service.unblock_direct_track(video_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return _direct_response(entry, service)


@router.post(
    "/direct/tracks/{video_id}/remove-from-list",
    response_model=SyncLedgerResponse,
)
def remove_direct_track_from_list(
    video_id: str,
    service: SyncLedgerServiceDep,
) -> SyncLedgerResponse:
    try:
        entry = service.remove_direct_track_from_list(video_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return _direct_response(entry, service)


@router.patch("/direct", response_model=SyncLedgerResponse)
def update_direct(
    data: DirectUpdateRequest,
    service: SyncLedgerServiceDep,
    scheduler: SchedulerDep,
) -> SyncLedgerResponse:
    """Update Download Center policy. Its system path is fixed."""
    entry = service.update_direct_folder(
        enabled=data.enabled,
        max_items=data.max_items,
        sync_jitter_seconds=data.sync_jitter_seconds,
        offline_marking_enabled=data.offline_marking_enabled,
        offline_cleanup_enabled=data.offline_cleanup_enabled,
        offline_cleanup_action=data.offline_cleanup_action,
        offline_cleanup_delay_hours=data.offline_cleanup_delay_hours,
    )
    if (
        data.enabled is not None
        or data.sync_jitter_seconds is not None
        or data.max_items is not None
    ):
        scheduler.invalidate_direct_plan()
    return _direct_response(entry, service)


@router.post("/direct/reconcile", response_model=SyncLedgerResponse)
def reconcile_direct(service: SyncLedgerServiceDep) -> SyncLedgerResponse:
    """Reconcile Direct ledger counts against on-disk files (no download)."""
    entry = service.reconcile_direct()
    if entry is None:
        entry = service.ensure_direct_entry()
    return _direct_response(entry, service)


@router.post("/direct/sync")
def sync_direct(scheduler: SchedulerDep) -> dict:
    """Manually trigger Direct auto-recover (bypasses jitter)."""
    job_id = scheduler.sync_direct()
    if job_id is None:
        raise HTTPException(
            status_code=503, detail="Could not start Direct recover job"
        )
    return {"job_id": job_id}


@router.post("/enrich", response_model=EnrichmentSummary)
def enrich_library(
    service: LibraryEnrichmentServiceDep,
    budget: int = Query(default=100, ge=1, le=2000),
) -> EnrichmentSummary:
    """Fill missing assets and upgrade non-premium library tracks."""
    return service.enrich_library(budget=budget, reason="manual")


@router.post("/enrich/{video_id}", response_model=EnrichmentSummary)
def enrich_track(
    video_id: str,
    service: LibraryEnrichmentServiceDep,
) -> EnrichmentSummary:
    """Fill or upgrade one non-premium track without replacing text tags."""
    if not video_id or "/" in video_id or "\\" in video_id or len(video_id) > 32:
        raise HTTPException(status_code=400, detail="invalid video_id")
    return service.enrich_track(video_id)


@router.delete("/direct", status_code=status.HTTP_204_NO_CONTENT)
def delete_direct(
    service: SyncLedgerServiceDep,
    confirm: bool = Query(default=False),
    mode: str = Query(
        default="wipe_list",
        pattern=(
            "^(keep_list|wipe_list|clear_offline_delete|"
            "clear_offline_to_raw_delete|clear_offline_to_wanted|migrate_to_external)$"
        ),
    ),
) -> None:
    """Delete Direct files / clear offline / migrate to external Default."""
    try:
        service.delete_direct(confirm=confirm, mode=mode)
    except OSError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
