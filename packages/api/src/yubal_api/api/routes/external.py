"""External music library endpoints: playlists, scan, match, tracks."""

import asyncio
import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from yubal_api.api.deps import (
    ExternalLibraryServiceDep,
    LibraryHealthServiceDep,
    PreferencesStoreDep,
    SchedulerDep,
)
from yubal_api.services.external_library_service import (
    DeletePlaylistResult,
    ExternalPlaylistView,
    MatchBatchResult,
    PlaylistTrackView,
    ScanResult,
    SyncPlaylistResult,
)
from yubal_api.services.preferences import PreferencesStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/external", tags=["external"])


def _ensure_external_enabled(prefs: PreferencesStore) -> None:
    if not prefs.effective().external_library_enabled:
        raise HTTPException(
            status_code=403,
            detail="External library is disabled. Enable it in Settings.",
        )


class ExternalPlaylistResponse(BaseModel):
    dir_name: str
    allow_mutate: bool
    access_mode: Literal["pending", "readonly", "managed"] = "pending"
    access_mode_locked: bool = False
    source_mutated_at: datetime | None = None
    source_mutation_kind: str | None = None
    show_raw: bool
    show_junk: bool
    inventory_scanned: bool = False
    unmatched_count: int
    matched_count: int
    meta_verified_count: int = 0
    meta_rejected_count: int = 0
    meta_rejected_mutable_count: int = 0
    cloud: int
    local: int
    offline: int
    exclusive: int
    shared: int
    hardlink: int
    cover_track_path: str | None = None
    enabled: bool
    max_items: int
    sync_jitter_seconds: int
    offline_marking_enabled: bool
    offline_cleanup_enabled: bool
    offline_cleanup_action: str
    offline_cleanup_delay_hours: int
    last_synced_at: datetime | None = None
    last_sync_status: str | None = None


class PlaylistSettingsUpdate(BaseModel):
    allow_mutate: bool | None = Field(default=None)
    access_mode: Literal["pending", "readonly", "managed"] | None = None
    show_raw: bool | None = Field(default=None)
    show_junk: bool | None = Field(default=None)
    enabled: bool | None = Field(default=None)
    max_items: int | None = Field(default=None, ge=1, le=10000)
    sync_jitter_seconds: int | None = Field(default=None, ge=0, le=600)
    offline_marking_enabled: bool | None = Field(default=None)
    offline_cleanup_enabled: bool | None = Field(default=None)
    offline_cleanup_action: str | None = Field(
        default=None, pattern="^(delete|archive)$"
    )
    offline_cleanup_delay_hours: int | None = Field(default=None, ge=0, le=8760)


class PendingPlaylistActivationRequest(BaseModel):
    access_mode: Literal["readonly", "managed"]


class PendingPlaylistActivationResponse(BaseModel):
    activated: int


class ScanResponse(BaseModel):
    playlists: int
    scanned: int
    added: int
    updated: int
    removed: int
    errors: int


class MatchBatchRequest(BaseModel):
    limit: int = Field(default=25, ge=1, le=200)
    dir_name: str | None = Field(default=None)


class MatchBatchResponse(BaseModel):
    checked: int
    matched: int
    deferred: int
    rejected: int
    errors: int


class SyncPlaylistRequest(BaseModel):
    """Selectable steps for a single external-playlist sync."""

    enrich: bool = True
    raw_match: bool = True
    verify_meta: bool = True
    junk_match: bool = False


class SyncPlaylistResponse(BaseModel):
    matched: int
    recovered: int
    checked: int
    errors: int
    deferred: int
    rejected: int
    meta_checked: int = 0
    meta_verified: int = 0
    enriched: int = 0
    upgraded: int = 0
    asset_errors: int = 0
    queued: bool = False


class DeletePlaylistResponse(BaseModel):
    deleted_files: int
    deleted_locations: int
    deleted_raw: int
    moved: int
    reset_matches: int
    skipped_readonly: int = 0
    errors: int


class MatchOneRequest(BaseModel):
    rel_path: str
    mode: Literal["strict", "relaxed"] | None = None


