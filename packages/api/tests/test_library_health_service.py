from pathlib import Path

import yubal_api.services.library_health_service as health_module
from pytest import MonkeyPatch
from yubal_api.services.library_health_service import LibraryHealthService


def test_stable_health_poll_persists_only_once(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    download = tmp_path / "Download"
    external = tmp_path / "External"
    download.mkdir()
    external.mkdir()
    download_sentinel = download / ".yubal-mount"
    external_sentinel = external / ".yubal-mount"
    download_sentinel.touch()
    external_sentinel.touch()

    monkeypatch.setattr(health_module, "DOWNLOAD_ROOT", download)
    monkeypatch.setattr(health_module, "EXTERNAL_ROOT", external)
    monkeypatch.setattr(health_module, "DOWNLOAD_MOUNT_SENTINEL", download_sentinel)
    monkeypatch.setattr(health_module, "EXTERNAL_MOUNT_SENTINEL", external_sentinel)
    monkeypatch.setattr(health_module, "ensure_external_layout", lambda: None)

    service = LibraryHealthService(tmp_path / "health.json")
    writes = 0
    original_persist = service._persist

    def count_persist() -> None:
        nonlocal writes
        writes += 1
        original_persist()

    monkeypatch.setattr(service, "_persist", count_persist)

    assert service.check().ok
    assert service.check().ok
    assert service.check().ok
    assert writes == 1


def test_scoped_delete_guard_uses_playlist_baseline() -> None:
    assert LibraryHealthService.allow_scoped_index_deletes(42, 42)
    assert LibraryHealthService.allow_scoped_index_deletes(42, 80)
    assert not LibraryHealthService.allow_scoped_index_deletes(42, 100)
    assert not LibraryHealthService.allow_scoped_index_deletes(0, 42)
    assert LibraryHealthService.allow_scoped_index_deletes(0, 5)
