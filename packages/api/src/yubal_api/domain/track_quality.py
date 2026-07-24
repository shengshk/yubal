"""Derived quality tier for library tracks.

The tier is always *computed* from objective per-track signals and is never
persisted, so it can never drift out of sync with the underlying catalog
facts. See the design notes in the Sync Center feature.

Tiers (matching the product vocabulary):

- ``draft`` / 半成品  — core tags missing OR no embedded cover.
- ``complete`` / 成品 — core tags present + embedded cover, but synced lyrics
  are missing OR the cover comparison is stale / never completed.
- ``premium`` / 优品 — core tags + embedded cover + synced lyrics, and either:
  - cover meets the optional excellence threshold (permanent), or
  - a cover comparison round is still within its shelf life (7d probe / 30d
    download).
"""

from __future__ import annotations

import re
from datetime import datetime

from yubal.utils.cover_quality import (
    PREMIUM_DOWNLOAD_COOLDOWN_DAYS,
    PREMIUM_PROBE_COOLDOWN_DAYS,
    cover_comparison_fresh,
    cover_meets_excellence,
)

TIER_DRAFT = "draft"
TIER_COMPLETE = "complete"
TIER_PREMIUM = "premium"

__all__ = [
    "PREMIUM_DOWNLOAD_COOLDOWN_DAYS",
    "PREMIUM_PROBE_COOLDOWN_DAYS",
    "TIER_COMPLETE",
    "TIER_DRAFT",
    "TIER_PREMIUM",
    "compute_track_tier",
    "cover_comparison_fresh",
    "cover_meets_excellence",
    "lyrics_are_synced",
]

# Standard LRC timestamp: [mm:ss] or [mm:ss.xx] / [mm:ss.xxx]
_LRC_TIMESTAMP_RE = re.compile(r"\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]")


def lyrics_are_synced(lyrics: str | None) -> bool:
    """Return True when the lyrics text contains at least one LRC timestamp."""
    if not lyrics or not lyrics.strip():
        return False
    return _LRC_TIMESTAMP_RE.search(lyrics) is not None


def compute_track_tier(
    *,
    title: str | None,
    artist: str | None,
    has_embedded_cover: bool,
    has_lyrics: bool,
    cover_source: str | None = None,
    has_synced_lyrics: bool | None = None,
    cover_compared_at: datetime | None = None,
    cover_check_kind: str | None = None,
    cover_width: int | None = None,
    cover_height: int | None = None,
    cover_excellence_px: int = 0,
    cover_probe_fresh_days: int | None = None,
    cover_download_fresh_days: int | None = None,
) -> str:
    """Return the derived tier for a single track from its signals.

    ``has_synced_lyrics`` defaults to ``has_lyrics`` when omitted so older
    call sites keep working; pass the real synced flag once available.

    ``cover_source`` is kept for callers/display but no longer gates premium.
    """
    _ = cover_source  # provenance only; premium is quality + freshness
    has_core = bool((title or "").strip()) and bool((artist or "").strip())
    if not has_core or not has_embedded_cover:
        return TIER_DRAFT
    synced = has_lyrics if has_synced_lyrics is None else has_synced_lyrics
    if not synced:
        return TIER_COMPLETE

    if cover_meets_excellence(cover_width, cover_height, cover_excellence_px):
        return TIER_PREMIUM
    if cover_comparison_fresh(
        cover_compared_at,
        cover_check_kind,
        probe_days=cover_probe_fresh_days,
        download_days=cover_download_fresh_days,
    ):
        return TIER_PREMIUM
    return TIER_COMPLETE
