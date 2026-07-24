"""Hardlink counting for external / download playlist views."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlmodel import SQLModel, create_engine

import yubal.utils.library as library
import yubal_api.services.external_library_service as ext_svc
from yubal.utils.library import STORAGE_DOWNLOAD, STORAGE_EXTERNAL

from yubal_api.db.external_library_repository import ExternalLibraryRepository
from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.services.external_library_service import ExternalLibraryService
from yubal_api.services.library_hardlink import (
    classify_catalog_file,
    is_cross_folder_hardlink,
)
from yubal_api.services.preferences import PreferencesStore


@pytest.fixture
def library_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    download = tmp_path / "Download"
    external = tmp_path / "External"
    download.mkdir()
    external.mkdir()
    monkeypatch.setattr(library, "DOWNLOAD_ROOT", download)
    monkeypatch.setattr(library, "EXTERNAL_ROOT", external)
    monkeypatch.setattr(library, "EXTERNAL_RAW_ROOT", external / "Raw")
    monkeypatch.setattr(library, "EXTERNAL_ORGANIZED_ROOT", external / "Organized")
    monkeypatch.setattr(
        library,
        "STORAGE_ROOTS",
        {STORAGE_DOWNLOAD: download, STORAGE_EXTERNAL: external},
    )
    roots = {STORAGE_DOWNLOAD: download, STORAGE_EXTERNAL: external}
    monkeypatch.setattr(ext_svc, "DOWNLOAD_ROOT", download)
    monkeypatch.setattr(ext_svc, "EXTERNAL_ROOT", external)
    monkeypatch.setattr(ext_svc, "STORAGE_ROOTS", roots)
    return download, external


def _service(tmp_path: Path) -> ExternalLibraryService:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    prefs_path = tmp_path / "prefs.json"
    data_path = tmp_path / "data"
    data_path.mkdir()
    return ExternalLibraryService(
        ExternalLibraryRepository(engine),
        TrackCatalogRepository(engine),
        PreferencesStore(prefs_path, data_path),
    )


def test_raw_organized_link_not_counted_as_download_hardlink(
    tmp_path: Path,
    library_roots: tuple[Path, Path],
) -> None:
    """Readonly ingest Raw↔Organized links are exclusive, not hardlink."""
    _download, external = library_roots
    dir_name = "MyList"
    save_folder = f"Organized/{dir_name}"
    org_rel = "Artist/Year - Album/01 Song.flac"
    org_path = external / save_folder / org_rel
    raw_path = external / "Raw" / dir_name / "song.flac"
    org_path.parent.mkdir(parents=True)
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"audio")
    os.link(raw_path, org_path)
    assert org_path.stat().st_nlink == 2

    service = _service(tmp_path)
    service._repository.upsert_playlist(dir_name)

    catalog = service._catalog
    catalog.upsert_track(
        video_id="vid1",
        title="Song",
        artist="Artist",
        album_artist="Artist",
        album="Album",
    )
    catalog.upsert_location(
        video_id="vid1",
        save_folder=save_folder,
        relative_path=org_rel,
        origin="external_dedupe",
        storage_root=STORAGE_EXTERNAL,
    )

    view = service.get_playlist_view(dir_name)
    assert view is not None
    assert view.local == 1
    assert view.hardlink == 0
    assert view.exclusive == 1


def test_copy_into_direct_stays_exclusive_both_sides(
    tmp_path: Path,
    library_roots: tuple[Path, Path],
) -> None:
    """Catalog row on Direct + Raw↔Organized nlink must not inflate hardlink."""
    download, external = library_roots
    dir_name = "TEST"
    save_folder = f"Organized/{dir_name}"
    org_rel = "Artist/Year - Album/01 Song.flac"
    org_path = external / save_folder / org_rel
    raw_path = external / "Raw" / dir_name / "song.flac"
    dl_path = download / "Direct" / org_rel
    org_path.parent.mkdir(parents=True)
    raw_path.parent.mkdir(parents=True)
    dl_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"audio")
    os.link(raw_path, org_path)
    # Simulate failed cross-mount hardlink → copy
    dl_path.write_bytes(b"audio")
    assert org_path.stat().st_ino != dl_path.stat().st_ino

    service = _service(tmp_path)
    service._repository.upsert_playlist(dir_name)
    catalog = service._catalog
    catalog.upsert_track(
        video_id="vid-copy",
        title="Song",
        artist="Artist",
        album_artist="Artist",
        album="Album",
    )
    catalog.upsert_location(
        video_id="vid-copy",
        save_folder=save_folder,
        relative_path=org_rel,
        origin="external_dedupe",
        storage_root=STORAGE_EXTERNAL,
    )
    catalog.upsert_location(
        video_id="vid-copy",
        save_folder="Direct",
        relative_path=org_rel,
        origin="external_add",
        storage_root=STORAGE_DOWNLOAD,
    )

    view = service.get_playlist_view(dir_name)
    assert view is not None
    assert view.hardlink == 0
    assert view.exclusive == 1

    assert (
        classify_catalog_file(
            dl_path,
            video_id="vid-copy",
            save_folder="Direct",
            catalog=catalog,
            download_root=download,
        )
        == "real"
    )


def test_organized_download_link_counted_as_hardlink(
    tmp_path: Path,
    library_roots: tuple[Path, Path],
) -> None:
    download, external = library_roots
    dir_name = "Shared"
    save_folder = f"Organized/{dir_name}"
    org_rel = "Artist/Year - Album/01 Song.flac"
    dl_rel = "SubList/Default/Artist/Year - Album/01 Song.flac"
    org_path = external / save_folder / org_rel
    dl_path = download / dl_rel
    org_path.parent.mkdir(parents=True)
    dl_path.parent.mkdir(parents=True)
    dl_path.write_bytes(b"audio")
    os.link(dl_path, org_path)
    assert org_path.stat().st_nlink == 2

    service = _service(tmp_path)
    service._repository.upsert_playlist(dir_name)

    catalog = service._catalog
    catalog.upsert_track(
        video_id="vid2",
        title="Song",
        artist="Artist",
        album_artist="Artist",
        album="Album",
    )
    catalog.upsert_location(
        video_id="vid2",
        save_folder=save_folder,
        relative_path=org_rel,
        origin="external_dedupe",
        storage_root=STORAGE_EXTERNAL,
    )
    catalog.upsert_location(
        video_id="vid2",
        save_folder="SubList/Default",
        relative_path=dl_rel.removeprefix("SubList/Default/"),
        origin="download",
        storage_root=STORAGE_DOWNLOAD,
    )
    catalog.set_canonical(
        "vid2",
        storage=STORAGE_DOWNLOAD,
        relative_path=dl_rel,
    )

    view = service.get_playlist_view(dir_name)
    assert view is not None
    assert view.local == 1
    assert view.hardlink == 1
    assert view.exclusive == 0

    assert is_cross_folder_hardlink(
        dl_path,
        video_id="vid2",
        save_folder="SubList/Default",
        catalog=catalog,
        download_root=download,
    )


def test_same_filesystem_probes_real_hardlink(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert library.same_filesystem(a, b) is True
