"""Repository for preselect_tracks (local library A index)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine, delete
from sqlmodel import Session, col, select

from yubal_api.db.preselect import PreselectTrack


class PreselectRepository:
    """CRUD + match queries for the preselect index."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def count(self) -> int:
        with Session(self._engine) as session:
            rows = session.exec(select(PreselectTrack.rel_path)).all()
            return len(rows)

    def list_path_stats(self) -> dict[str, tuple[int, int]]:
        """Map rel_path → (mtime_ns, size)."""
        with Session(self._engine) as session:
            rows = session.exec(
                select(
                    PreselectTrack.rel_path,
                    PreselectTrack.mtime_ns,
                    PreselectTrack.size,
                )
            ).all()
            return {r[0]: (int(r[1]), int(r[2])) for r in rows}

    def upsert(self, row: PreselectTrack) -> None:
        with Session(self._engine) as session:
            existing = session.get(PreselectTrack, row.rel_path)
            now = datetime.now(UTC)
            if existing is None:
                row.updated_at = now
                session.add(row)
            else:
                for field in (
                    "mtime_ns",
                    "size",
                    "inode",
                    "codec",
                    "sample_rate",
                    "bit_depth",
                    "channels",
                    "duration_ms",
                    "title",
                    "artists",
                    "album",
                    "album_artist",
                    "track_number",
                    "disc_number",
                    "year",
                    "title_norm",
                    "artist_norm",
                    "album_norm",
                    "has_lyrics",
                    "lyrics_embedded",
                    "has_cover",
                    "cover_embedded",
                ):
                    setattr(existing, field, getattr(row, field))
                existing.updated_at = now
                session.add(existing)
            session.commit()

    def delete_paths(self, paths: list[str]) -> int:
        if not paths:
            return 0
        with Session(self._engine) as session:
            stmt = delete(PreselectTrack).where(col(PreselectTrack.rel_path).in_(paths))
            result = session.execute(stmt)
            session.commit()
            return int(result.rowcount or 0)

    def find_by_artist_title(
        self, artist_norm: str, title_norm: str
    ) -> list[PreselectTrack]:
        if not artist_norm or not title_norm:
            return []
        with Session(self._engine) as session:
            stmt = select(PreselectTrack).where(
                PreselectTrack.artist_norm == artist_norm,
                PreselectTrack.title_norm == title_norm,
            )
            return list(session.exec(stmt).all())

    def refresh_all_norms(self) -> int:
        """Recompute norm keys from stored tags (no file I/O). Returns rows changed."""
        from yubal.utils.normalize_text import normalize_artist_key, normalize_music_text

        changed = 0
        with Session(self._engine) as session:
            rows = list(session.exec(select(PreselectTrack)).all())
            now = datetime.now(UTC)
            for row in rows:
                title_norm = normalize_music_text(row.title)[:500]
                artist_norm = normalize_artist_key(row.artists)[:500]
                album_norm = normalize_music_text(row.album)[:500]
                if (
                    row.title_norm == title_norm
                    and row.artist_norm == artist_norm
                    and row.album_norm == album_norm
                ):
                    continue
                row.title_norm = title_norm
                row.artist_norm = artist_norm
                row.album_norm = album_norm
                row.updated_at = now
                session.add(row)
                changed += 1
            if changed:
                session.commit()
        return changed