class MatchCandidateResponse(BaseModel):
    video_id: str
    title: str
    artists: str
    album: str = ""
    thumbnail_url: str | None = None
    title_score: float = 0
    artist_score: float = 0
    score: float = 0


class MetaCandidateResponse(BaseModel):
    source: str
    source_id: str
    title: str
    artists: str
    album: str = ""
    source_url: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: int | None = None
    score: float = 0


class MatchOneResponse(BaseModel):
    rel_path: str
    matched: bool
    video_id: str | None = None
    ingested: bool = False
    mode_used: Literal["strict", "relaxed"] = "strict"
    candidates: list[MatchCandidateResponse] = Field(default_factory=list)
    meta_candidates: list[MetaCandidateResponse] = Field(default_factory=list)


class MatchAcceptRequest(BaseModel):
    rel_path: str
    video_id: str
    score: float | None = None


class MetaAcceptRequest(BaseModel):
    rel_path: str
    source: str
    source_id: str
    title: str
    artists: str
    album: str = ""
    source_url: str | None = None
    thumbnail_url: str | None = None


class MetaAcceptResponse(BaseModel):
    rel_path: str
    verified: bool
    meta_status: str = "verified"


class ExternalTrackResponse(BaseModel):
    rel_path: str
    dir_name: str
    title: str
    artist: str
    album: str
    video_id: str | None
    match_status: str
    is_raw: bool
    tags_complete: bool = False
    is_junk: bool = False
    junk_kind: str | None = None
    cover_url: str | None = None
    cover_source: str | None = None
    has_embedded_cover: bool = False
    album_artist: str | None = None
    year: str | None = None
    track_number: int | None = None
    in_direct: bool = False
    meta_status: str = "pending"
    meta_source: str | None = None
    meta_source_id: str | None = None
    meta_source_url: str | None = None
    can_mutate: bool = False


class ExternalTrackListResponse(BaseModel):
    total: int
    offset: int
    next_offset: int | None
    items: list[ExternalTrackResponse]


def _to_playlist_response(v: ExternalPlaylistView) -> ExternalPlaylistResponse:
    return ExternalPlaylistResponse(
        dir_name=v.dir_name,
        allow_mutate=v.allow_mutate,
        access_mode=v.access_mode,  # type: ignore[arg-type]
        access_mode_locked=v.access_mode_locked,
        source_mutated_at=v.source_mutated_at,
        source_mutation_kind=v.source_mutation_kind,
        show_raw=v.show_raw,
        show_junk=v.show_junk,
        inventory_scanned=v.inventory_scanned,
        unmatched_count=v.unmatched_count,
        matched_count=v.matched_count,
        meta_verified_count=v.meta_verified_count,
        meta_rejected_count=v.meta_rejected_count,
        meta_rejected_mutable_count=v.meta_rejected_mutable_count,
        cloud=v.cloud,
        local=v.local,
        offline=v.offline,
        exclusive=v.exclusive,
        shared=v.shared,
        hardlink=v.hardlink,
        cover_track_path=v.cover_track_path,
        enabled=v.enabled,
        max_items=v.max_items,
        sync_jitter_seconds=v.sync_jitter_seconds,
        offline_marking_enabled=v.offline_marking_enabled,
        offline_cleanup_enabled=v.offline_cleanup_enabled,
        offline_cleanup_action=v.offline_cleanup_action,
        offline_cleanup_delay_hours=v.offline_cleanup_delay_hours,
        last_synced_at=v.last_synced_at,
        last_sync_status=v.last_sync_status,
    )


def _to_scan_response(r: ScanResult) -> ScanResponse:
    return ScanResponse(
        playlists=r.playlists,
        scanned=r.scanned,
        added=r.added,
        updated=r.updated,
        removed=r.removed,
        errors=r.errors,
    )


def _to_match_batch_response(r: MatchBatchResult) -> MatchBatchResponse:
    return MatchBatchResponse(
        checked=r.checked,
        matched=r.matched,
        deferred=r.deferred,
        rejected=r.rejected,
        meta_checked=r.meta_checked,
        meta_verified=r.meta_verified,
        enriched=r.enriched,
        upgraded=r.upgraded,
        asset_errors=r.asset_errors,
        errors=r.errors,
    )


