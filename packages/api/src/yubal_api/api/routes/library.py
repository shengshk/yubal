"""Library folder listing / creation under the data root."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from yubal.services.track_index import repair_track_index, rewrite_track_index_prefix
from yubal.utils.audio_assets import (
    read_embedded_cover,
    read_embedded_lyrics,
    read_lyrics_sidecar,
    write_embedded_lyrics,
    write_lyrics_sidecar,
)
from yubal.utils.cover import find_album_folder_cover, find_playlist_folder_cover
from yubal.utils.filename import _limit_path_component, clean_filename
from yubal.utils.library import (
    AUDIO_SUFFIXES,
    STORAGE_EXTERNAL,
    STORAGE_WANTED,
    WANTED_ROOT,
    assert_folder_depth,
    delete_empty_library_folder,
    is_empty_library_folder,
    list_library_folder_options,
    resolve_storage_path,
    resolve_under_data,
    sanitize_save_folder,
)

from yubal_api.api.deps import (
    LibraryHealthServiceDep,
    LibraryLookupServiceDep,
    LibraryStatsServiceDep,
    PreferencesStoreDep,
    SettingsDep,
    SubscriptionServiceDep,
    SyncLedgerServiceDep,
    TrackMetadataServiceDep,
    TrackRetagServiceDep,
)
from yubal_api.schemas.library_lookup import (
    PlaylistPresenceResponse,
    TextPresenceResponse,
    TrackPresenceResponse,
)
from yubal_api.schemas.track_metadata import (
    MetadataResolveRequest,
    MetadataSearchRequest,
    MetadataSearchResponse,
    MetadataSuggestion,
)
from yubal_api.schemas.tracks import TrackTagUpdate, TrackTagUpdateResponse
from yubal_api.services.track_retag_service import TrackRetagConflictError

router = APIRouter(prefix="/library", tags=["library"])

_MEDIA_TYPES = {
    ".opus": "audio/ogg",
    ".ogg": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}

_IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


class LibraryFoldersResponse(BaseModel):
    items: list[str]
    direct_folder: str
    subscription_folders: list[str] = Field(
        default_factory=list,
        description="Exact save_folder paths used by subscriptions",
    )
    empty_folders: list[str] = Field(
        default_factory=list,
        description="Unused empty folders (rename/delete allowed)",
    )
    shared_folders: dict[str, int] = Field(
        default_factory=dict,
        description="save_folder → subscription count (count>1 means shared)",
    )


class CreateFolderRequest(BaseModel):
    path: str = Field(min_length=1, max_length=400)


class CreateFolderResponse(BaseModel):
    path: str


class RenameFolderRequest(BaseModel):
    path: str = Field(min_length=1, max_length=400)
    new_name: str = Field(min_length=1, max_length=200)


class TrackLyricsResponse(BaseModel):
    available: bool
    content: str | None = None
    source: str | None = Field(
        default=None,
        description="lrclib | ytm | qq | manual | db | sidecar | embedded",
    )


class SaveTrackLyricsRequest(BaseModel):
    path: str = Field(min_length=1, max_length=800)
    content: str = Field(max_length=500_000)


class SaveTrackLyricsResponse(BaseModel):
    ok: bool
    sidecar: bool = False
    embedded: bool = False
    catalog: bool = False
    errors: list[str] = Field(default_factory=list)


def _resolve_audio_file(data_path: Path, path: str) -> Path:
    rel = path.strip().replace("\\", "/")
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        raise HTTPException(status_code=400, detail="invalid path")
    try:
        # External library paths are rooted at /External (Organized/… or Raw/…).
        if rel.startswith("External/"):
            rel = rel[len("External/") :]
        if rel.startswith("organized/") or rel.startswith("raw/"):
            abs_path = resolve_storage_path(STORAGE_EXTERNAL, rel)
        elif rel.startswith("wanted/"):
            abs_path = resolve_storage_path(STORAGE_WANTED, rel[len("wanted/") :])
        else:
            abs_path = resolve_under_data(data_path, rel)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if abs_path.suffix.lower() not in AUDIO_SUFFIXES:
        raise HTTPException(status_code=400, detail="not an audio file")
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return abs_path


def _resolve_library_dir(data_path: Path, folder: str) -> Path:
    rel = folder.strip().replace("\\", "/").strip("/")
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        raise HTTPException(status_code=400, detail="invalid folder")
    try:
        # External playlist cards pass ``External/<dir_name>``; covers live under
        # Organized/<dir_name> on the External root (not under Download).
        if rel.startswith("External/"):
            rest = rel[len("External/") :]
            if not (
                rest.startswith("organized/") or rest.startswith("raw/")
            ):
                rest = f"organized/{rest}"
            abs_path = resolve_storage_path(STORAGE_EXTERNAL, rest)
        elif rel == "wanted":
            abs_path = WANTED_ROOT
        elif rel.startswith("wanted/"):
            abs_path = resolve_storage_path(STORAGE_WANTED, rel[len("wanted/") :])
        else:
            abs_path = resolve_under_data(data_path, rel)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not abs_path.is_dir():
        raise HTTPException(status_code=404, detail="folder not found")
    return abs_path


def _locked_paths(
    usage: dict[str, int],
    direct_folder: str,
) -> set[str]:
    locked = set(usage.keys())
    locked.add(direct_folder)
    return locked


def _is_locked_or_ancestor(path: str, locked: set[str]) -> bool:
    if path in locked:
        return True
    prefix = f"{path}/"
    return any(item.startswith(prefix) for item in locked)


def _assert_manageable(
    relative: str,
    *,
    data_path: Path,
    locked: set[str],
) -> Path:
    if not relative or relative in {".", "/"}:
        raise HTTPException(status_code=400, detail="Cannot modify library root")
    if _is_locked_or_ancestor(relative, locked):
        raise HTTPException(
            status_code=409,
            detail="Folder is in use by a subscription or Direct",
        )
    abs_path = resolve_under_data(data_path, relative)
    if not abs_path.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")
    if not is_empty_library_folder(abs_path):
        raise HTTPException(status_code=409, detail="Folder is not empty")
    return abs_path


class LibraryHealthResponse(BaseModel):
    ok: bool
    status: str
    reason: str | None = None
    same_filesystem: bool
    download_sentinel_ok: bool
    external_sentinel_ok: bool
    last_good_raw_count: int
    last_check_at: str | None = None


class LibraryTrackSummaryResponse(BaseModel):
    effective_count: int
    identified_count: int
    unidentified_count: int
    verified_count: int
    unverified_count: int
    physical_count: int
    hardlink_duplicate_count: int


class LibraryAuditResponse(BaseModel):
    ok: bool
    physical_count: int
    hardlink_duplicate_count: int
    catalog_location_count: int
    missing_catalog_locations: int
    repaired_catalog_locations: int
    repaired_index_entries: int
    untracked_physical_count: int


@router.get("/health", response_model=LibraryHealthResponse)
def library_health(library_health: LibraryHealthServiceDep) -> LibraryHealthResponse:
    """Download/External mount health used to gate jobs, scheduler, and matching."""
    health = library_health.check()
    return LibraryHealthResponse(
        ok=health.ok,
        status=health.status,
        reason=health.reason,
        same_filesystem=health.same_filesystem,
        download_sentinel_ok=health.download_sentinel_ok,
        external_sentinel_ok=health.external_sentinel_ok,
        last_good_raw_count=health.last_good_raw_count,
        last_check_at=(
            health.last_check_at.isoformat() if health.last_check_at else None
        ),
    )


@router.post("/audit", response_model=LibraryAuditResponse)
def audit_library(
    library_health: LibraryHealthServiceDep,
    library_stats: LibraryStatsServiceDep,
    settings: SettingsDep,
    subscriptions: SubscriptionServiceDep,
    repair: bool = Query(default=False),
) -> LibraryAuditResponse:
    """Audit physical files/catalog rows; optional repair only removes stale refs."""
    library_health.ensure_healthy()
    audit = library_stats.audit(repair_missing=repair)
    repaired_index = 0
    if repair:
        save_folders = [
            subscription.save_folder or subscription.name
            for subscription in subscriptions.list()
        ]
        repaired_index = repair_track_index(
            settings.data,
            save_folders=save_folders,
        )
    return LibraryAuditResponse(
        ok=audit.ok,
        physical_count=audit.physical_count,
        hardlink_duplicate_count=audit.hardlink_duplicate_count,
        catalog_location_count=audit.catalog_location_count,
        missing_catalog_locations=audit.missing_catalog_locations,
        repaired_catalog_locations=audit.repaired_catalog_locations,
        repaired_index_entries=repaired_index,
        untracked_physical_count=audit.untracked_physical_count,
    )


@router.get("/track-summary", response_model=LibraryTrackSummaryResponse)
def library_track_summary(
    library_stats: LibraryStatsServiceDep,
) -> LibraryTrackSummaryResponse:
    """Count real audio files once per inode across the whole local library."""
    return LibraryTrackSummaryResponse.model_validate(
        library_stats.summary(), from_attributes=True
    )


@router.get("/file")
def stream_library_file(
    settings: SettingsDep,
    path: str = Query(min_length=1, max_length=800),
) -> FileResponse:
    """Stream an audio file under the data root (cookie auth required)."""
    abs_path = _resolve_audio_file(settings.data, path)
    media = _MEDIA_TYPES.get(abs_path.suffix.lower(), "application/octet-stream")
    return FileResponse(
        abs_path,
        media_type=media,
        filename=abs_path.name,
        content_disposition_type="inline",
    )


@router.get("/track-cover")
def stream_track_cover(
    settings: SettingsDep,
    path: str = Query(min_length=1, max_length=800),
) -> Response:
    """Return embedded cover art from an audio file, if present.

    Missing art returns 204 (not 404) so ``<img>`` fallback chains do not
    flood the browser console with failed GET errors.
    """
    abs_path = _resolve_audio_file(settings.data, path)
    cover = read_embedded_cover(abs_path)
    if cover is None:
        return Response(status_code=204)
    data, mime = cover
    return Response(content=data, media_type=mime)


@router.get("/playlist-cover")
def stream_playlist_cover(
    settings: SettingsDep,
    folder: str = Query(min_length=1, max_length=400),
) -> Response:
    """Serve a playlist sidecar cover from a library folder.

    Missing folder or cover → 204 (not 404) so ``<img>`` fallback chains do not
    flood the browser console.
    """
    try:
        abs_dir = _resolve_library_dir(settings.data, folder)
    except HTTPException as e:
        if e.status_code == 404:
            return Response(status_code=204)
        raise
    cover_path = find_playlist_folder_cover(abs_dir)
    if cover_path is None:
        return Response(status_code=204)
    media = _IMAGE_MEDIA_TYPES.get(
        cover_path.suffix.lower(), "application/octet-stream"
    )
    return FileResponse(
        cover_path,
        media_type=media,
        filename=cover_path.name,
        content_disposition_type="inline",
    )


@router.get("/wanted-cover")
def stream_wanted_cover(
    matched: bool = Query(default=False),
) -> Response:
    """Default wishlist cover: empty (no hardlinks) vs matched (has files)."""
    name = "cover-matched.jpg" if matched else "cover-empty.jpg"
    cover_path = WANTED_ROOT / name
    if not cover_path.is_file():
        return Response(status_code=204)
    media = _IMAGE_MEDIA_TYPES.get(cover_path.suffix.lower(), "image/jpeg")
    return FileResponse(
        cover_path,
        media_type=media,
        filename=cover_path.name,
        content_disposition_type="inline",
    )


@router.get("/album-cover")
def stream_album_cover(
    settings: SettingsDep,
    path: str = Query(min_length=1, max_length=800),
) -> Response:
    """Serve album-folder cover art beside an audio file.

    Missing cover → 204 (not 404); see ``stream_track_cover``.
    """
    abs_path = _resolve_audio_file(settings.data, path)
    cover_path = find_album_folder_cover(abs_path)
    if cover_path is None:
        return Response(status_code=204)
    media = _IMAGE_MEDIA_TYPES.get(
        cover_path.suffix.lower(), "application/octet-stream"
    )
    return FileResponse(
        cover_path,
        media_type=media,
        filename=cover_path.name,
        content_disposition_type="inline",
    )


@router.get("/track-lyrics", response_model=TrackLyricsResponse)
def get_track_lyrics(
    settings: SettingsDep,
    sync_ledger: SyncLedgerServiceDep,
    path: str = Query(min_length=1, max_length=800),
) -> TrackLyricsResponse:
    """Resolve lyrics: catalog DB → sibling ``.lrc`` → embedded tags."""
    abs_path = _resolve_audio_file(settings.data, path)
    rel = path.strip().replace("\\", "/").lstrip("/")

    catalog = sync_ledger.get_catalog_lyrics(rel)
    if catalog:
        # Prefer the recorded provider (lrclib/ytm/qq/manual) over the generic
        # "db" location so the edit modal shows a meaningful source.
        provider = sync_ledger.get_catalog_lyrics_source(rel)
        return TrackLyricsResponse(
            available=True, content=catalog, source=provider or "db"
        )

    sidecar = read_lyrics_sidecar(abs_path)
    if sidecar:
        return TrackLyricsResponse(
            available=True, content=sidecar, source="sidecar"
        )

    embedded = read_embedded_lyrics(abs_path)
    if embedded:
        return TrackLyricsResponse(
            available=True, content=embedded, source="embedded"
        )

    return TrackLyricsResponse(available=False, content=None, source=None)


@router.put("/track-lyrics", response_model=SaveTrackLyricsResponse)
def save_track_lyrics(
    body: SaveTrackLyricsRequest,
    settings: SettingsDep,
    sync_ledger: SyncLedgerServiceDep,
) -> SaveTrackLyricsResponse:
    """Best-effort write lyrics to ``.lrc``, embedded tags, and catalog DB."""
    abs_path = _resolve_audio_file(settings.data, body.path)
    rel = body.path.strip().replace("\\", "/").lstrip("/")
    content = body.content.strip()
    errors: list[str] = []
    sidecar_ok = False
    embedded_ok = False
    catalog_ok = False

    try:
        write_lyrics_sidecar(abs_path, content)
        sidecar_ok = True
    except OSError as e:
        errors.append(f"sidecar: {e}")

    try:
        write_embedded_lyrics(abs_path, content)
        embedded_ok = True
    except Exception as e:
        errors.append(f"embedded: {e}")

    try:
        catalog_ok = bool(sync_ledger.set_catalog_lyrics(rel, content))
        if not catalog_ok:
            # No catalog row is fine for Direct-only files.
            pass
    except Exception as e:
        errors.append(f"catalog: {e}")
        catalog_ok = False

    ok = sidecar_ok or embedded_ok or catalog_ok
    if not ok and not errors:
        errors.append("nothing written")
    return SaveTrackLyricsResponse(
        ok=ok,
        sidecar=sidecar_ok,
        embedded=embedded_ok,
        catalog=catalog_ok,
        errors=errors,
    )


@router.patch("/tracks/{video_id}", response_model=TrackTagUpdateResponse)
def update_track_tags(
    video_id: str,
    body: TrackTagUpdate,
    retag: TrackRetagServiceDep,
) -> TrackTagUpdateResponse:
    """Edit music tags and relocate files to match the updated metadata."""
    if not body.model_dump(exclude_unset=True):
        raise HTTPException(status_code=400, detail="no fields to update")
    try:
        return retag.update_tags(video_id, body)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except TrackRetagConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post(
    "/tracks/{video_id}/metadata/search",
    response_model=MetadataSearchResponse,
)
async def search_track_metadata(
    video_id: str,
    body: MetadataSearchRequest,
    meta: TrackMetadataServiceDep,
) -> MetadataSearchResponse:
    """Search YouTube Music for scrape candidates (does not touch global search)."""
    try:
        return await asyncio.to_thread(meta.search, video_id, body.query)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post(
    "/tracks/{video_id}/metadata/resolve",
    response_model=MetadataSuggestion,
)
async def resolve_track_metadata(
    video_id: str,
    body: MetadataResolveRequest,
    meta: TrackMetadataServiceDep,
) -> MetadataSuggestion:
    """Enrich a candidate into full tag / cover / lyrics suggestions."""
    try:
        return await asyncio.to_thread(
            meta.resolve,
            video_id,
            body.candidate_video_id,
            fetch_lyrics=body.fetch_lyrics,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/folders", response_model=LibraryFoldersResponse)
def list_folders(
    settings: SettingsDep,
    preferences: PreferencesStoreDep,
    subscriptions: SubscriptionServiceDep,
) -> LibraryFoldersResponse:
    """List existing relative folders under data + usage counts."""
    prefs = preferences.effective()
    items = list_library_folder_options(settings.data)
    usage = subscriptions.folders_in_use()
    for folder in usage:
        parts = folder.split("/")
        for i in range(1, len(parts) + 1):
            items.append("/".join(parts[:i]))
    direct = prefs.direct_folder
    parts = direct.split("/")
    for i in range(1, len(parts) + 1):
        items.append("/".join(parts[:i]))
    items = sorted(set(items), key=lambda s: s.lower())

    locked = _locked_paths(usage, direct)
    empty: list[str] = []
    for rel in items:
        if _is_locked_or_ancestor(rel, locked):
            continue
        try:
            abs_path = resolve_under_data(settings.data, rel)
        except ValueError:
            continue
        if is_empty_library_folder(abs_path):
            empty.append(rel)

    return LibraryFoldersResponse(
        items=items,
        direct_folder=prefs.direct_folder,
        subscription_folders=sorted(usage.keys(), key=lambda s: s.lower()),
        empty_folders=empty,
        shared_folders={k: v for k, v in usage.items() if v > 1},
    )


@router.post("/folders", response_model=CreateFolderResponse)
def create_folder(
    data: CreateFolderRequest,
    settings: SettingsDep,
) -> CreateFolderResponse:
    """Create a relative folder under the data root."""
    try:
        safe = sanitize_save_folder(data.path)
        assert_folder_depth(safe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    path = resolve_under_data(settings.data, safe)
    path.mkdir(parents=True, exist_ok=True)
    return CreateFolderResponse(path=safe)


@router.patch("/folders", response_model=CreateFolderResponse)
def rename_folder(
    data: RenameFolderRequest,
    settings: SettingsDep,
    preferences: PreferencesStoreDep,
    subscriptions: SubscriptionServiceDep,
) -> CreateFolderResponse:
    """Rename an unused empty folder (last path segment only)."""
    prefs = preferences.effective()
    locked = _locked_paths(subscriptions.folders_in_use(), prefs.direct_folder)
    try:
        old_rel = sanitize_save_folder(data.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    abs_old = _assert_manageable(
        old_rel, data_path=settings.data, locked=locked
    )

    segment = clean_filename(data.new_name.strip())
    segment = _limit_path_component(segment.strip()) if segment else ""
    if not segment or "/" in segment or "\\" in segment or segment in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid folder name")

    parent = old_rel.rsplit("/", 1)[0] if "/" in old_rel else ""
    new_rel = f"{parent}/{segment}" if parent else segment
    try:
        assert_folder_depth(new_rel)
        new_rel = sanitize_save_folder(new_rel)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if new_rel == old_rel:
        return CreateFolderResponse(path=old_rel)

    abs_new = resolve_under_data(settings.data, new_rel)
    if abs_new.exists():
        raise HTTPException(status_code=409, detail="Target folder already exists")

    shutil.move(str(abs_old), str(abs_new))
    rewrite_track_index_prefix(settings.data, old_rel, new_rel)
    return CreateFolderResponse(path=new_rel)


@router.delete("/folders", status_code=204)
def delete_folder(
    settings: SettingsDep,
    preferences: PreferencesStoreDep,
    subscriptions: SubscriptionServiceDep,
    path: str = Query(min_length=1, max_length=400),
) -> None:
    """Delete an unused empty folder tree."""
    prefs = preferences.effective()
    locked = _locked_paths(subscriptions.folders_in_use(), prefs.direct_folder)
    try:
        rel = sanitize_save_folder(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    abs_path = _assert_manageable(rel, data_path=settings.data, locked=locked)
    try:
        delete_empty_library_folder(abs_path)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/lookup/track", response_model=TrackPresenceResponse)
def lookup_track_presence(
    service: LibraryLookupServiceDep,
    video_id: str = Query(min_length=1, max_length=32),
) -> TrackPresenceResponse:
    """Check whether a single track already exists in Direct / other playlists."""
    return service.lookup_track(video_id)


@router.get("/lookup/playlist", response_model=PlaylistPresenceResponse)
def lookup_playlist_presence(
    service: LibraryLookupServiceDep,
    url: str = Query(min_length=1, max_length=2048),
) -> PlaylistPresenceResponse:
    """Check whether a playlist URL is already subscribed (or last Direct job)."""
    return service.lookup_playlist(url)


@router.get("/lookup/text", response_model=TextPresenceResponse)
def lookup_text_presence(
    service: LibraryLookupServiceDep,
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
) -> TextPresenceResponse:
    """Fuzzy-match local catalog tracks for Enter-key quick actions."""
    return service.lookup_text(q, limit=limit)
