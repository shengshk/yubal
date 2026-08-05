"""Durable one-pass attempt state for the external media library."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from sqlmodel import SQLModel, create_engine
from yubal_api.db.external_library import (
    MATCH_UNMATCHED,
    META_REJECTED,
    ExternalRawTrack,
)
from yubal_api.db.external_library_repository import ExternalLibraryRepository
from yubal_api.services.external_library_service import ExternalLibraryService


def _repository(tmp_path: Path) -> ExternalLibraryRepository:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'external-attempt.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return ExternalLibraryRepository(engine)


def _track(*, mtime_ns: int = 1, title: str = "Song") -> ExternalRawTrack:
    return ExternalRawTrack(
        rel_path="playlist/song.flac",
        dir_name="playlist",
        origin_kind="external_playlist",
        origin_ref="playlist",
        mtime_ns=mtime_ns,
        size=100,
        title=title,
        artists="Artist",
        album="Album",
        title_norm=title.lower(),
        artist_norm="artist",
        album_norm="album",
    )


def test_completed_attempt_is_not_automatically_retried(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.upsert(_track())

    repository.record_match_failure(
        "playlist/song.flac",
        next_eligible_at=datetime.now(UTC) + timedelta(days=1),
    )

    stored = repository.get("playlist/song.flac")
    assert stored is not None
    assert stored.ytm_attempted_at is not None
    assert repository.list_matchable(now=datetime.now(UTC), limit=10) == []

    assert repository.clear_match_cooldowns(include_rejected=False) == 1
    retry = repository.list_matchable(now=datetime.now(UTC), limit=10)
    assert [row.rel_path for row in retry] == ["playlist/song.flac"]


def test_file_change_reopens_ytm_and_metadata_decisions(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.upsert(_track())
    repository.record_match_failure(
        "playlist/song.flac",
        next_eligible_at=datetime.now(UTC) + timedelta(days=1),
    )
    repository.record_meta_rejected(
        "playlist/song.flac",
        fingerprint="song|artist|album",
        next_eligible_at=datetime.now(UTC) + timedelta(days=1),
    )

    repository.upsert(_track(mtime_ns=2, title="Changed Song"))

    stored = repository.get("playlist/song.flac")
    assert stored is not None
    assert stored.match_status == MATCH_UNMATCHED
    assert stored.ytm_attempted_at is None
    assert stored.meta_status != META_REJECTED
    assert stored.meta_attempted_at is None
    assert stored.match_fail_count == 0
    assert stored.meta_fail_count == 0


def test_routine_sync_does_not_repeat_empty_tag_provider_lookup() -> None:
    repository = MagicMock()
    row = _track()
    row.artists = ""
    row.artist_norm = ""
    row.ytm_attempted_at = datetime.now(UTC)
    repository.list_for_dir.return_value = [row]
    service = ExternalLibraryService(repository, MagicMock(), MagicMock())
    service._fill_empty_tags_one = MagicMock(return_value=False)  # type: ignore[method-assign]

    result = service.fill_empty_tags_batch("playlist")

    assert result == {"checked": 0, "filled": 0, "skipped": 1}
    service._fill_empty_tags_one.assert_not_called()
