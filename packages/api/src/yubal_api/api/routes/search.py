"""Online song search, local matching, and temporary previews."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from yubal_api.api.deps import SearchServiceDep, SettingsDep
from yubal_api.schemas.search import (
    SearchDownloadResponse,
    SearchLyricsResponse,
    SearchPreviewResponse,
    SearchRequest,
    SearchSnapshotResponse,
)

router = APIRouter(prefix="/search", tags=["search"])

_MEDIA_TYPES = {
    ".webm": "audio/webm",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".opus": "audio/ogg",
    ".ogg": "audio/ogg",
    ".aac": "audio/aac",
}


@router.get("", response_model=SearchSnapshotResponse | None)
def get_search_results(search: SearchServiceDep) -> SearchSnapshotResponse | None:
    return search.current()


@router.post("", response_model=SearchSnapshotResponse)
async def run_search(
    body: SearchRequest,
    search: SearchServiceDep,
) -> SearchSnapshotResponse:
    try:
        result = await asyncio.to_thread(search.search, body.query)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="No song results found")
    return result


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_search_results(search: SearchServiceDep) -> None:
    search.delete()


@router.post(
    "/preview/{video_id}",
    response_model=SearchPreviewResponse,
)
async def prepare_search_preview(
    video_id: str,
    search: SearchServiceDep,
    settings: SettingsDep,
) -> SearchPreviewResponse:
    try:
        await asyncio.to_thread(search.prepare_preview, video_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        message = str(exc)
        if "unavailable" in message.lower():
            detail = "This track is unavailable on YouTube (removed or region-locked)."
        else:
            detail = f"Preview failed: {message}"
        raise HTTPException(status_code=502, detail=detail) from exc
    return SearchPreviewResponse(
        video_id=video_id,
        url=f"{settings.base_path}/api/search/preview/{video_id}/file",
    )


@router.post(
    "/download/{video_id}",
    response_model=SearchDownloadResponse,
)
async def download_search_preview(
    video_id: str,
    search: SearchServiceDep,
) -> SearchDownloadResponse:
    """Import a cached preview into Direct without re-downloading."""
    try:
        snapshot = await asyncio.to_thread(search.promote_preview, video_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    track = next((t for t in snapshot.tracks if t.video_id == video_id), None)
    if track is None or not track.local_path:
        raise HTTPException(status_code=502, detail="Import succeeded but path missing")
    return SearchDownloadResponse(
        video_id=video_id,
        local_path=track.local_path,
        snapshot=snapshot,
    )


@router.get("/lyrics/{video_id}", response_model=SearchLyricsResponse)
async def get_search_lyrics(
    video_id: str,
    search: SearchServiceDep,
) -> SearchLyricsResponse:
    """Resolve lyrics on demand for a previewed (not-yet-imported) result."""
    result = await asyncio.to_thread(search.preview_lyrics, video_id)
    if result is None:
        return SearchLyricsResponse(available=False, content=None, source=None)
    content, source = result
    return SearchLyricsResponse(available=True, content=content, source=source)


@router.get("/preview/{video_id}/file")
def stream_search_preview(
    video_id: str,
    search: SearchServiceDep,
) -> FileResponse:
    snapshot = search.current()
    if snapshot is None or not any(t.video_id == video_id for t in snapshot.tracks):
        raise HTTPException(status_code=404, detail="search result not found")
    path = search.preview_file(video_id)
    if path is None:
        raise HTTPException(status_code=404, detail="preview not prepared")
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        filename=path.name,
        content_disposition_type="inline",
    )
