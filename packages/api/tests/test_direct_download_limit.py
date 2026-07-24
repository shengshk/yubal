"""Tests for the configured direct-download track limit."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from yubal_api.api.exceptions import DirectDownloadLimitExceededError
from yubal_api.api.routes.jobs import create_job
from yubal_api.schemas.jobs import CreateJobRequest
from yubal_api.services.preferences import PreferencesStore


class _PlaylistInfo:
    def __init__(self, track_count: int | None) -> None:
        self.track_count = track_count

    def get_content_info(self, url: str) -> SimpleNamespace:
        return SimpleNamespace(track_count=self.track_count)


class _Executor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None]] = []

    def create_and_start_job(self, url: str, max_items: int | None) -> SimpleNamespace:
        self.calls.append((url, max_items))
        return SimpleNamespace(id="job-id")


def _preferences(tmp_path: Path, limit: int = 50) -> PreferencesStore:
    store = PreferencesStore(
        tmp_path / "preferences.json",
        tmp_path / "data",
    )
    store.update(direct_download_limit=limit)
    return store


@pytest.mark.asyncio
async def test_direct_download_rejects_content_above_limit(tmp_path: Path) -> None:
    executor = _Executor()

    with pytest.raises(DirectDownloadLimitExceededError) as exc_info:
        await create_job(
            CreateJobRequest(url="https://music.youtube.com/playlist?list=test"),
            executor,  # type: ignore[arg-type]
            _PlaylistInfo(51),  # type: ignore[arg-type]
            _preferences(tmp_path),  # type: ignore[arg-type]
        )

    assert exc_info.value.track_count == 51
    assert exc_info.value.limit == 50
    assert executor.calls == []


@pytest.mark.asyncio
async def test_direct_download_starts_all_items_within_limit(tmp_path: Path) -> None:
    executor = _Executor()
    url = "https://music.youtube.com/playlist?list=test"

    response = await create_job(
        CreateJobRequest(url=url, max_items=1),
        executor,  # type: ignore[arg-type]
        _PlaylistInfo(50),  # type: ignore[arg-type]
        _preferences(tmp_path),  # type: ignore[arg-type]
    )

    assert response.id == "job-id"
    assert executor.calls == [(url, None)]


def test_direct_download_limit_persists_only_valid_range(tmp_path: Path) -> None:
    store = _preferences(tmp_path, 100)
    assert store.effective().direct_download_limit == 100

    store.update(direct_download_limit=101)
    assert store.effective().direct_download_limit == 100