def _to_sync_playlist_response(r: SyncPlaylistResult) -> SyncPlaylistResponse:
    return SyncPlaylistResponse(
        matched=r.matched,
        recovered=r.recovered,
        checked=r.checked,
        errors=r.errors,
        deferred=r.deferred,
        rejected=r.rejected,
    )


def _to_delete_playlist_response(r: DeletePlaylistResult) -> DeletePlaylistResponse:
    return DeletePlaylistResponse(
        deleted_files=r.deleted_files,
        deleted_locations=r.deleted_locations,
        deleted_raw=r.deleted_raw,
        moved=r.moved,
        reset_matches=r.reset_matches,
        skipped_readonly=r.skipped_readonly,
        errors=r.errors,
    )


def _to_track_response(v: PlaylistTrackView) -> ExternalTrackResponse:
    return ExternalTrackResponse(
        rel_path=v.rel_path,
        dir_name=v.dir_name,
        title=v.title,
        artist=v.artist,
        album=v.album,
        video_id=v.video_id,
        match_status=v.match_status,
        is_raw=v.is_raw,
        tags_complete=v.tags_complete,
        is_junk=v.is_junk,
        junk_kind=v.junk_kind,
        cover_url=v.cover_url,
        cover_source=v.cover_source,
        has_embedded_cover=v.has_embedded_cover,
        album_artist=v.album_artist,
        year=v.year,
        track_number=v.track_number,
        in_direct=v.in_direct,
        meta_status=v.meta_status,
        meta_source=v.meta_source,
        meta_source_id=v.meta_source_id,
        meta_source_url=v.meta_source_url,
        can_mutate=v.can_mutate,
    )


@router.get("/playlists", response_model=list[ExternalPlaylistResponse])
def list_playlists(
    service: ExternalLibraryServiceDep,
    prefs: PreferencesStoreDep,
) -> list[ExternalPlaylistResponse]:
    if not prefs.effective().external_library_enabled:
        return []
    # Keep the UI directory list in sync with External/Raw without walking
    # the audio tree. Full indexing and matching remain explicit sync work.
    service.sync_playlists_from_disk()
    return [_to_playlist_response(v) for v in service.list_playlists()]


