"""Persistent per-video_id scrape state (cover source + API cooldowns)."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from yubal.utils.library import runtime_state_path

logger = logging.getLogger(__name__)

COVER_APPLE = "apple"
COVER_YTM = "ytm"
COVER_EMBEDDED = "embedded"

COVER_CHECK_PROBE = "probe"
COVER_CHECK_DOWNLOAD = "download"


def scrape_state_path(base_path: Path) -> Path:
    return runtime_state_path(base_path, "scrape_state.json")


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _dump_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


@dataclass
class TrackScrapeState:
    cover_source: str | None = None
    apple_checked_at: datetime | None = None
    # Last completed cover comparison round (probe or full download).
    cover_compared_at: datetime | None = None
    cover_check_kind: str | None = None  # probe | download
    cover_width: int | None = None
    cover_height: int | None = None
    lyrics_checked: dict[str, datetime] = field(default_factory=dict)
    has_lyrics: bool = False
    # Provider that produced the currently stored lyrics (lrclib | ytm | qq).
    lyrics_source: str | None = None

    def effective_compared_at(self) -> datetime | None:
        """Prefer new field; fall back to legacy apple_checked_at."""
        return self.cover_compared_at or self.apple_checked_at

    def effective_check_kind(self) -> str | None:
        if self.cover_check_kind in {COVER_CHECK_PROBE, COVER_CHECK_DOWNLOAD}:
            return self.cover_check_kind
        # Legacy rows only recorded apple checks after a full image fetch.
        if self.apple_checked_at is not None or self.cover_source:
            return COVER_CHECK_DOWNLOAD
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "cover_source": self.cover_source,
            "apple_checked_at": _dump_dt(self.apple_checked_at),
            "cover_compared_at": _dump_dt(self.cover_compared_at),
            "cover_check_kind": self.cover_check_kind,
            "cover_width": self.cover_width,
            "cover_height": self.cover_height,
            "lyrics_checked": {
                k: _dump_dt(v) for k, v in self.lyrics_checked.items() if v
            },
            "has_lyrics": self.has_lyrics,
            "lyrics_source": self.lyrics_source,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> TrackScrapeState:
        checked_raw = raw.get("lyrics_checked") or {}
        lyrics_checked: dict[str, datetime] = {}
        if isinstance(checked_raw, dict):
            for key, val in checked_raw.items():
                dt = _parse_dt(val)
                if dt is not None:
                    lyrics_checked[str(key)] = dt

        def _int_or_none(value: Any) -> int | None:
            if value is None or value == "":
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        kind = raw.get("cover_check_kind")
        kind_s = str(kind).strip().lower() if kind else None
        if kind_s not in {COVER_CHECK_PROBE, COVER_CHECK_DOWNLOAD}:
            kind_s = None

        return cls(
            cover_source=str(raw["cover_source"]) if raw.get("cover_source") else None,
            apple_checked_at=_parse_dt(raw.get("apple_checked_at")),
            cover_compared_at=_parse_dt(raw.get("cover_compared_at")),
            cover_check_kind=kind_s,
            cover_width=_int_or_none(raw.get("cover_width")),
            cover_height=_int_or_none(raw.get("cover_height")),
            lyrics_checked=lyrics_checked,
            has_lyrics=bool(raw.get("has_lyrics")),
            lyrics_source=str(raw["lyrics_source"])
            if raw.get("lyrics_source")
            else None,
        )


class ScrapeStateStore:
    """JSON store: video_id → scrape state."""

    def __init__(self, base_path: Path) -> None:
        self._path = scrape_state_path(base_path)
        self._lock = threading.Lock()
        self._data: dict[str, TrackScrapeState] = {}
        self._load()

    def get(self, video_id: str) -> TrackScrapeState:
        with self._lock:
            state = self._data.get(video_id)
            if state is None:
                return TrackScrapeState()
            # Return a copy so callers can mutate then save
            return TrackScrapeState(
                cover_source=state.cover_source,
                apple_checked_at=state.apple_checked_at,
                cover_compared_at=state.cover_compared_at,
                cover_check_kind=state.cover_check_kind,
                cover_width=state.cover_width,
                cover_height=state.cover_height,
                lyrics_checked=dict(state.lyrics_checked),
                has_lyrics=state.has_lyrics,
                lyrics_source=state.lyrics_source,
            )

    def set(self, video_id: str, state: TrackScrapeState) -> None:
        if not video_id:
            return
        with self._lock:
            self._data[video_id] = state
            self._save_unlocked()

    def apple_in_cooldown(self, state: TrackScrapeState, cooldown_hours: int) -> bool:
        """Legacy helper: hours-based Apple miss cooldown (lyrics-era)."""
        if cooldown_hours <= 0 or state.apple_checked_at is None:
            return False
        return datetime.now(UTC) - state.apple_checked_at < timedelta(
            hours=cooldown_hours
        )

    def lyrics_source_in_cooldown(
        self,
        state: TrackScrapeState,
        source: str,
        cooldown_hours: int,
    ) -> bool:
        if cooldown_hours <= 0:
            return False
        checked = state.lyrics_checked.get(source)
        if checked is None:
            return False
        return datetime.now(UTC) - checked < timedelta(hours=cooldown_hours)

    def clear_all(self) -> int:
        """Drop all scrape-state entries (cover/lyrics cooldowns). Returns count."""
        with self._lock:
            cleared = len(self._data)
            if cleared:
                self._data.clear()
                self._save_unlocked()
            return cleared

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to read scrape state: %s", e)
            return
        if not isinstance(raw, dict):
            return
        for video_id, entry in raw.items():
            if isinstance(entry, dict):
                self._data[str(video_id)] = TrackScrapeState.from_json(entry)

    def _save_unlocked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {vid: st.to_json() for vid, st in sorted(self._data.items())}
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self._path)
