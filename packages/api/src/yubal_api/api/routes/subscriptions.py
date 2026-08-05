"""Subscription management endpoints."""

import asyncio

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from yubal_api.api.deps import (
    JobExecutorDep,
    MembershipServiceDep,
    PreferencesStoreDep,
    SchedulerDep,
    SubscriptionServiceDep,
    SyncLedgerServiceDep,
)
from yubal_api.api.exceptions import QueueFullError
from yubal_api.db.subscription import (
    OfflineCleanupAction,
    Subscription,
    SubscriptionType,
)
from yubal_api.db.subscription_membership import MembershipStatus
from yubal_api.schemas.subscriptions import (
    SubscriptionCreate,
    LikedSongRatingRequest,
    SubscriptionListResponse,
    SubscriptionResponse,
    SubscriptionTrackDisposeRequest,
    SubscriptionTrackDisposeResponse,
    SubscriptionTrackFileDeleteRequest,
    SubscriptionTrackListResponse,
    SubscriptionTrackResponse,
    SubscriptionUpdate,
    SyncResponse,
)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


def _to_response(subscription: Subscription) -> SubscriptionResponse:
    """Build response with effective save_folder (falls back to name)."""
    offline_action = subscription.offline_cleanup_action
    if offline_action == OfflineCleanupAction.TO_WANTED:
        offline_action = OfflineCleanupAction.ARCHIVE
    return SubscriptionResponse(
        id=subscription.id,
        type=subscription.type,
        url=subscription.url,
        name=subscription.name,
        save_folder=subscription.save_folder or subscription.name,
        enabled=subscription.enabled,
        max_items=subscription.max_items,
        sync_jitter_seconds=subscription.sync_jitter_seconds,
        sync_mode=subscription.sync_mode,
        offline_marking_enabled=subscription.offline_marking_enabled,
        offline_cleanup_enabled=subscription.offline_cleanup_enabled,
        offline_cleanup_action=offline_action,  # type: ignore[arg-type]
        offline_cleanup_delay_hours=subscription.offline_cleanup_delay_hours,
        id_invalid_marking_enabled=getattr(
            subscription, "id_invalid_marking_enabled", True
        ),
        id_invalid_cleanup_enabled=getattr(
            subscription, "id_invalid_cleanup_enabled", False
        ),
        id_invalid_cleanup_action=getattr(
            subscription,
            "id_invalid_cleanup_action",
            OfflineCleanupAction.ARCHIVE,
        ),
        id_invalid_cleanup_delay_hours=int(
            getattr(subscription, "id_invalid_cleanup_delay_hours", 72) or 72
        ),
        thumbnail_url=subscription.thumbnail_url,
        created_at=subscription.created_at,
        last_synced_at=subscription.last_synced_at,
    )


# =============================================================================
# Sync routes MUST be registered BEFORE /{subscription_id} routes
# FastAPI matches routes in order - "sync" would be captured as a UUID otherwise
# =============================================================================


@router.post("/sync", response_model=SyncResponse)
async def sync_all_subscriptions(
    scheduler: SchedulerDep,
) -> SyncResponse:
    """Queue all enabled subscriptions and the shared library cycle."""
    job_ids, steps = await scheduler.queue_manual_sync()
    return SyncResponse(
        job_ids=job_ids,
        steps=steps,
    )


# =============================================================================
# CRUD routes
# =============================================================================


@router.get("", response_model=SubscriptionListResponse)
def list_subscriptions(
    service: SubscriptionServiceDep,
    enabled: bool | None = None,
    type: SubscriptionType | None = None,
) -> SubscriptionListResponse:
    """List all subscriptions."""
    subscriptions = service.list(enabled=enabled, type=type)
    return SubscriptionListResponse(items=[_to_response(s) for s in subscriptions])


@router.post(
    "", response_model=SubscriptionResponse, status_code=status.HTTP_201_CREATED
)
def create_subscription(
    data: SubscriptionCreate,
    service: SubscriptionServiceDep,
) -> SubscriptionResponse:
    """Create a new subscription."""
    created = service.create(str(data.url), data.max_items)
    return _to_response(created)