@router.patch("/playlists/{dir_name}", response_model=ExternalPlaylistResponse)
async def update_playlist_settings(
    dir_name: str,
    body: PlaylistSettingsUpdate,
    service: ExternalLibraryServiceDep,
    prefs: PreferencesStoreDep,
    scheduler: SchedulerDep,
) -> ExternalPlaylistResponse:
    _ensure_external_enabled(prefs)
    previous = service.get_playlist_view(dir_name)
    try:
        playlist = service.update_playlist_settings(
            dir_name,
            allow_mutate=body.allow_mutate,
            access_mode=body.access_mode,
            show_raw=body.show_raw,
            show_junk=body.show_junk,
            enabled=body.enabled,
            max_items=body.max_items,
            sync_jitter_seconds=body.sync_jitter_seconds,
            offline_marking_enabled=body.offline_marking_enabled,
            offline_cleanup_enabled=body.offline_cleanup_enabled,
            offline_cleanup_action=body.offline_cleanup_action,
            offline_cleanup_delay_hours=body.offline_cleanup_delay_hours,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if playlist is None:
        raise HTTPException(status_code=404, detail=f"playlist not found: {dir_name}")
    view = service.get_playlist_view(dir_name)
    if view is None:
        raise HTTPException(status_code=404, detail=f"playlist not found: {dir_name}")
    if (
        body.enabled is not None
        or body.sync_jitter_seconds is not None
        or body.access_mode is not None
    ):
        scheduler.invalidate_external_plan(dir_name)
    if (
        previous is not None
        and previous.access_mode == "pending"
        and view.access_mode != "pending"
    ):
        await scheduler.queue_external_playlist_sync(
            dir_name,
            enrich=False,
            raw_match=True,
            verify_meta=True,
            junk_match=False,
            scan_first=True,
            drain=True,
        )
    return _to_playlist_response(view)


@router.patch(
    "/activate-pending",
    response_model=PendingPlaylistActivationResponse,
)
async def activate_pending_playlists(
    body: PendingPlaylistActivationRequest,
    service: ExternalLibraryServiceDep,
    prefs: PreferencesStoreDep,
    scheduler: SchedulerDep,
) -> PendingPlaylistActivationResponse:
    """Classify every newly discovered external folder in one action."""
    _ensure_external_enabled(prefs)
    names = service.activate_pending_playlist_names(body.access_mode)
    for dir_name in names:
        await scheduler.queue_external_playlist_sync(
            dir_name,
            enrich=False,
            raw_match=True,
            verify_meta=True,
            junk_match=False,
            scan_first=True,
            drain=True,
        )
    return PendingPlaylistActivationResponse(activated=len(names))


@router.get("/playlists/{dir_name}/tracks", response_model=list[ExternalTrackResponse])
def list_playlist_tracks(
    dir_name: str,
    service: ExternalLibraryServiceDep,
    prefs: PreferencesStoreDep,
    show_raw: bool | None = None,
) -> list[ExternalTrackResponse]:
    _ensure_external_enabled(prefs)
    return [
        _to_track_response(v)
        for v in service.list_playlist_tracks(dir_name, show_raw=show_raw)
    ]


@router.get(
    "/playlists/{dir_name}/tracks/page",
    response_model=ExternalTrackListResponse,
)
def list_playlist_tracks_page(
    dir_name: str,
    service: ExternalLibraryServiceDep,
    prefs: PreferencesStoreDep,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=20, le=200),
    show_raw: bool | None = None,
    refresh: bool = False,
) -> ExternalTrackListResponse:
    """Return one stable external-track page for large playlist expansion."""
    _ensure_external_enabled(prefs)
    if refresh:
        service.invalidate_track_page_cache(dir_name)
    total, rows = service.list_playlist_tracks_page(
        dir_name,
        offset=offset,
        limit=limit,
        sort_key=prefs.effective().track_sort_key,
        show_raw=show_raw,
    )
    next_offset = offset + len(rows)
    return ExternalTrackListResponse(
        total=total,
        offset=offset,
        next_offset=next_offset if rows and next_offset < total else None,
        items=[_to_track_response(row) for row in rows],
    )


@router.post("/playlists/{dir_name}/sync", response_model=SyncPlaylistResponse)
async def sync_playlist(
    dir_name: str,
    scheduler: SchedulerDep,
    prefs: PreferencesStoreDep,
    body: SyncPlaylistRequest | None = None,
) -> SyncPlaylistResponse:
    _ensure_external_enabled(prefs)
    req = body if body is not None else SyncPlaylistRequest()
    if not (req.enrich or req.raw_match or req.verify_meta or req.junk_match):
        raise HTTPException(
            status_code=400,
            detail=(
                "at least one of enrich, raw_match, verify_meta, junk_match is required"
            ),
        )
    try:
        queued = await scheduler.queue_external_playlist_sync(
            dir_name,
            enrich=req.enrich,
            raw_match=req.raw_match,
            verify_meta=req.verify_meta,
            junk_match=req.junk_match,
        )
    except ValueError as e:
        detail = str(e)
        code = (
            409
            if "access mode is pending" in detail
            else 400
            if "at least one of" in detail
            else 404
        )
        raise HTTPException(status_code=code, detail=detail) from e
    return SyncPlaylistResponse(
        matched=0,
        recovered=0,
        checked=0,
        errors=0,
        deferred=0,
        rejected=0,
        queued=queued,
    )


@router.delete("/playlists/{dir_name}", response_model=DeletePlaylistResponse)
async def delete_playlist(
    dir_name: str,
    service: ExternalLibraryServiceDep,
    prefs: PreferencesStoreDep,
    confirm: bool = Query(default=False),
    mode: str = Query(
        ...,
        pattern=(
            "^(forget_matched|"
            "delete_matched|move_matched_to_direct|add_matched_to_direct|"
            "add_meta_verified_to_wanted|"
            "delete_unmatched|archive_meta_rejected|delete_meta_rejected|delete_all|"
            "clear_offline_delete|clear_offline_to_raw_delete)$"
        ),
    ),
) -> DeletePlaylistResponse:
    _ensure_external_enabled(prefs)
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true required")
    try:
        result = await asyncio.to_thread(
            service.delete_playlist,
            dir_name,
            mode,
            direct_folder=prefs.effective().direct_folder,
        )
    except ValueError as e:
        detail = str(e)
        code = 400 if "read-only" in detail or "unknown delete" in detail else 404
        raise HTTPException(status_code=code, detail=detail) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _to_delete_playlist_response(result)


