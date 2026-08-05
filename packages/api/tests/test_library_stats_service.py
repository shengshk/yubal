"""Tests for whole-library physical track statistics."""

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from yubal_api.services.library_stats_service import (
    LibraryStatsService,
    LibraryTrackSummary,
)


def _audio(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio")
    return path


def test_summary_deduplicates_hardlinks_and_prioritizes_ids(
    tmp_path: Path,
) -> None:
    download = tmp_path / "download"
    external = tmp_path / "external"
    wanted = tmp_path / "wanted"

    identified = _audio(download / "direct" / "identified.mp3")
    identified_link = external / "organized" / "A" / "identified.mp3"
    identified_link.parent.mkdir(parents=True)
    identified_link.hardlink_to(identified)

    verified = _audio(external / "raw" / "A" / "verified.flac")
    verified_link = wanted / "Artist" / "verified.flac"
    verified_link.parent.mkdir(parents=True)
    verified_link.hardlink_to(verified)

    unverified = _audio(external / "raw" / "A" / "unverified.flac")
    _audio(download / "direct" / "ignored.txt")

    catalog = MagicMock()
    catalog.list_all_by_video_id.return_value = {
        "video-id": [
            (
                SimpleNamespace(
                    storage_root="download",
                    save_folder="direct",
                    relative_path="identified.mp3",
                ),
                SimpleNamespace(),
            )
        ],
        # A cloud/catalog row without a real file must not be counted.
        "missing-id": [
            (
                SimpleNamespace(
                    storage_root="download",
                    save_folder="direct",
                    relative_path="missing.mp3",
                ),
                SimpleNamespace(),
            )
        ],
    }

    external_repo = MagicMock()
    external_repo.list_inventory_inodes.return_value = [
        ("A/verified.flac", verified.stat().st_ino),
        ("A/unverified.flac", unverified.stat().st_ino),
    ]
    external_repo.list_playlists.return_value = [SimpleNamespace(dir_name="A")]
    external_repo.list_for_dir.return_value = [
        SimpleNamespace(
            rel_path="A/verified.flac",
            video_id=None,
            meta_status="verified",
        ),
        SimpleNamespace(
            rel_path="A/unverified.flac",
            video_id=None,
            meta_status="rejected",
        ),
    ]

    wanted_repo = MagicMock()
    wanted_repo.list_all.return_value = [
        SimpleNamespace(
            relative_path="Artist/verified.flac",
            video_id=None,
            source="musicbrainz",
            source_id="mbid",
        )
    ]

    summary = LibraryStatsService(
        catalog=catalog,
        external=external_repo,
        wanted=wanted_repo,
        download_root=download,
        external_root=external,
        wanted_root=wanted,
    ).summary()

    assert summary.physical_count == 3
    assert summary.hardlink_duplicate_count == 2
    assert summary.identified_count == 1
    assert summary.verified_count == 1
    assert summary.unverified_count == 1
    assert summary.unidentified_count == 2
    assert summary.effective_count == 2


def test_audit_reports_untracked_and_repairs_stale_catalog_rows(
    tmp_path: Path,
) -> None:
    download = tmp_path / "download"
    external = tmp_path / "external"
    wanted = tmp_path / "wanted"
    _audio(download / "direct" / "tracked.mp3")
    _audio(download / "manual" / "untracked.flac")

    catalog = MagicMock()
    catalog.list_all_by_video_id.return_value = {
        "tracked": [
            (
                SimpleNamespace(
                    storage_root="download",
                    save_folder="direct",
                    relative_path="tracked.mp3",
                ),
                SimpleNamespace(),
            )
        ],
        "missing": [
            (
                SimpleNamespace(
                    storage_root="download",
                    save_folder="direct",
                    relative_path="missing.mp3",
                ),
                SimpleNamespace(),
            )
        ],
    }
    external_repo = MagicMock()
    external_repo.list_playlists.return_value = []
    wanted_repo = MagicMock()
    wanted_repo.list_all.return_value = []

    audit = LibraryStatsService(
        catalog=catalog,
        external=external_repo,
        wanted=wanted_repo,
        download_root=download,
        external_root=external,
        wanted_root=wanted,
    ).audit(repair_missing=True)

    assert audit.physical_count == 2
    assert audit.catalog_location_count == 2
    assert audit.missing_catalog_locations == 0
    assert audit.repaired_catalog_locations == 1
    assert audit.untracked_physical_count == 1
    assert not audit.ok
    catalog.delete_location.assert_called_once_with("direct", "missing.mp3")


def test_summary_reuses_cached_disk_walk_until_forced(tmp_path: Path) -> None:
    service = LibraryStatsService(
        catalog=MagicMock(),
        external=MagicMock(),
        wanted=MagicMock(),
        download_root=tmp_path / "download",
        external_root=tmp_path / "external",
        wanted_root=tmp_path / "wanted",
    )
    service._scan_audio = MagicMock(return_value=(set(), 0))  # type: ignore[method-assign]
    service._catalog_identified = MagicMock(return_value=set())  # type: ignore[method-assign]
    service._raw_status = MagicMock(return_value=(set(), set()))  # type: ignore[method-assign]
    service._wanted_status = MagicMock(return_value=(set(), set()))  # type: ignore[method-assign]

    service.summary()
    service.summary()
    assert service._scan_audio.call_count == 1

    service.summary(force=True)
    assert service._scan_audio.call_count == 2


def test_snapshot_survives_restart_without_implicit_media_scan(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state" / "library_summary.json"
    repositories = {
        "catalog": MagicMock(),
        "external": MagicMock(),
        "wanted": MagicMock(),
    }
    repositories["catalog"].list_all_by_video_id.return_value = {}
    repositories["external"].list_playlists.return_value = []
    repositories["wanted"].list_all.return_value = []

    first = LibraryStatsService(
        **repositories,
        download_root=tmp_path / "download",
        external_root=tmp_path / "external",
        wanted_root=tmp_path / "wanted",
        state_path=state_path,
    )
    expected = first.summary()
    assert state_path.is_file()

    restored = LibraryStatsService(
        **repositories,
        download_root=tmp_path / "download",
        external_root=tmp_path / "external",
        wanted_root=tmp_path / "wanted",
        state_path=state_path,
    )
    started = threading.Event()

    def delayed_refresh() -> LibraryTrackSummary:
        started.set()
        return expected

    restored._build_summary = delayed_refresh  # type: ignore[method-assign]
    cached, refreshing = restored.snapshot()

    assert not started.wait(timeout=0.1)
    assert cached == expected
    assert refreshing is False

    _cached, refreshing = restored.snapshot(refresh=True)
    assert started.wait(timeout=1)
    assert refreshing is True


def test_dirty_snapshot_starts_one_background_refresh(tmp_path: Path) -> None:
    service = LibraryStatsService(
        catalog=MagicMock(),
        external=MagicMock(),
        wanted=MagicMock(),
        download_root=tmp_path / "download",
        external_root=tmp_path / "external",
        wanted_root=tmp_path / "wanted",
    )
    cached = LibraryTrackSummary(
        effective_count=1,
        identified_count=1,
        unidentified_count=0,
        verified_count=0,
        unverified_count=0,
        physical_count=1,
        hardlink_duplicate_count=0,
    )
    refreshed = LibraryTrackSummary(
        effective_count=2,
        identified_count=2,
        unidentified_count=0,
        verified_count=0,
        unverified_count=0,
        physical_count=2,
        hardlink_duplicate_count=0,
    )
    service._summary_cache = cached
    service.mark_media_changed()

    started = threading.Event()
    release = threading.Event()

    def delayed_refresh() -> LibraryTrackSummary:
        started.set()
        release.wait(timeout=1)
        return refreshed

    service._build_summary = delayed_refresh  # type: ignore[method-assign]

    first, first_refreshing = service.snapshot()
    assert started.wait(timeout=1)
    second, second_refreshing = service.snapshot()
    assert first == cached
    assert second == cached
    assert first_refreshing is True
    assert second_refreshing is True

    release.set()
    for thread in threading.enumerate():
        if thread.name == "library-summary-refresh":
            thread.join(timeout=1)

    final, final_refreshing = service.snapshot()
    assert final == refreshed
    assert final_refreshing is False


def test_concurrent_forced_summaries_share_one_scan(tmp_path: Path) -> None:
    service = LibraryStatsService(
        catalog=MagicMock(),
        external=MagicMock(),
        wanted=MagicMock(),
        download_root=tmp_path / "download",
        external_root=tmp_path / "external",
        wanted_root=tmp_path / "wanted",
    )
    service._summary_cache = LibraryTrackSummary(0, 0, 0, 0, 0, 0, 0)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def delayed_refresh() -> LibraryTrackSummary:
        nonlocal calls
        calls += 1
        started.set()
        release.wait(timeout=1)
        return LibraryTrackSummary(1, 1, 0, 0, 0, 1, 0)

    service._build_summary = delayed_refresh  # type: ignore[method-assign]
    first = threading.Thread(target=lambda: service.summary(force=True))
    second = threading.Thread(target=lambda: service.summary(force=True))
    first.start()
    assert started.wait(timeout=1)
    second.start()
    release.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert calls == 1


def test_mutation_during_summary_scan_keeps_snapshot_dirty(tmp_path: Path) -> None:
    service = LibraryStatsService(
        catalog=MagicMock(),
        external=MagicMock(),
        wanted=MagicMock(),
        download_root=tmp_path / "download",
        external_root=tmp_path / "external",
        wanted_root=tmp_path / "wanted",
    )
    started = threading.Event()
    release = threading.Event()

    def delayed_refresh() -> LibraryTrackSummary:
        started.set()
        release.wait(timeout=1)
        return LibraryTrackSummary(1, 1, 0, 0, 0, 1, 0)

    service._build_summary = delayed_refresh  # type: ignore[method-assign]
    worker = threading.Thread(target=lambda: service.summary(force=True))
    worker.start()
    assert started.wait(timeout=1)
    service.mark_media_changed()
    release.set()
    worker.join(timeout=1)

    assert service._summary_dirty is True
