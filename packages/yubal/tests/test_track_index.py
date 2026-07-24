"""Tests for track index rewrite and repair."""

from pathlib import Path

from yubal.services.track_index import repair_track_index, rewrite_track_index_prefix
from yubal.utils.library import track_index_path


def test_rewrite_track_index_prefix(tmp_path: Path) -> None:
    base = tmp_path / "data"
    base.mkdir()
    index_path = track_index_path(base)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        """{
  "abc": "Liked Music/Artist/song.opus",
  "def": "Liked Music/other.opus",
  "ghi": "Direct/keep.opus"
}
""",
        encoding="utf-8",
    )

    updated = rewrite_track_index_prefix(base, "Liked Music", "Liked")
    assert updated == 2

    text = index_path.read_text(encoding="utf-8")
    assert "Liked/Artist/song.opus" in text
    assert "Liked/other.opus" in text
    assert "Direct/keep.opus" in text
    assert "Liked Music" not in text


def test_repair_track_index_by_suffix(tmp_path: Path) -> None:
    base = tmp_path / "data"
    liked = base / "Liked" / "Artist"
    liked.mkdir(parents=True)
    song = liked / "song.opus"
    song.write_bytes(b"x")

    index_path = track_index_path(base)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        '{"vid1": "Liked Music/Artist/song.opus"}',
        encoding="utf-8",
    )

    repaired = repair_track_index(base, save_folders=["Liked"])
    assert repaired == 1

    text = index_path.read_text(encoding="utf-8")
    assert "Liked/Artist/song.opus" in text