@router.post("/scan", response_model=ScanResponse)
async def scan_external(
    health: LibraryHealthServiceDep,
    prefs: PreferencesStoreDep,
    scheduler: SchedulerDep,
    service: ExternalLibraryServiceDep,
) -> ScanResponse:
    """Reconcile files, then process every new/changed configured item."""
    _ensure_external_enabled(prefs)
    try:
        result = await asyncio.to_thread(
            scheduler.reconcile_external_inventory,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if result is None:
        raise HTTPException(status_code=503, detail="External library is unavailable")
    for dir_name in service.configured_playlist_names():
        await scheduler.queue_external_playlist_sync(
            dir_name,
            enrich=False,
            raw_match=True,
            verify_meta=True,
            junk_match=False,
            scan_first=False,
            drain=True,
        )
    return _to_scan_response(result)


@router.post("/match/batch", response_model=MatchBatchResponse)
async def match_batch(
    body: MatchBatchRequest,
    service: ExternalLibraryServiceDep,
    health: LibraryHealthServiceDep,
    prefs: PreferencesStoreDep,
) -> MatchBatchResponse:
    """Attempt YTM matches for a batch of raw tracks (respects backoff)."""
    _ensure_external_enabled(prefs)
    result = await asyncio.to_thread(
        service.match_batch,
        health,
        limit=body.limit,
        dir_name=body.dir_name,
        ignore_backoff=False,
        include_junk=False,
        enabled_only=body.dir_name is None,
    )
    return _to_match_batch_response(result)


@router.post("/match/one", response_model=MatchOneResponse)
async def match_one(
    body: MatchOneRequest,
    service: ExternalLibraryServiceDep,
    health: LibraryHealthServiceDep,
    prefs: PreferencesStoreDep,
) -> MatchOneResponse:
    """Attempt a single match + ingest for one raw track by rel_path.

    Manual match resets backoff first. Incomplete tags: fill empty fields via
    QQ/MB (never YTM), then YTM match. On miss returns YTM + meta candidates.
    """
    _ensure_external_enabled(prefs)
    health.ensure_healthy()
    try:
        result = await asyncio.to_thread(
            service.match_one_manual, body.rel_path, mode=body.mode
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return MatchOneResponse(
        rel_path=body.rel_path,
        matched=result.matched,
        video_id=result.video_id,
        ingested=result.ingested,
        mode_used=result.mode_used,  # type: ignore[arg-type]
        candidates=[
            MatchCandidateResponse(
                video_id=c.video_id,
                title=c.title,
                artists=c.artists,
                album=c.album,
                thumbnail_url=c.thumbnail_url,
                title_score=c.title_score,
                artist_score=c.artist_score,
                score=c.score,
            )
            for c in result.ytm_candidates
        ],
        meta_candidates=[
            MetaCandidateResponse(
                source=c.source,
                source_id=c.source_id,
                title=c.title,
                artists=c.artists,
                album=c.album,
                source_url=c.source_url,
                thumbnail_url=c.thumbnail_url,
                duration_seconds=c.duration_seconds,
                score=c.score,
            )
            for c in result.meta_candidates
        ],
    )


@router.post("/match/accept", response_model=MatchOneResponse)
async def accept_match(
    body: MatchAcceptRequest,
    service: ExternalLibraryServiceDep,
    health: LibraryHealthServiceDep,
    prefs: PreferencesStoreDep,
) -> MatchOneResponse:
    """Accept a manually chosen YTM candidate for one raw track."""
    _ensure_external_enabled(prefs)
    health.ensure_healthy()
    try:
        matched, video_id, ingested = await asyncio.to_thread(
            service.accept_match,
            body.rel_path,
            body.video_id,
            confidence=float(body.score or 0.0),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return MatchOneResponse(
        rel_path=body.rel_path,
        matched=matched,
        video_id=video_id,
        ingested=ingested,
    )


@router.post("/meta/accept", response_model=MetaAcceptResponse)
async def accept_meta(
    body: MetaAcceptRequest,
    service: ExternalLibraryServiceDep,
    prefs: PreferencesStoreDep,
) -> MetaAcceptResponse:
    """Accept a Wanted-source hit as tags-verified (no YTM video_id)."""
    _ensure_external_enabled(prefs)
    try:
        ok = await asyncio.to_thread(
            service.accept_meta_candidate,
            body.rel_path,
            source=body.source,
            source_id=body.source_id,
            title=body.title,
            artists=body.artists,
            album=body.album,
            source_url=body.source_url,
            thumbnail_url=body.thumbnail_url,
        )
    except ValueError as e:
        detail = str(e)
        code = 404 if "not found" in detail else 400
        raise HTTPException(status_code=code, detail=detail) from e
    return MetaAcceptResponse(
        rel_path=body.rel_path,
        verified=ok,
        meta_status="verified" if ok else "pending",
    )


class DeleteTrackResponse(BaseModel):
    deleted_files: int = 0
    deleted_locations: int = 0
    reset_matches: int = 0
    errors: int = 0
    ok: bool = True


@router.delete("/playlists/{dir_name}/tracks", response_model=DeleteTrackResponse)
async def delete_playlist_track(
    dir_name: str,
    service: ExternalLibraryServiceDep,
    prefs: PreferencesStoreDep,
    rel_path: str = Query(...),
    mode: str = Query(
        ...,
        pattern="^(keep_match|clear_match|delete_raw|move_to_direct|add_to_direct)$",
    ),
) -> DeleteTrackResponse:
    """Delete one external playlist track (matched Organized or unmatched Raw)."""
    _ensure_external_enabled(prefs)
    try:
        if mode in ("move_to_direct", "add_to_direct"):
            direct_folder = prefs.effective().direct_folder
            fn = (
                service.add_one_matched_to_direct
                if mode == "add_to_direct"
                else service.move_one_matched_to_direct
            )
            result = await asyncio.to_thread(
                fn,
                dir_name,
                rel_path=rel_path,
                direct_folder=direct_folder,
            )
            return DeleteTrackResponse(
                deleted_files=int(result.get("moved", 0)),
                deleted_locations=int(result.get("deleted_locations", 0)),
                reset_matches=0,
                errors=int(result.get("errors", 0)),
                ok=bool(result.get("ok")),
            )
        result = await asyncio.to_thread(
            service.delete_track, dir_name, rel_path=rel_path, mode=mode
        )
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return DeleteTrackResponse(**result)


@router.post("/reset", response_model=ExternalTrackResponse)
def reset_match(
    body: MatchOneRequest,
    service: ExternalLibraryServiceDep,
    prefs: PreferencesStoreDep,
) -> ExternalTrackResponse:
    """Manually clear backoff/fail state so a track is retried on next batch."""
    _ensure_external_enabled(prefs)
    raw_rel = body.rel_path.strip().replace("\\", "/").lstrip("/")
    if raw_rel.startswith("External/"):
        raw_rel = raw_rel[len("External/") :]
    if raw_rel.startswith("raw/"):
        raw_rel = raw_rel[len("raw/") :]
    row = service.reset_match(raw_rel)
    if row is None:
        raise HTTPException(
            status_code=404, detail=f"raw track not found: {body.rel_path}"
        )
    return ExternalTrackResponse(
        rel_path=f"Raw/{row.rel_path}",
        dir_name=row.dir_name,
        title=row.title,
        artist=row.artists,
        album=row.album,
        video_id=row.video_id,
        match_status=row.match_status,
        is_raw=True,
        tags_complete=service.tags_complete_enough(row.title, row.artists, row.album),
        is_junk=False,
        junk_kind=None,
        album_artist=row.album_artist,
        year=row.year,
        track_number=row.track_number,
    )
