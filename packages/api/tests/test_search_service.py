from pathlib import Path
from types import SimpleNamespace

from sqlmodel import SQLModel, create_engine
from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.services.preferences import PreferencesStore
from yubal_api.services.search_service import SearchService


class _Client:
    def __init__(self, results: list[SimpleNamespace]) -> None:
        self.results = results

    def search_songs(self, query: str) -> list[SimpleNamespace]:
        return self.results


def _result(video_id: str, title: str = "Song") -> SimpleNamespace:
    return SimpleNamespace(
        video_id=video_id,
        title=title,
        artists=[SimpleNamespace(name="Artist")],
        album=SimpleNamespace(name="Album"),
        thumbnails=[SimpleNamespace(url="https://example.test/cover.jpg")],
        duration_seconds=123,
    )


def _service(tmp_path: Path) -> tuple[SearchService, TrackCatalogRepository]:
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    SQLModel.metadata.create_all(engine)
    catalog = TrackCatalogRepository(engine)
    preferences = PreferencesStore(
        tmp_path / "preferences.json",
        tmp_path / "data",
    )
    service = SearchService(
        state_path=tmp_path / "search.json",
        preview_root=tmp_path / "preview",
        data_path=tmp_path / "data",
        cookies_path=None,
        preferences=preferences,
        track_catalog=catalog,
    )
    return service, catalog


def test_search_matches_existing_catalog_path(tmp_path: Path) -> None:
    service, catalog = _service(tmp_path)
    audio = tmp_path / "data" / "Direct" / "Artist" / "Song.opus"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"audio")
    catalog.upsert_track(
        video_id="video1",
        title="Song",
        artist="Artist",
        album_artist="Artist",
    )
    catalog.upsert_location(
        video_id="video1",
        save_folder="Direct",
        relative_path="Artist/Song.opus",
    )
    service._client = _Client([_result("video1")])  # type: ignore[assignment]

    snapshot = service.search("test query")

    assert snapshot is not None
    assert snapshot.matched_count == 1
    assert snapshot.tracks[0].local_path == "Direct/Artist/Song.opus"


def test_empty_search_keeps_previous_snapshot(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    service._client = _Client([_result("video1", "First")])  # type: ignore[assignment]
    assert service.search("first") is not None

    service._client = _Client([])  # type: ignore[assignment]
    assert service.search("empty") is None

    current = service.current()
    assert current is not None
    assert current.query == "first"


def test_delete_removes_snapshot_and_preview_cache(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    service._client = _Client([_result("video1")])  # type: ignore[assignment]
    assert service.search("test") is not None
    preview = tmp_path / "preview" / "video1.webm"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"preview")

    assert service.delete() is True
    assert service.current() is None
    assert not preview.exists()


def test_promote_preview_imports_into_direct(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    service._client = _Client([_result("video1", "Song")])  # type: ignore[assignment]
    assert service.search("test") is not None
    preview = tmp_path / "preview" / "video1.opus"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"preview-audio")

    updated = service.promote_preview("video1")

    assert updated.matched_count == 1
    assert updated.tracks[0].matched is True
    assert updated.tracks[0].local_path is not None
    assert (tmp_path / "data" / updated.tracks[0].local_path).is_file()
