"""Tests for SQL-backed meta verification candidate selection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlmodel import SQLModel, create_engine
from yubal_api.db.external_library import (
    MATCH_UNMATCHED,
    META_VERIFIED,
    ExternalRawTrack,
)
from yubal_api.db.external_library_repository import ExternalLibraryRepository


def _repository(tmp_path: Path) -> ExternalLibraryRepository:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'meta-verifiable.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return ExternalLibraryRepository(engine)


def _track(**kwargs: object) -> ExternalRawTrack:
    base = dict(
        rel_path="playlist/song.flac",
        dir_name="playlist",
        origin_kind="external_playlist",
        origin_ref="playlist",
        mtime_ns=1,
        size=100,
        title="Song",
        artists="Artist",
        album="Album",
        title_norm="song",
        artist_norm="artist",
        album_norm="album",
        match_status=MATCH_UNMATCHED,
    )
    base.update(kwargs)
    return ExternalRawTrack(**base)


def test_list_meta_verifiable_skips_verified(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.upsert(
        _track(meta_status=META_VERIFIED, meta_attempted_at=datetime.now(UTC))
    )
    repository.upsert(_track(rel_path="playlist/other.flac", title="Other"))

    now = datetime.now(UTC)
    rows = repository.list_meta_verifiable("playlist", now=now, limit=10)
    assert [row.rel_path for row in rows] == ["playlist/other.flac"]


def test_list_meta_verifiable_reopens_rejected_after_backoff(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    repository.upsert(_track())
    repository.record_meta_rejected(
        "playlist/song.flac",
        fingerprint="song|artist|album",
        next_eligible_at=datetime.now(UTC) - timedelta(hours=1),
    )

    now = datetime.now(UTC)
    rows = repository.list_meta_verifiable("playlist", now=now, limit=10)
    assert [row.rel_path for row in rows] == ["playlist/song.flac"]
