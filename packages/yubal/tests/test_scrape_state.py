"""Tests for scrape cooldown state store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from yubal.services.scrape_state import (
    COVER_APPLE,
    COVER_YTM,
    ScrapeStateStore,
    TrackScrapeState,
)
from yubal.utils.library import TRACK_INDEX_DIR


def _store(tmp_path: Path) -> ScrapeStateStore:
    (tmp_path / TRACK_INDEX_DIR).mkdir(parents=True, exist_ok=True)
    return ScrapeStateStore(tmp_path)


def test_persist_roundtrip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    state = TrackScrapeState(cover_source=COVER_APPLE, has_lyrics=True)
    store.set("abc", state)

    reloaded = ScrapeStateStore(tmp_path)
    got = reloaded.get("abc")
    assert got.cover_source == COVER_APPLE
    assert got.has_lyrics is True


def test_apple_cooldown(tmp_path: Path) -> None:
    store = _store(tmp_path)
    state = TrackScrapeState(
        cover_source=COVER_YTM,
        apple_checked_at=datetime.now(UTC) - timedelta(hours=1),
    )
    assert store.apple_in_cooldown(state, 24) is True
    assert store.apple_in_cooldown(state, 0) is False
    assert store.apple_in_cooldown(state, 1) is False


def test_lyrics_source_cooldown(tmp_path: Path) -> None:
    store = _store(tmp_path)
    state = TrackScrapeState(
        lyrics_checked={"lrclib": datetime.now(UTC) - timedelta(hours=2)}
    )
    assert store.lyrics_source_in_cooldown(state, "lrclib", 24) is True
    assert store.lyrics_source_in_cooldown(state, "qq", 24) is False
    assert store.lyrics_source_in_cooldown(state, "lrclib", 1) is False
