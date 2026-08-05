"""External playlist hukou: origin stamp, flip, liberate, delete modes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yubal.utils.library as library
import yubal_api.services.external_library_service as ext_svc
from sqlmodel import SQLModel, create_engine
from yubal.utils.library import (
    EXTERNAL_DEFAULT_DIR,
    STORAGE_DOWNLOAD,
    STORAGE_EXTERNAL,
)
from yubal_api.db.external_library import ExternalPlaylist, ExternalRawTrack
from yubal_api.db.external_library_repository import ExternalLibraryRepository
from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.services.external_library_service import ExternalLibraryService
from yubal_api.services.preferences import PreferencesStore


@pytest.fixture
def library_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
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


def test_access_mode_switches_until_source_is_mutated(
    tmp_path: Path, library_roots: tuple[Path, Path]
) -> None:
    service = _service(tmp_path)
    playlist = service._repository.upsert_playlist("TEST")

    managed = service.update_playlist_settings("TEST", access_mode="managed")
    assert managed is not None and managed.access_mode == "managed"
    readonly = service.update_playlist_settings("TEST", access_mode="readonly")
    assert readonly is not None and readonly.access_mode == "readonly"

    service._repository.mark_source_mutated(
        playlist.playlist_uid,
        mutation_kind="audio_tags",
    )
    with pytest.raises(ValueError, match="locked"):
        service.update_playlist_settings("TEST", access_mode="managed")

    unchanged = service.update_playlist_settings("TEST", access_mode="readonly")
    assert unchanged is not None and unchanged.access_mode == "readonly"


def test_configured_playlist_cannot_return_to_pending(
    tmp_path: Path, library_roots: tuple[Path, Path]
) -> None:
    service = _service(tmp_path)
    service._repository.upsert_playlist("TEST")
    service.update_playlist_settings("TEST", access_mode="readonly")

    with pytest.raises(ValueError, match="cannot return to pending"):
        service.update_playlist_settings("TEST", access_mode="pending")


def test_external_inventory_is_separate_and_metadata_indexing_resumes(
    tmp_path: Path,
    library_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _download, external = library_roots
    service = _service(tmp_path)
    raw_test = external / "Raw" / "TEST"
    raw_test.mkdir(parents=True)
    for number in range(3):
        (raw_test / f"song-{number}.flac").write_bytes(b"audio")

    service._repository.upsert_playlist(
        "TEST",
        access_mode="managed",
        enabled=True,
    )
    # Keep this test deterministic; discovery itself is the operation under test.
    monkeypatch.setattr(service, "_schedule_playlist_inventories", lambda: None)

    def fake_read(
        path: Path,
        rel_path: str,
        dir_name: str,
        *,
        origin_kind: str,
        origin_ref: str,
    ) -> ExternalRawTrack:
        stat = path.stat()
        return ExternalRawTrack(
            rel_path=rel_path,
            dir_name=dir_name,
            origin_kind=origin_kind,
            origin_ref=origin_ref,
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            title=path.stem,
            artists="Artist",
        )

    monkeypatch.setattr(ext_svc, "_read_raw_tags", fake_read)
    health = MagicMock()
    health.allow_scoped_index_deletes.return_value = True

    discovered = service.discover_raw(health, dir_name="TEST")

    assert discovered.scanned == 3
    assert discovered.added == 3
    assert service._repository.count_inventory_for_dir("TEST") == 3
    assert service._repository.count_for_dir("TEST") == 0
    # Existing UI buckets include known-but-not-yet-parsed stock.
    view = service.get_playlist_view("TEST")
    assert view is not None and view.unmatched_count == 3

    indexed, errors = service.index_inventory_batch(limit=1, dir_name="TEST")
    assert (indexed, errors) == (1, 0)
    assert service._repository.count_for_dir("TEST") == 1
    assert (
        len(service._repository.list_pending_inventory(limit=10, dir_name="TEST")) == 2
    )

    indexed, errors = service.index_inventory_batch(limit=10, dir_name="TEST")
    assert (indexed, errors) == (2, 0)
    assert service._repository.count_for_dir("TEST") == 3
    assert not service._repository.list_pending_inventory(
        limit=10,
        dir_name="TEST",
    )


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
    playlist = service._repository.upsert_playlist("TEST")
    service._repository.update_playlist_settings("TEST", allow_mutate=True)
    raw = external / "Raw" / "TEST" / "song.flac"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"audio")
    service._repository.upsert(
        ExternalRawTrack(
            rel_path="TEST/song.flac",
            dir_name="TEST",
            origin_kind="external",
            origin_ref=playlist.playlist_uid,
            title="Song",
            artists="Artist",
            match_status="unmatched",
        )
    )
    result = service.delete_playlist("TEST", "delete_unmatched", direct_folder="Direct")
    assert result.deleted_raw == 1
    assert not raw.is_file()
    assert service._repository.get("TEST/song.flac") is None
    locked = service._repository.get_playlist("TEST")
    assert locked is not None
    assert locked.source_mutated_at is not None
    assert locked.source_mutation_kind == "audio_deleted"


def test_managed_ingest_move_locks_access_mode(
    tmp_path: Path, library_roots: tuple[Path, Path]
) -> None:
    _download, external = library_roots
    service = _service(tmp_path)
    playlist = service._repository.upsert_playlist("TEST")
    service.update_playlist_settings("TEST", access_mode="managed")
    raw = external / "Raw" / "TEST" / "song.flac"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"audio")
    service._repository.upsert(
        ExternalRawTrack(
            rel_path="TEST/song.flac",
            dir_name="TEST",
            origin_kind="external",
            origin_ref=playlist.playlist_uid,
            title="Song",
            artists="Artist",
            album="Album",
            video_id="managed-video",
            match_status="matched",
        )
    )

    assert service.ingest_matched("TEST/song.flac") is True
    assert not raw.exists()
    locked = service._repository.get_playlist("TEST")
    assert locked is not None
    assert locked.source_mutated_at is not None
    assert locked.source_mutation_kind == "audio_moved"
    with pytest.raises(ValueError, match="locked"):
        service.update_playlist_settings("TEST", access_mode="readonly")


def test_archive_cleanup_respects_original_source_permission(
    tmp_path: Path, library_roots: tuple[Path, Path]
) -> None:
    _download, external = library_roots
    service = _service(tmp_path)
    readonly = service._repository.upsert_playlist("READONLY")
    writable = service._repository.upsert_playlist("WRITABLE")
    service._repository.update_playlist_settings("WRITABLE", allow_mutate=True)
    service.ensure_special_playlists()

    delete_root = external / "Raw" / "delete"
    delete_root.mkdir(parents=True, exist_ok=True)
    ro_path = delete_root / "readonly.flac"
    rw_path = delete_root / "writable.flac"
    ro_path.write_bytes(b"audio")
    rw_path.write_bytes(b"audio")
    service._repository.upsert(
        ExternalRawTrack(
            rel_path="delete/readonly.flac",
            dir_name="delete",
            origin_kind="external",
            origin_ref=readonly.playlist_uid,
            meta_status="rejected",
        )
    )
    service._repository.upsert(
        ExternalRawTrack(
            rel_path="delete/writable.flac",
            dir_name="delete",
            origin_kind="external",
            origin_ref=writable.playlist_uid,
            meta_status="rejected",
        )
    )

    result = service.delete_playlist(
        "delete", "delete_meta_rejected", direct_folder="direct"
    )
    assert result.deleted_raw == 1
    assert result.skipped_readonly == 1
    assert ro_path.is_file()
    assert not rw_path.exists()


def test_rejected_cleanup_moves_managed_file_to_archive_ingress(
    tmp_path: Path, library_roots: tuple[Path, Path]
) -> None:
    _download, external = library_roots
    service = _service(tmp_path)
    playlist = service._repository.upsert_playlist("MANAGED")
    service._repository.update_playlist_settings("MANAGED", allow_mutate=True)
    service.ensure_special_playlists()
    raw = external / "Raw" / "MANAGED" / "song.flac"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"audio")
    service._repository.upsert(
        ExternalRawTrack(
            rel_path="MANAGED/song.flac",
            dir_name="MANAGED",
            origin_kind="external",
            origin_ref=playlist.playlist_uid,
            title="Song",
            artists="Artist",
            meta_status="rejected",
        )
    )

    result = service.delete_playlist(
        "MANAGED",
        "archive_meta_rejected",
        direct_folder="direct",
    )

    assert result.moved == 1
    assert not raw.exists()
    archived = service._repository.list_for_dir("default")
    assert len(archived) == 1
    assert archived[0].origin_kind == "external"
    assert archived[0].origin_ref == playlist.playlist_uid
    assert (external / "Raw" / archived[0].rel_path).is_file()


def test_forget_unmatched_rejected(
    tmp_path: Path, library_roots: tuple[Path, Path]
) -> None:
    service = _service(tmp_path)
    service._repository.upsert_playlist("TEST")
    with pytest.raises(ValueError, match="unknown delete mode"):
        service.delete_playlist("TEST", "forget_unmatched", direct_folder="Direct")


def test_default_cannot_become_readonly(
    tmp_path: Path, library_roots: tuple[Path, Path]
) -> None:
    service = _service(tmp_path)
    service.ensure_special_playlists()
    with pytest.raises(ValueError, match="writable"):
        service.update_playlist_settings(EXTERNAL_DEFAULT_DIR, allow_mutate=False)


def test_default_raw_is_writable_manual_archive_source(
    tmp_path: Path, library_roots: tuple[Path, Path]
) -> None:
    service = _service(tmp_path)
    service.ensure_special_playlists()

    playlist = service._repository.get_playlist(EXTERNAL_DEFAULT_DIR)
    assert playlist is not None
    assert playlist.allow_mutate is True
    assert service._playlist_origin(EXTERNAL_DEFAULT_DIR) == ("manual", "archive")


def test_readonly_inventory_publishes_cover_before_finishing_count(
    tmp_path: Path,
    library_roots: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _download, external = library_roots
    service = _service(tmp_path)
    folder = external / "Raw" / "NEW" / "album"
    folder.mkdir(parents=True)
    (folder / "one.flac").write_bytes(b"audio")
    (folder / "two.mp3").write_bytes(b"audio")
    (folder / "notes.txt").write_text("ignore")
    service._repository.upsert_playlist("NEW")
    service.update_playlist_settings("NEW", access_mode="readonly")
    assert "NEW" in service._inventory_candidates()

    original_record_inventory = service._repository.record_inventory

    def record_inventory_after_representative(
        dir_name: str,
        *,
        audio_count: int,
        cover_rel: str | None,
    ) -> ExternalPlaylist | None:
        row = service._repository.get_playlist(dir_name)
        assert row is not None
        assert row.inventory_scanned_at is None
        assert row.discovered_cover_rel == "NEW/album/one.flac"
        return original_record_inventory(
            dir_name,
            audio_count=audio_count,
            cover_rel=cover_rel,
        )

    monkeypatch.setattr(
        service._repository,
        "record_inventory",
        record_inventory_after_representative,
    )

    service._inventory_playlist_folder("NEW")

    view = service.get_playlist_view("NEW")
    assert view is not None
    assert view.inventory_scanned is True
    assert view.unmatched_count == 2
    assert view.cover_track_path == "External/raw/NEW/album/one.flac"


def test_recycle_center_rejects_move_to_itself(
    tmp_path: Path, library_roots: tuple[Path, Path]
) -> None:
    service = _service(tmp_path)
    service.ensure_special_playlists()

    with pytest.raises(ValueError, match="cannot be moved to recycle center"):
        service.delete_playlist(
            "delete",
            "clear_offline_to_raw_delete",
            direct_folder="direct",
        )


def test_recycle_center_reconciles_legacy_organized_delete(
    tmp_path: Path,
    library_roots: tuple[Path, Path],
) -> None:
    _download, external = library_roots
    service = _service(tmp_path)
    source = service._repository.upsert_playlist("SOURCE")
    (external / "Raw" / "SOURCE").mkdir(parents=True)
    service.ensure_special_playlists()

    organized = external / "Organized" / "delete"
    tracked = organized / "Artist" / "Album" / "song.flac"
    tracked.parent.mkdir(parents=True)
    tracked.write_bytes(b"audio")
    tracked.with_suffix(".lrc").write_text("lyrics")
    dirty = organized / "dirty.mp3"
    dirty.write_bytes(b"dirty")
    (organized / "cover.jpg").write_bytes(b"cover")

    service._catalog.upsert_track(
        video_id="legacy-delete-id",
        title="Song",
        artist="Artist",
        album_artist="Artist",
        album="Album",
    )
    service._catalog.stamp_origin_hukou(
        "legacy-delete-id",
        playlist_uid=source.playlist_uid,
        immutable=False,
    )
    service._catalog.upsert_location(
        video_id="legacy-delete-id",
        save_folder="organized/delete",
        relative_path="Artist/Album/song.flac",
        origin="external_move",
        storage_root=STORAGE_EXTERNAL,
    )

    service._reconcile_legacy_recycle_organized(
        {"SOURCE", EXTERNAL_DEFAULT_DIR, "delete"}
    )

    rows = service._repository.list_for_dir("delete")
    assert len(rows) == 1
    assert rows[0].origin_kind == "external"
    assert rows[0].origin_ref == source.playlist_uid
    recycled = external / "Raw" / rows[0].rel_path
    assert recycled.is_file()
    assert recycled.with_suffix(".lrc").is_file()
    assert service._catalog.list_for_save_folder("organized/delete") == []
    assert not organized.exists()


def test_meta_retry_accepts_sqlite_naive_datetime(
    tmp_path: Path,
    library_roots: tuple[Path, Path],
) -> None:
    service = _service(tmp_path)
    service._repository.upsert_playlist("TEST")
    service._repository.upsert(
        ExternalRawTrack(
            rel_path="TEST/song.flac",
            dir_name="TEST",
            origin_kind="system",
            origin_ref="test",
            title="Song",
            artists="Artist",
            album="Album",
            meta_next_eligible_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    result = service.verify_meta_batch("TEST")

    assert result == {
        "checked": 0,
        "verified": 0,
        "rejected": 0,
        "skipped": 1,
    }
