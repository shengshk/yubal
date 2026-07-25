"""Tests for whole-library physical track statistics."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from yubal_api.services.library_stats_service import LibraryStatsService


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

    _audio(external / "raw" / "A" / "unverified.flac")
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
