"""Runtime-state placement and legacy migration."""

from __future__ import annotations

from pathlib import Path

import pytest
import yubal.utils.library as library
from yubal.services.scrape_state import ScrapeStateStore, TrackScrapeState


def _configure_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    media = tmp_path / "media"
    download = media / "download"
    external = media / "external"
    wanted = media / "wanted"
    for root in (download, external, wanted):
        root.mkdir(parents=True)
    state_root = tmp_path / "config" / "state"
    monkeypatch.setenv("YUBAL_STATE_ROOT", str(state_root))
    monkeypatch.setattr(
        library,
        "STORAGE_ROOTS",
        {
            library.STORAGE_DOWNLOAD: download,
            library.STORAGE_EXTERNAL: external,
            library.STORAGE_WANTED: wanted,
        },
    )
    return download, state_root


def test_track_index_moves_from_media_to_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    download, state_root = _configure_roots(tmp_path, monkeypatch)
    legacy = download / library.TRACK_INDEX_DIR / "track_index.json"
    legacy.parent.mkdir()
    legacy.write_text('{"video": "download:direct/song.mp3"}', encoding="utf-8")

    target = library.track_index_path(download)

    assert target == state_root / "download" / "track_index.json"
    assert target.read_text(encoding="utf-8") == (
        '{"video": "download:direct/song.mp3"}'
    )
    assert not legacy.exists()


def test_scrape_state_moves_and_writes_under_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    download, state_root = _configure_roots(tmp_path, monkeypatch)
    legacy = download / library.TRACK_INDEX_DIR / "scrape_state.json"
    legacy.parent.mkdir()
    legacy.write_text(
        '{"old": {"cover_source": "ytm", "has_lyrics": false}}',
        encoding="utf-8",
    )

    store = ScrapeStateStore(download)
    store.set("new", TrackScrapeState(has_lyrics=True))

    target = state_root / "download" / "scrape_state.json"
    assert target.is_file()
    assert store.get("old").cover_source == "ytm"
    assert store.get("new").has_lyrics is True
    assert not legacy.exists()


def test_explicit_legacy_marker_moves_to_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    download, state_root = _configure_roots(tmp_path, monkeypatch)
    legacy = download / ".last_sync.json"
    legacy.write_text('{"status": "completed"}', encoding="utf-8")

    target = library.runtime_state_path(
        download,
        "last_sync.json",
        legacy_path=legacy,
    )

    assert target == state_root / "download" / "last_sync.json"
    assert target.read_text(encoding="utf-8") == '{"status": "completed"}'
    assert not legacy.exists()


def test_unknown_paths_keep_colocated_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _download, _state_root = _configure_roots(tmp_path, monkeypatch)
    preview = tmp_path / "preview"
    preview.mkdir()

    assert library.runtime_state_path(preview, "track_index.json") == (
        preview / library.TRACK_INDEX_DIR / "track_index.json"
    )


def test_migration_failure_falls_back_to_legacy_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    download, _state_root = _configure_roots(tmp_path, monkeypatch)
    legacy = download / library.TRACK_INDEX_DIR / "scrape_state.json"
    legacy.parent.mkdir()
    legacy.write_text('{"safe": {}}', encoding="utf-8")

    def fail_copy(_source: Path, _target: Path) -> None:
        raise OSError("copy failed")

    monkeypatch.setattr(library.shutil, "copy2", fail_copy)

    assert library.runtime_state_path(download, "scrape_state.json") == legacy
    assert legacy.read_text(encoding="utf-8") == '{"safe": {}}'