@router.patch("/{subscription_id}", response_model=SubscriptionResponse)
def update_subscription(
    subscription_id: UUID,
    data: SubscriptionUpdate,
    service: SubscriptionServiceDep,
    sync_ledger: SyncLedgerServiceDep,
    scheduler: SchedulerDep,
) -> SubscriptionResponse:
    """Update a subscription."""
    payload = data.model_dump(exclude_unset=True)
    confirm = bool(payload.pop("confirm_folder_move", False))
    folder_changed = "save_folder" in payload
    jitter_changed = "sync_jitter_seconds" in payload
    enabled_changed = "enabled" in payload
    subscription = service.update(
        subscription_id,
        payload,
        confirm_folder_move=confirm,
    )
    if folder_changed:
        sync_ledger.relocate_subscription_folder(
            subscription_id,
            subscription.save_folder or subscription.name,
        )
    if jitter_changed or enabled_changed:
        scheduler.invalidate_plan(subscription_id)
    return _to_response(subscription)


@router.delete("/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_subscription(
    subscription_id: UUID,
    service: SubscriptionServiceDep,
    sync_ledger: SyncLedgerServiceDep,
    preferences: PreferencesStoreDep,
    file_action: Literal[
        "keep", "keep_list", "delete", "move_to_direct"
    ] = Query(default="keep"),
) -> None:
    """Delete a subscription or wipe its files.

    ``file_action``:
    - ``keep_list``: delete files; keep subscription + membership list
    - ``keep``: leave files; delete subscription
    - ``delete``: delete membership files + subscription
    - ``move_to_direct``: move membership into the Direct bucket + delete subscription
    """
    direct_folder = preferences.effective().direct_folder
    service.delete(
        subscription_id,
        file_action=file_action,
        direct_folder=direct_folder,
    )
    if file_action == "keep_list":
        # Subscription remains; refresh ledger counts against disk.
        sub = service.get(subscription_id)
        sync_ledger.relocate_subscription_folder(
            subscription_id,
            sub.save_folder or sub.name,
        )
        return
    sync_ledger.delete_for_subscription(subscription_id)
    if file_action == "move_to_direct":
        sync_ledger.reconcile_direct(direct_folder)


@router.post("/{subscription_id}/sync", response_model=SyncResponse)
async def sync_subscription(
    subscription_id: UUID,
    service: SubscriptionServiceDep,
    scheduler: SchedulerDep,
) -> SyncResponse:
    """Sync a single subscription."""
    service.get(
        subscription_id
    )  # Validates existence, raises SubscriptionNotFoundError
    job_id = scheduler.sync_subscription(subscription_id)
    if job_id is None:
        raise QueueFullError()
    return SyncResponse(job_ids=[job_id])


@router.get(
    "/{subscription_id}/tracks",
    response_model=SubscriptionTrackListResponse,
)
def list_subscription_tracks(
    subscription_id: UUID,
    service: SubscriptionServiceDep,
    membership: MembershipServiceDep,
    status: MembershipStatus | None = None,
) -> SubscriptionTrackListResponse:
    """List logical membership for a subscription."""
    service.get(subscription_id)
    items = membership.list_membership(subscription_id, status=status)
    return SubscriptionTrackListResponse(
        items=[SubscriptionTrackResponse.model_validate(i) for i in items]
    )


