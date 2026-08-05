"""Tests for track catalog display formatting."""

from yubal_api.db.track_catalog_repository import format_track_display


def test_format_when_artists_differ() -> None:
    assert (
        format_track_display("黄霄雲", "群星", "连名带姓 (Live)")
        == "群星 · 黄霄雲 - 连名带姓 (Live)"
    )


def test_format_when_artists_same() -> None:
    assert format_track_display("周杰伦", "周杰伦", "轨迹") == "周杰伦 - 轨迹"


def test_format_missing_artist() -> None:
    assert format_track_display("", "群星", "歌") == "群星 - 歌"
