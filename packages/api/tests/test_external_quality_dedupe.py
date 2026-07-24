"""Quality-aware dedupe when external Raw matches an existing video_id."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlmodel import SQLModel, create_engine

import yubal.utils.library as library
import yubal_api.db.track_catalog_repository as catalog_repo
import yubal_api.services.external_library_service as ext_svc
from yubal.utils.library import STORAGE_DOWNLOAD, STORAGE_EXTERNAL

from yubal_api.db.external_library import MATCH_MATCHED, ExternalRawTrack
from yubal_api.db.external_library_repository import ExternalLibraryRepository
from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.services.external_library_service import ExternalLibraryService
from yubal_api.services.preferences import PreferencesStore


@pytest.fixture
def library_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    download = tmp_path / "Download"
    external = tmp_path / "External"
    download.mkdir()
    (external / "Raw").mkdir(parents=True)
    (external / "Organized").mkdir(parents=True)
    monkeypatch.setattr(library, "DOWNLOAD_ROOT", download)
    monkeypatch.setattr(library, "EXTERNAL_ROOT", external)
    monkeypatch.setattr(library, "EXTERNAL_RAW_ROOT", external / "Raw")
    monkeypatch.setattr(library, "EXTERNAL_ORGANIZED_ROOT", external / "Organized")
    roots = {STORAGE_DOWNLOAD: download, STORAGE_EXTERNAL: external}
    monkeypatch.setattr(library, "STORAGE_ROOTS", roots)
    monkeypatch.setattr(catalog_repo, "STORAGE_ROOTS", roots)
    monkeypatch.setattr(ext_svc, "DOWNLOAD_ROOT", download)
    monkeypatch.setattr(ext_svc, "EXTERNAL_ROOT", external)
    monkeypatch.setattr(ext_svc, "EXTERNAL_RAW_ROOT", external / "Raw")
    monkeypatch.setattr(ext_svc, "EXTERNAL_ORGANIZED_ROOT", external / "Organized")
    monkeypatch.setattr(ext_svc, "STORAGE_ROOTS", roots)
    return download, external


def _service(tmp_path: Path) -> ExternalLibraryService:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'q.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    data_path = tmp_path / "data"
    data_path.mkdir()
    return ExternalLibraryService(
        ExternalLibraryRepository(engine),
        TrackCatalogRepository(engine),
        PreferencesStore(tmp_path / "prefs.json", data_path),
    )


def test_readonly_worse_raw_links_organized_to_existing(
    tmp_path: Path, library_roots: tuple[Path, Path]
) -> None:
    download, external = library_roots
    service = _service(tmp_path)
    playlist = service._repository.upsert_playlist("TEST2")
    assert playlist.allow_mutate is False

    # Existing better library file (larger "flac").
    sub = download / "SubList" / "Album" / "song.flac"
    sub.parent.mkdir(parents=True)
    sub.write_bytes(b"x" * 5000)

    catalog = service._catalog
    catalog.upsert_track(
        video_id="vidQ",
        title="Song",
        artist="Artist",
        album_artist="Artist",
        album="Album",
    )
    catalog.upsert_location(
        video_id="vidQ",
        save_folder="SubList/Album",
        relative_path="song.flac",
        origin="download",
        storage_root=STORAGE_DOWNLOAD,
    )
    catalog.set_canonical(
        "vidQ",
        storage=STORAGE_DOWNLOAD,
        relative_path="SubList/Album/song.flac",
    )

    # Worse Raw (small mp3).
    raw = external / "Raw" / "TEST2" / "song.mp3"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"y" * 100)
    service._repository.upsert(
        ExternalRawTrack(
            rel_path="TEST2/song.mp3",
            dir_name="TEST2",
            title="Song",
            artists="Artist",
            album="Album",
            codec="mp3",
            sample_rate=44100,
            bit_depth=16,
            size=100,
            video_id="vidQ",
            match_status=MATCH_MATCHED,
        )
    )

    assert service.ingest_matched("TEST2/song.mp3") is True
    assert raw.is_file()
    assert raw.stat().st_nlink == 1  # Raw not linked away

    org_locs = catalog.list_for_save_folder("Organized/TEST2")
    assert len(org_locs) == 1
    org_path = external / "Organized" / "TEST2" / org_locs[0][0].relative_path
    assert org_path.is_file()
    assert org_path.stat().st_ino == sub.stat().st_ino

    rec = catalog.get_track("vidQ")
    assert rec is not None
    assert rec.immutable is False
    assert rec.origin_playlist_uid is None


def test_readonly_better_raw_becomes_master_hardlink(
    tmp_path: Path, library_roots: tuple[Path, Path]
) -> None:
    download, external = library_roots
    service = _service(tmp_path)
    service._repository.upsert_playlist("TEST2")

    sub = download / "SubList" / "Album" / "song.mp3"
    sub.parent.mkdir(parents=True)
    sub.write_bytes(b"x" * 100)

    catalog = service._catalog
    catalog.upsert_track(
        video_id="vidB",
        title="Song",
        artist="Artist",
        album_artist="Artist",
        album="Album",
    )
    catalog.upsert_location(
        video_id="vidB",
        save_folder="SubList/Album",
        relative_path="song.mp3",
        origin="download",
        storage_root=STORAGE_DOWNLOAD,
    )
    catalog.set_canonical(
        "vidB",
        storage=STORAGE_DOWNLOAD,
        relative_path="SubList/Album/song.mp3",
    )

    raw = external / "Raw" / "TEST2" / "song.flac"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"z" * 8000)
    service._repository.upsert(
        ExternalRawTrack(
            rel_path="TEST2/song.flac",
            dir_name="TEST2",
            title="Song",
            artists="Artist",
            album="Album",
            codec="flac",
            sample_rate=96000,
            bit_depth=24,
            size=8000,
            video_id="vidB",
            match_status=MATCH_MATCHED,
        )
    )

    assert service.ingest_matched("TEST2/song.flac") is True
    assert raw.is_file()

    sub_after = download / "SubList" / "Album" / "song.mp3"
    assert sub_after.is_file()
    assert sub_after.stat().st_ino == raw.stat().st_ino
    assert raw.stat().st_nlink >= 2

    rec = catalog.get_track("vidB")
    assert rec is not None
    assert rec.immutable is True
    assert rec.canonical_storage == STORAGE_EXTERNAL
    assert rec.canonical_rel and rec.canonical_rel.startswith("Raw/")
