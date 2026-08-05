"""Runtime settings endpoints (user preferences)."""

import asyncio
import os
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query
from yubal.services.scrape_state import ScrapeStateStore
from yubal.utils.library import EXTERNAL_ROOT, WANTED_ROOT

from yubal_api.api.deps import (
    ExternalLibraryServiceDep,
    FactoryResetServiceDep,
    LibraryHealthServiceDep,
    PreferencesStoreDep,
    ServicesDep,
    SettingsDep,
)
from yubal_api.schemas.settings import (
    FactoryResetExecuteRequest,
    FactoryResetPreviewRequest,
    FactoryResetPreviewResponse,
    FactoryResetResultResponse,
    SettingsResponse,
    SettingsUpdate,
)
from yubal_api.services.factory_reset_service import FactoryResetMode
from yubal_api.services.preferences import DOWNLOAD_CACHE_ROOT, PreferencesStore

router = APIRouter(prefix="/settings", tags=["settings"])


def _to_response(
    store: PreferencesStore,
    *,
    maintenance_locked: bool = False,
    library_health_status: str | None = None,
    library_health_reason: str | None = None,
    telegram_api_url: str = "",
    telegram_bot_running: bool = False,
) -> SettingsResponse:
    prefs = store.effective()
    status = store.disk_status()
    cache_status = store.cache_disk_status()
    return SettingsResponse(
        min_free_gb=prefs.min_free_gb,
        direct_download_limit=prefs.direct_download_limit,
        index_threshold=prefs.index_threshold,
        track_sort_key=prefs.track_sort_key,  # type: ignore[arg-type]
        search_result_ttl_hours=prefs.search_result_ttl_hours,
        audio_format=prefs.audio_format,  # type: ignore[arg-type]
        audio_quality=prefs.audio_quality,
        fetch_lyrics=prefs.fetch_lyrics,
        ytmusic_lyrics_fallback=prefs.ytmusic_lyrics_fallback,
        qq_lyrics_fallback=prefs.qq_lyrics_fallback,
        scrape_cooldown_hours=prefs.scrape_cooldown_hours,
        download_ugc=prefs.download_ugc,
        replaygain=prefs.replaygain,
        scheduler_enabled=prefs.scheduler_enabled,
        scheduler_cron=prefs.scheduler_cron,
        external_inventory_schedule_enabled=(prefs.external_inventory_schedule_enabled),
        external_inventory_schedule_time=prefs.external_inventory_schedule_time,
        job_timeout_seconds=prefs.job_timeout_seconds,
        external_library_enabled=prefs.external_library_enabled,
        external_new_playlist_mode=prefs.external_new_playlist_mode,  # type: ignore[arg-type]
        # Legacy preselect fields kept for schema compat; always off / fixed.
        preselect_enabled=False,
        wash_enabled=False,
        preselect_root=str(EXTERNAL_ROOT),
        preselect_place_mode="link",  # type: ignore[arg-type]
        preselect_match_mode="standard",  # type: ignore[arg-type]
        preselect_hardlink_ok=None,
        preselect_indexed=0,
        preselect_placed=0,
        download_cache_enabled=prefs.download_cache_enabled,
        cache_path=str(cache_status.path),
        cache_min_free_gb=prefs.cache_min_free_gb,
        cache_free_bytes=cache_status.free_bytes,
        cache_free_gb=round(cache_status.free_gb, 2),
        cache_available=(
            cache_status.path.is_dir() and os.access(cache_status.path, os.W_OK)
        ),
        match_backoff_cap_days=prefs.match_backoff_cap_days,
        match_strictness=prefs.match_strictness,  # type: ignore[arg-type]
        cover_excellence_px=prefs.cover_excellence_px,
        cover_probe_fresh_days=prefs.cover_probe_fresh_days,
        cover_download_fresh_days=prefs.cover_download_fresh_days,
        library_health_status=library_health_status,
        library_health_reason=library_health_reason,
        data_path=str(status.path),
        free_bytes=status.free_bytes,
        free_gb=round(status.free_gb, 2),
        enough_space=status.enough_space,
        maintenance_locked=maintenance_locked,
        telegram_bot_token=prefs.telegram_bot_token,
        telegram_admin_ids=prefs.telegram_admin_ids,
        telegram_user_ids=prefs.telegram_user_ids,
        telegram_daily_limit=prefs.telegram_daily_limit,
        telegram_api_url=telegram_api_url,
        telegram_bot_running=telegram_bot_running,
        wanted_enabled=prefs.wanted_enabled,
        wanted_auto_match_enabled=prefs.wanted_auto_match_enabled,
        wanted_max_items=prefs.wanted_max_items,
        wanted_sync_jitter_seconds=prefs.wanted_sync_jitter_seconds,
        wanted_source_musicbrainz=prefs.wanted_source_musicbrainz,
        wanted_source_qq=prefs.wanted_source_qq,
        wanted_source_discogs=prefs.wanted_source_discogs,
        wanted_source_lastfm=prefs.wanted_source_lastfm,
        lastfm_api_key=prefs.lastfm_api_key,
    )


