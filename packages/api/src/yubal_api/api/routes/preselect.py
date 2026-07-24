"""Preselect library endpoints."""

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from yubal_api.api.deps import PreselectServiceDep, WashServiceDep

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/preselect", tags=["preselect"])


class PreselectStatusResponse(BaseModel):
    running: bool
    phase: str
    scanned: int
    added: int
    updated: int
    removed: int
    errors: int
    total_indexed: int
    last_error: str | None = None
    finished_at: str | None = None
    hardlink_ok: bool | None = None


class PreselectScanRequest(BaseModel):
    force_all: bool = Field(default=False)


class WashResultResponse(BaseModel):
    checked: int
    upgraded: int
    skipped: int
    errors: int


def _to_status(service) -> PreselectStatusResponse:
    st = service.status()
    return PreselectStatusResponse(
        running=st.running,
        phase=st.phase,
        scanned=st.scanned,
        added=st.added,
        updated=st.updated,
        removed=st.removed,
        errors=st.errors,
        total_indexed=st.total_indexed,
        last_error=st.last_error,
        finished_at=st.finished_at.isoformat() if st.finished_at else None,
        hardlink_ok=service.hardlink_supported(),
    )


@router.get("/status", response_model=PreselectStatusResponse)
def preselect_status(service: PreselectServiceDep) -> PreselectStatusResponse:
    return _to_status(service)


@router.post("/scan", response_model=PreselectStatusResponse)
async def preselect_scan(
    service: PreselectServiceDep,
    body: PreselectScanRequest | None = None,
) -> PreselectStatusResponse:
    """Scan External in a worker thread; returns when finished (empty dirs are instant)."""
    force_all = bool(body.force_all) if body else False
    st = service.status()
    if st.running:
        raise HTTPException(status_code=409, detail="preselect scan already running")
    if not service.root_configured():
        raise HTTPException(
            status_code=400,
            detail="External mount missing (/External)",
        )
    try:
        await asyncio.to_thread(service.scan_incremental, force_all=force_all)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except Exception:
        logger.exception("Preselect scan failed")
        raise HTTPException(status_code=500, detail="preselect scan failed") from None
    return _to_status(service)


@router.post("/wash", response_model=WashResultResponse)
def preselect_wash(wash: WashServiceDep) -> WashResultResponse:
    """Run one wash pass over the download library (synchronous)."""
    ok, err = wash.ready()
    if not ok:
        raise HTTPException(status_code=400, detail=err or "wash not ready")
    result = wash.run()
    return WashResultResponse(
        checked=result.checked,
        upgraded=result.upgraded,
        skipped=result.skipped,
        errors=result.errors,
    )
