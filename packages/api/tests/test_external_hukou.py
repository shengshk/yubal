"""External playlist hukou: origin stamp, flip, liberate, delete modes."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import SQLModel, create_engine

import yubal.utils.library as library
import yubal_api.services.external_library_service as ext_svc
from yubal.utils.library import STORAGE_DOWNLOAD, STORAGE_EXTERNAL

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
    monkeypatch.setattr(
        library,
        "STORAGE_ROOTS",
        {STORAGE_DOWNLOAD: download, STORAGE_EXTERNAL: external},
    )
    roots = {STORAGE_DOWNLOAD: download, STORAGE_EXTERNAL: external}
    monkeypatch.setattr(ext_svc, "DOWNLOAD_ROOT", download)
    monkeypatch.setattr(ext_svc, "EXTERNAL_ROOT", external)
    monkeypatch.setattr(ext_svc, "EXTERNAL_RAW_ROOT", external / "Raw")
    monkeypatch.setattr(ext_svc, "EXTERNAL_ORGANIZED_ROOT", external / "Organized")
    monkeypatch.setattr(ext_svc, "STORAGE_ROOTS", roots)
    return download, external


def _service(tmp_path: Path) -> ExternalLibraryService:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'hukou.db'}",
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


def test_stamp_hukou_and_flip_allow_mutate(
    tmp_path: Path, library_roots: tuple[Path, Path]
) -> None:
    service = _service(tmp_path)
    playlist = service._repository.upsert_playlist("TEST")
    assert playlist.allow_mutate is False

    catalog = service._catalog
    catalog.upsert_track(
        video_id="vid1",
        title="Song",
        artist="Artist",
        album_artist="Artist",
        album="Album",
    )
    catalog.stamp_origin_hukou(
        "vid1",
        playlist_uid=playlist.playlist_uid,
        immutable=True,
    )
    rec = catalog.get_track("vid1")
    assert rec is not None
    assert rec.origin_playlist_uid == playlist.playlist_uid
    assert rec.immutable is True

    # First-wins: another playlist cannot overwrite hukou.
    other = service._repository.upsert_playlist("OTHER")
    stamped = catalog.stamp_origin_hukou(
        "vid1",
        playlist_uid=other.playlist_uid,
        immutable=False,
    )
    assert stamped is False
    rec = catalog.get_track("vid1")
    assert rec is not None
    assert rec.origin_playlist_uid == playlist.playlist_uid
    assert rec.immutable is True

    updated = service.update_playlist_settings("TEST", allow_mutate=True)
    assert updated is not None and updated.allow_mutate is True
    rec = catalog.get_track("vid1")
    assert rec is not None
    assert rec.immutable is False


def test_liberate_when_playlist_folder_gone(
    tmp_path: Path, library_roots: tuple[Path, Path]
) -> None:
    _download, external = library_roots
    service = _service(tmp_path)
    raw_test = external / "Raw" / "TEST"
    raw_test.mkdir(parents=True)
    playlist = service._repository.upsert_playlist("TEST")
    catalog = service._catalog
    catalog.upsert_track(
        video_id="vid2",
        title="Song",
        artist="Artist",
        album_artist="Artist",
    )
    catalog.stamp_origin_hukou(
        "vid2",
        playlist_uid=playlist.playlist_uid,
        immutable=True,
    )

    # Folder removed → sync cancels hukou and liberates.
    raw_test.rmdir()
    service.sync_playlists_from_disk()
    assert service._repository.get_playlist("TEST") is None
    rec = catalog.get_track("vid2")
    assert rec is not None
    assert rec.origin_playlist_uid is None
    assert rec.immutable is False


def test_readonly_rejects_file_modes(
    tmp_path: Path, library_roots: tuple[Path, Path]
) -> None:
    service = _service(tmp_path)
    service._repository.upsert_playlist("TEST")
    with pytest.raises(ValueError, match="read-only"):
        service.delete_playlist("TEST", "delete_matched", direct_folder="Direct")


def test_delete_unmatched_removes_files(
    tmp_path: Path, library_roots: tuple[Path, Path]
) -> None:
    _download, external = library_roots
    service = _service(tmp_path)
    service._repository.upsert_playlist("TEST")
    service._repository.update_playlist_settings("TEST", allow_mutate=True)
    raw = external / "Raw" / "TEST" / "song.flac"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"audio")
    from yubal_api.db.external_library import ExternalRawTrack

    service._repository.upsert(
        ExternalRawTrack(
            rel_path="TEST/song.flac",
            dir_name="TEST",
            title="Song",
            artists="Artist",
            match_status="unmatched",
        )
    )
    result = service.delete_playlist(
        "TEST", "delete_unmatched", direct_folder="Direct"
    )
    assert result.deleted_raw == 1
    assert not raw.is_file()
    assert service._repository.get("TEST/song.flac") is None


def test_forget_unmatched_rejected(
    tmp_path: Path, library_roots: tuple[Path, Path]
) -> None:
    service = _service(tmp_path)
    service._repository.upsert_playlist("TEST")
    with pytest.raises(ValueError, match="unknown delete mode"):
        service.delete_playlist(
            "TEST", "forget_unmatched", direct_folder="Direct"
        )


def test_default_cannot_become_readonly(
    tmp_path: Path, library_roots: tuple[Path, Path]
) -> None:
    service = _service(tmp_path)
    service.ensure_special_playlists()
    with pytest.raises(ValueError, match="writable"):
        service.update_playlist_settings("Default", allow_mutate=False)
