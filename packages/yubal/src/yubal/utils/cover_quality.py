"""Cover excellence threshold and premium shelf-life helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

# Shelf life after a completed cover comparison round.
PREMIUM_PROBE_COOLDOWN_DAYS = 7
PREMIUM_DOWNLOAD_COOLDOWN_DAYS = 30

COVER_CHECK_PROBE = "probe"
COVER_CHECK_DOWNLOAD = "download"


def cover_meets_excellence(
    width: int | None,
    height: int | None,
    excellence_px: int,
) -> bool:
    """True when both edges are at least ``excellence_px`` (0 disables)."""
    if excellence_px <= 0:
        return False
    if width is None or height is None:
        return False
    try:
        w, h = int(width), int(height)
    except (TypeError, ValueError):
        return False
    return w > 0 and h > 0 and min(w, h) >= excellence_px


def cover_comparison_fresh(
    compared_at: datetime | None,
    check_kind: str | None,
    *,
    now: datetime | None = None,
    probe_days: int | None = None,
    download_days: int | None = None,
) -> bool:
    """True when a prior comparison round is still within its shelf life."""
    if compared_at is None:
        return False
    if compared_at.tzinfo is None:
        compared_at = compared_at.replace(tzinfo=UTC)
    if (check_kind or "") == COVER_CHECK_DOWNLOAD:
        days = (
            download_days
            if download_days is not None
            else PREMIUM_DOWNLOAD_COOLDOWN_DAYS
        )
    else:
        days = (
            probe_days if probe_days is not None else PREMIUM_PROBE_COOLDOWN_DAYS
        )
    current = now or datetime.now(UTC)
    return current - compared_at < timedelta(days=max(0, int(days)))