@router.post("/{subscription_id}/tracks/{video_id}/rating", response_model=SyncResponse)
async def rate_liked_song(
    subscription_id: UUID,
    video_id: str,
    data: LikedSongRatingRequest,
    service: SubscriptionServiceDep,
    scheduler: SchedulerDep,
) -> SyncResponse:
    """Change YTM thumbs-up, then queue a Liked Music refresh to confirm it."""
    try:
        await asyncio.to_thread(
            service.rate_liked_song,
            subscription_id,
            video_id,
            liked=data.liked,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job_id = scheduler.sync_subscription(subscription_id)
    # The remote rating already succeeded. A currently running Liked Music
    # job will observe it, so do not report a false failure to the UI.
    return SyncResponse(job_ids=[job_id] if job_id is not None else [])


@router.post(
    "/{subscription_id}/clear-offline",
)
def clear_subscription_offline(
    subscription_id: UUID,
    service: SubscriptionServiceDep,
    membership: MembershipServiceDep,
    mode: Literal["delete", "to_raw_delete", "to_wanted"] = Query(default="delete"),
    status: Literal["offline", "id_invalid"] = Query(default="offline"),
) -> dict[str, int]:
    """Clear offline / ID-invalid memberships: hard-delete, Raw/Delete, or Wanted."""
    subscription = service.get(subscription_id)
    target = (
        MembershipStatus.ID_INVALID
        if status == "id_invalid"
        else MembershipStatus.OFFLINE
    )
    try:
        if mode == "to_wanted" and target == MembershipStatus.OFFLINE:
            raise ValueError(
                "to_wanted is only allowed for id_invalid, not not-in-playlist"
            )
        return membership.clear_offline(
            subscription,
            to_raw_delete=mode == "to_raw_delete",
            to_wanted=mode == "to_wanted",
            status=target,
        )
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/{subscription_id}/tracks/{video_id}/dispose",
    response_model=SubscriptionTrackDisposeResponse,
)
def dispose_subscription_track(
    subscription_id: UUID,
    video_id: str,
    data: SubscriptionTrackDisposeRequest,
    service: SubscriptionServiceDep,
    membership: MembershipServiceDep,
) -> SubscriptionTrackDisposeResponse:
    """Manually delete or archive one offline/active membership track."""
    subscription = service.get(subscription_id)
    try:
        result = membership.dispose_membership(
            subscription,
            video_id,
            action=data.action,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return SubscriptionTrackDisposeResponse(
        video_id=result.video_id,
        action=result.action,
        path=result.path,
        kept_reason=result.kept_reason,
    )


@router.post(
    "/{subscription_id}/tracks/{video_id}/download",
    response_model=SyncResponse,
)
def download_subscription_track(
    subscription_id: UUID,
    video_id: str,
    service: SubscriptionServiceDep,
    membership: MembershipServiceDep,
    job_executor: JobExecutorDep,
) -> SyncResponse:
    """Queue a one-track download into this subscription's save folder."""
    service.get(subscription_id)
    vid = (video_id or "").strip()
    if not vid or len(vid) > 32:
        raise HTTPException(status_code=400, detail="Invalid video_id")
    rows = membership.list_membership(subscription_id)
    row = next(
        (
            r
            for r in rows
            if r.video_id == vid or r.catalog_video_id == vid
        ),
        None,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Track not in subscription")
    if row.membership_status == MembershipStatus.BLOCKED:
        raise HTTPException(status_code=409, detail="Track is blacklisted")
    target = row.catalog_video_id or row.video_id
    url = f"https://music.youtube.com/watch?v={target}"
    job = job_executor.create_and_start_job(url, None, subscription_id=subscription_id)
    if job is None:
        raise QueueFullError()
    return SyncResponse(job_ids=[job.id])


@router.post(
    "/{subscription_id}/tracks/{video_id}/delete-file",
    response_model=SubscriptionTrackDisposeResponse,
)
def delete_subscription_track_file(
    subscription_id: UUID,
    video_id: str,
    data: SubscriptionTrackFileDeleteRequest,
    service: SubscriptionServiceDep,
    membership: MembershipServiceDep,
) -> SubscriptionTrackDisposeResponse:
    """Delete local audio; keep membership. Optionally blacklist auto-sync."""
    subscription = service.get(subscription_id)
    result = membership.delete_track_keep_membership(
        subscription,
        video_id,
        block=data.block,
    )
    return SubscriptionTrackDisposeResponse(
        video_id=result.video_id,
        action=result.action,
        path=result.path,
        kept_reason=result.kept_reason,
        membership_status=(
            MembershipStatus.BLOCKED if data.block else MembershipStatus.ACTIVE
        ),
    )


@router.post(
    "/{subscription_id}/tracks/{video_id}/unblock",
    response_model=SubscriptionTrackDisposeResponse,
)
def unblock_subscription_track(
    subscription_id: UUID,
    video_id: str,
    service: SubscriptionServiceDep,
    membership: MembershipServiceDep,
) -> SubscriptionTrackDisposeResponse:
    """Clear sync blacklist so the next sync may recover the track."""
    subscription = service.get(subscription_id)
    row = membership.unblock_membership(subscription, video_id)
    if row is None:
        raise HTTPException(status_code=404, detail="membership not found")
    return SubscriptionTrackDisposeResponse(
        video_id=video_id,
        action="unblocked",
        membership_status=MembershipStatus.ACTIVE,
    )


@router.post(
    "/{subscription_id}/tracks/{video_id}/remove-from-list",
    response_model=SubscriptionTrackDisposeResponse,
)
def remove_subscription_track_from_list(
    subscription_id: UUID,
    video_id: str,
    service: SubscriptionServiceDep,
    membership: MembershipServiceDep,
) -> SubscriptionTrackDisposeResponse:
    """Remove membership from the subscription list and delete the file if unused."""
    subscription = service.get(subscription_id)
    result = membership.remove_from_list(subscription, video_id)
    if result.action == "missing":
        raise HTTPException(status_code=404, detail="membership not found")
    return SubscriptionTrackDisposeResponse(
        video_id=result.video_id,
        action=result.action,
        path=result.path,
        kept_reason=result.kept_reason,
    )
