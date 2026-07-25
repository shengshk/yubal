"""Persistence for wanted / wishlist tracks."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlmodel import Session, col, select

from yubal_api.db.wanted import WantedTrack


class WantedRepository:
    def __init__(self, engine) -> None:
        self._engine = engine

    def list_all(self) -> list[WantedTrack]:
        with Session(self._engine) as session:
            rows = session.exec(
                select(WantedTrack).order_by(
                    col(WantedTrack.relative_path).is_(None),
                    WantedTrack.created_at.desc(),
                )
            ).all()
            return list(rows)

    def get(self, track_id: UUID) -> WantedTrack | None:
        with Session(self._engine) as session:
            return session.get(WantedTrack, track_id)

    def find_by_norms(
        self, *, title_norm: str, artist_norm: str, album_norm: str
    ) -> WantedTrack | None:
        with Session(self._engine) as session:
            return session.exec(
                select(WantedTrack).where(
                    WantedTrack.title_norm == title_norm,
                    WantedTrack.artist_norm == artist_norm,
                    WantedTrack.album_norm == album_norm,
                )
            ).first()

    def add(self, row: WantedTrack) -> WantedTrack:
        with Session(self._engine) as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row

    def save(self, row: WantedTrack) -> WantedTrack:
        row.updated_at = datetime.now(UTC)
        with Session(self._engine) as session:
            merged = session.merge(row)
            session.commit()
            session.refresh(merged)
            session.expunge(merged)
            return merged

    def delete(self, track_id: UUID) -> bool:
        with Session(self._engine) as session:
            row = session.get(WantedTrack, track_id)
            if row is None:
                return False
            session.delete(row)
            session.commit()
            return True

    def delete_all(self, *, only_unmatched: bool = False) -> int:
        with Session(self._engine) as session:
            q = select(WantedTrack)
            if only_unmatched:
                q = q.where(col(WantedTrack.relative_path).is_(None))
            rows = list(session.exec(q).all())
            for row in rows:
                session.delete(row)
            session.commit()
            return len(rows)

    def list_matchable(self, *, now: datetime | None = None, limit: int = 25) -> list[WantedTrack]:
        now = now or datetime.now(UTC)
        with Session(self._engine) as session:
            rows = session.exec(
                select(WantedTrack)
                .where(
                    (col(WantedTrack.match_next_eligible_at).is_(None))
                    | (WantedTrack.match_next_eligible_at <= now)
                )
                .order_by(WantedTrack.created_at.asc())
                .limit(limit)
            ).all()
            return list(rows)
