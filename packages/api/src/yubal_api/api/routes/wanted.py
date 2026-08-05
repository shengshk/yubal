"""Wishlist / wanted playlist API."""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from yubal_api.api.deps import (
    SchedulerDep,
    SubscriptionServiceDep,
    SyncPipelineServiceDep,
    WantedServiceDep,
)
from yubal_api.schemas.wanted import (
    WantedAddRequest,
    WantedDeleteRequest,
    WantedPlaylistDeleteRequest,
    WantedSummary,
    WantedTrackResponse,
)
from yubal_api.services.subscription_service import is_liked_music_url

router = APIRouter(prefix="/wanted", tags=["wanted"])


@router.get("/summary", response_model=WantedSummary)
def get_summary(wanted: WantedServiceDep) -> WantedSummary:
    return wanted.summary()


@router.get("/tracks", response_model=list[WantedTrackResponse])
def list_tracks(wanted: WantedServiceDep) -> list[WantedTrackResponse]:
    return wanted.list_tracks()


@router.post("/tracks", response_model=WantedTrackResponse)
def add_track(body: WantedAddRequest, wanted: WantedServiceDep) -> WantedTrackResponse:
    try:
        return wanted.add(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/tracks/{track_id}/delete", status_code=status.HTTP_204_NO_CONTENT)
def delete_track(
    track_id: UUID,
    body: WantedDeleteRequest,
    wanted: WantedServiceDep,
) -> None:
    try:
        wanted.delete_track(track_id, mode=body.mode)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/delete", response_model=dict)
def delete_playlist(
    body: WantedPlaylistDeleteRequest, wanted: WantedServiceDep
) -> dict:
    try:
        return wanted.delete_playlist(mode=body.mode)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/tracks/{track_id}/link-local", response_model=WantedTrackResponse)
def link_local(track_id: UUID, wanted: WantedServiceDep) -> WantedTrackResponse:
    try:
        return wanted.try_link_local(track_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/match/local", response_model=dict)
def match_local(wanted: WantedServiceDep) -> dict:
    linked = wanted.match_local_batch()
    return {"linked": linked}


@router.post("/sync", response_model=dict)
async def sync_wanted(pipeline: SyncPipelineServiceDep) -> dict:
    """Run the canonical pipeline for the Wanted playlist scope."""
    return await asyncio.to_thread(
        pipeline.sync_wanted,
        trigger="wanted",
        force_ytm=True,
    )


@router.post("/tracks/{track_id}/match-ytm", response_model=dict)
async def match_ytm_one(
    track_id: UUID,
    wanted: WantedServiceDep,
    subscriptions: SubscriptionServiceDep,
    scheduler: SchedulerDep,
) -> dict:
    try:
        result = await asyncio.to_thread(wanted.match_ytm_one, track_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if result.get("awaiting_liked_sync"):
        liked = next(
            (sub for sub in subscriptions.list(enabled=True) if is_liked_music_url(sub.url)),
            None,
        )
        if liked is not None:
            job_id = scheduler.sync_subscription(liked.id)
            if job_id is not None:
                result["liked_sync_job_id"] = job_id
    return result


@router.post("/match/ytm", response_model=dict)
async def match_ytm_batch(wanted: WantedServiceDep) -> dict:
    # Manual Sync button prefers /sync; keep this for partial runs.
    return await asyncio.to_thread(wanted.match_ytm_batch, force=True)