def _telegram_status(services: ServicesDep, settings: SettingsDep) -> tuple[str, bool]:
    bot = getattr(services, "telegram_bot", None)
    running = bool(bot is not None and bot.is_running)
    return (settings.tg_api_url or "").strip(), running


@router.get("", response_model=SettingsResponse)
def get_settings(
    store: PreferencesStoreDep,
    services: ServicesDep,
    library_health: LibraryHealthServiceDep,
    settings: SettingsDep,
) -> SettingsResponse:
    """Get editable preferences and current free disk space."""
    health = library_health.current()
    api_url, running = _telegram_status(services, settings)
    return _to_response(
        store,
        maintenance_locked=services.operation_gate.is_locked,
        library_health_status=health.status,
        library_health_reason=health.reason,
        telegram_api_url=api_url,
        telegram_bot_running=running,
    )


@router.patch("", response_model=SettingsResponse)
async def update_settings(
    data: SettingsUpdate,
    store: PreferencesStoreDep,
    services: ServicesDep,
    library_health: LibraryHealthServiceDep,
    settings: SettingsDep,
) -> SettingsResponse:
    """Update editable preferences (UI overrides env defaults)."""
    payload = data.model_dump(exclude_unset=True)
    previous_match_strictness = store.effective().match_strictness

    # Drop retired preselect/wash keys if a stale client still sends them.
    for key in (
        "preselect_enabled",
        "wash_enabled",
        "preselect_root",
        "preselect_place_mode",
        "preselect_match_mode",
    ):
        payload.pop(key, None)

    if "download_cache_enabled" in payload:
        current = store.effective().download_cache_enabled
        requested = bool(payload["download_cache_enabled"])
        if requested != current and any(
            not job.status.is_finished for job in services.job_store.get_all()
        ):
            raise HTTPException(
                status_code=409,
                detail="Cannot change download cache while jobs are active or queued",
            )
        if requested and (
            not DOWNLOAD_CACHE_ROOT.is_dir()
            or not os.access(DOWNLOAD_CACHE_ROOT, os.W_OK)
        ):
            raise HTTPException(
                status_code=400,
                detail="Download cache is missing or not writable",
            )

    telegram_changed = any(key.startswith("telegram_") for key in payload)
    if payload:
        store.update(**payload)
        if (
            "match_strictness" in payload
            and store.effective().match_strictness != previous_match_strictness
        ):
            # A different matching rule invalidates prior negative decisions.
            services.external_library_service.clear_match_cooldowns(
                include_rejected=True
            )
            configured = (
                services.external_library_service.configured_playlist_names()
            )
            for dir_name in configured:
                await services.scheduler.queue_external_playlist_sync(
                    dir_name,
                    enrich=False,
                    raw_match=True,
                    verify_meta=True,
                    junk_match=False,
                    drain=True,
                )
        if any(
            key in payload
            for key in (
                "scheduler_enabled",
                "scheduler_cron",
                "external_inventory_schedule_enabled",
                "external_inventory_schedule_time",
                "external_library_enabled",
            )
        ):
            services.scheduler.invalidate_plan()
        elif any(
            key in payload
            for key in (
                "wanted_enabled",
                "wanted_auto_match_enabled",
                "wanted_sync_jitter_seconds",
            )
        ):
            services.scheduler.invalidate_wanted_plan()
    if telegram_changed:
        bot = getattr(services, "telegram_bot", None)
        if bot is not None:
            await bot.reload()

    health = library_health.current()
    api_url, running = _telegram_status(services, settings)
    return _to_response(
        store,
        maintenance_locked=services.operation_gate.is_locked,
        library_health_status=health.status,
        library_health_reason=health.reason,
        telegram_api_url=api_url,
        telegram_bot_running=running,
    )


@router.post("/reset", response_model=SettingsResponse)
async def reset_settings(
    store: PreferencesStoreDep,
    services: ServicesDep,
    library_health: LibraryHealthServiceDep,
    settings: SettingsDep,
) -> SettingsResponse:
    """Compatibility endpoint for the level-1 preference reset."""
    store.reset_preferences()
    bot = getattr(services, "telegram_bot", None)
    if bot is not None:
        await bot.reload()
    health = library_health.current()
    api_url, running = _telegram_status(services, settings)
    return _to_response(
        store,
        maintenance_locked=services.operation_gate.is_locked,
        library_health_status=health.status,
        library_health_reason=health.reason,
        telegram_api_url=api_url,
        telegram_bot_running=running,
    )


@router.post(
    "/factory/preview",
    response_model=FactoryResetPreviewResponse,
)
async def preview_factory_reset(
    body: FactoryResetPreviewRequest,
    service: FactoryResetServiceDep,
) -> FactoryResetPreviewResponse:
    preview = await asyncio.to_thread(
        service.preview,
        FactoryResetMode(body.mode),
    )
    return FactoryResetPreviewResponse(**asdict(preview))


@router.post(
    "/factory/execute",
    response_model=FactoryResetResultResponse,
)
async def execute_factory_reset(
    body: FactoryResetExecuteRequest,
    service: FactoryResetServiceDep,
    services: ServicesDep,
) -> FactoryResetResultResponse:
    mode = FactoryResetMode(body.mode)
    try:
        result = await asyncio.to_thread(
            service.execute,
            mode,
            body.token,
            password=body.password,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail="invalid password") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if mode is FactoryResetMode.FULL:
        services.search_service.delete()
        services.telegram_bot.clear_factory_state()
        await services.telegram_bot.reload()
        services.job_store.clear_finished()

    return FactoryResetResultResponse(**asdict(result))


@router.post("/clear-scrape-cooldowns")
def clear_scrape_cooldowns(settings: SettingsDep) -> dict[str, int]:
    """Clear all scrape-state cover/lyrics cooldowns."""
    roots = (settings.data, EXTERNAL_ROOT, WANTED_ROOT)
    cleared = sum(ScrapeStateStore(root).clear_all() for root in roots)
    return {"cleared": cleared}


@router.post("/clear-match-cooldowns")
def clear_match_cooldowns(
    service: ExternalLibraryServiceDep,
    include_rejected: bool = Query(
        default=False,
        description=("When true, also requeue rejected (junk) rows as unmatched."),
    ),
) -> dict[str, int]:
    """Clear external-library match backoff counters."""
    cleared = service.clear_match_cooldowns(include_rejected=include_rejected)
    return {"cleared": cleared}


@router.post("/reclaim-pits")
def reclaim_pits(
    service: ExternalLibraryServiceDep,
    prefs: PreferencesStoreDep,
    target: str = Query(
        ...,
        pattern="^(delete|default|both)$",
        description="Empty Raw/Delete, Organized/Default, or both.",
    ),
) -> dict[str, int]:
    """Empty salvage pits (files + index together; never ledger-only)."""
    if not prefs.effective().external_library_enabled:
        raise HTTPException(
            status_code=400,
            detail="External library is disabled. Enable it in Settings.",
        )
    try:
        result = service.reclaim_special_pit(target)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "deleted_files": result.deleted_files,
        "deleted_raw": result.deleted_raw,
        "deleted_locations": result.deleted_locations,
        "errors": result.errors,
    }
