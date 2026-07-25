"""Repository for external_playlists + external_raw_tracks."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine, delete
from sqlmodel import Session, col, select

from yubal_api.db.external_library import (
    MATCH_MATCHED,
    MATCH_PENDING,
    MATCH_REJECTED,
    MATCH_UNMATCHED,
    META_PENDING,
    META_REJECTED,
    META_VERIFIED,
    ExternalPlaylist,
    ExternalRawTrack,
)

_UPSERT_FIELDS = (
    "dir_name",
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
    "file_key",
)


class ExternalLibraryRepository:
    """CRUD for external playlists (dirs) + raw track index rows."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # -- Playlists (Raw/<dir_name>) --

    def upsert_playlist(self, dir_name: str) -> ExternalPlaylist:
        with Session(self._engine) as session:
            existing = session.get(ExternalPlaylist, dir_name)
            if existing is not None:
                return existing
            row = ExternalPlaylist(dir_name=dir_name)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def get_playlist(self, dir_name: str) -> ExternalPlaylist | None:
        with Session(self._engine) as session:
            return session.get(ExternalPlaylist, dir_name)

    def get_playlist_by_uid(self, playlist_uid: str) -> ExternalPlaylist | None:
        with Session(self._engine) as session:
            stmt = select(ExternalPlaylist).where(
                ExternalPlaylist.playlist_uid == playlist_uid
            )
            return session.exec(stmt).first()

    def list_playlists(self) -> list[ExternalPlaylist]:
        with Session(self._engine) as session:
            stmt = select(ExternalPlaylist).order_by(col(ExternalPlaylist.dir_name))
            return list(session.exec(stmt).all())

    def update_playlist_settings(
        self,
        dir_name: str,
        *,
        allow_mutate: bool | None = None,
        show_raw: bool | None = None,
        show_junk: bool | None = None,
        enabled: bool | None = None,
        max_items: int | None = None,
        sync_jitter_seconds: int | None = None,
        offline_marking_enabled: bool | None = None,
        offline_cleanup_enabled: bool | None = None,
        offline_cleanup_action: str | None = None,
        offline_cleanup_delay_hours: int | None = None,
    ) -> ExternalPlaylist | None:
        with Session(self._engine) as session:
            row = session.get(ExternalPlaylist, dir_name)
            if row is None:
                return None
            if allow_mutate is not None:
                row.allow_mutate = allow_mutate
            if show_raw is not None:
                row.show_raw = show_raw
            if show_junk is not None:
                row.show_junk = show_junk
            # Junk is a subset of unmatched — cannot show junk without unmatched.
            if not row.show_raw:
                row.show_junk = False
            if enabled is not None:
                row.enabled = enabled
            if max_items is not None:
                row.max_items = max(1, min(10000, int(max_items)))
            if sync_jitter_seconds is not None:
                row.sync_jitter_seconds = max(0, min(600, int(sync_jitter_seconds)))
            if offline_marking_enabled is not None:
                row.offline_marking_enabled = offline_marking_enabled
            if offline_cleanup_enabled is not None:
                row.offline_cleanup_enabled = bool(offline_cleanup_enabled)
            if offline_cleanup_action is not None:
                action = str(offline_cleanup_action).lower().strip()
                if action not in {"delete", "archive"}:
                    raise ValueError(f"invalid offline_cleanup_action: {action}")
                row.offline_cleanup_action = action
            if offline_cleanup_delay_hours is not None:
                row.offline_cleanup_delay_hours = max(
                    0, min(8760, int(offline_cleanup_delay_hours))
                )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def record_sync(
        self,
        dir_name: str,
        *,
        status: str,
        synced_at: datetime | None = None,
    ) -> ExternalPlaylist | None:
        with Session(self._engine) as session:
            row = session.get(ExternalPlaylist, dir_name)
            if row is None:
                return None
            row.last_synced_at = synced_at or datetime.now(UTC)
            row.last_sync_status = status
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def count_unmatched_for_dir(self, dir_name: str) -> int:
        """Count YTM-unmatched rows that are *not* meta-verified (pure X bucket)."""
        with Session(self._engine) as session:
            stmt = select(ExternalRawTrack.rel_path).where(
                ExternalRawTrack.dir_name == dir_name,
                col(ExternalRawTrack.match_status).in_(
                    [MATCH_UNMATCHED, MATCH_PENDING, MATCH_REJECTED]
                ),
                col(ExternalRawTrack.meta_status) != META_VERIFIED,
            )
            return len(list(session.exec(stmt).all()))

    def delete_playlists_not_in(
        self,
        dir_names: set[str],
        *,
        protected: set[str] | None = None,
    ) -> list[tuple[str, str]]:
        """Delete playlist rows missing from disk. Returns (dir_name, playlist_uid)."""
        keep = protected or set()
        removed: list[tuple[str, str]] = []
        with Session(self._engine) as session:
            stmt = select(ExternalPlaylist)
            rows = list(session.exec(stmt).all())
            for row in rows:
                if row.dir_name in dir_names or row.dir_name in keep:
                    continue
                removed.append((row.dir_name, row.playlist_uid))
                session.delete(row)
            if removed:
                session.commit()
        return removed

    # -- Raw tracks --

    def count(self) -> int:
        with Session(self._engine) as session:
            rows = session.exec(select(ExternalRawTrack.rel_path)).all()
            return len(rows)

    def count_for_dir(self, dir_name: str) -> int:
        with Session(self._engine) as session:
            stmt = select(ExternalRawTrack.rel_path).where(
                ExternalRawTrack.dir_name == dir_name
            )
            return len(list(session.exec(stmt).all()))

    def list_path_stats(self) -> dict[str, tuple[int, int]]:
        """Map rel_path → (mtime_ns, size)."""
        with Session(self._engine) as session:
            rows = session.exec(
                select(
                    ExternalRawTrack.rel_path,
                    ExternalRawTrack.mtime_ns,
                    ExternalRawTrack.size,
                )
            ).all()
            return {r[0]: (int(r[1]), int(r[2])) for r in rows}

    def get(self, rel_path: str) -> ExternalRawTrack | None:
        with Session(self._engine) as session:
            return session.get(ExternalRawTrack, rel_path)

    def upsert(self, row: ExternalRawTrack) -> None:
        with Session(self._engine) as session:
            existing = session.get(ExternalRawTrack, row.rel_path)
            now = datetime.now(UTC)
            if existing is None:
                row.updated_at = now
                session.add(row)
            else:
                for field in _UPSERT_FIELDS:
                    setattr(existing, field, getattr(row, field))
                existing.updated_at = now
                session.add(existing)
            session.commit()

    def delete_paths(self, paths: list[str]) -> int:
        if not paths:
            return 0
        with Session(self._engine) as session:
            stmt = delete(ExternalRawTrack).where(
                col(ExternalRawTrack.rel_path).in_(paths)
            )
            result = session.execute(stmt)
            session.commit()
            return int(result.rowcount or 0)

    def list_for_dir(
        self, dir_name: str, *, limit: int | None = None
    ) -> list[ExternalRawTrack]:
        with Session(self._engine) as session:
            stmt = (
                select(ExternalRawTrack)
                .where(ExternalRawTrack.dir_name == dir_name)
                .order_by(col(ExternalRawTrack.rel_path))
            )
            if limit is not None:
                stmt = stmt.limit(limit)
            return list(session.exec(stmt).all())

    def list_matched(self, dir_name: str | None = None) -> list[ExternalRawTrack]:
        with Session(self._engine) as session:
            stmt = select(ExternalRawTrack).where(
                ExternalRawTrack.match_status == MATCH_MATCHED
            )
            if dir_name:
                stmt = stmt.where(ExternalRawTrack.dir_name == dir_name)
            return list(session.exec(stmt).all())

    def list_enabled_dir_names(self) -> set[str]:
        """Return ``dir_name`` values for playlists with ``enabled=True``."""
        with Session(self._engine) as session:
            stmt = select(ExternalPlaylist.dir_name).where(
                ExternalPlaylist.enabled == True  # noqa: E712
            )
            return set(session.exec(stmt).all())

    def clear_match_cooldowns(self, *, include_rejected: bool) -> int:
        """Clear match backoff counters (cooldown-only or including junk).

        Always clears ``match_next_eligible_at`` and ``match_fail_count`` on
        unmatched/pending rows (and rejected when ``include_rejected``).

        When ``include_rejected`` is True, also set status from rejected → unmatched
        so junk tracks re-enter the match queue.
        """
        statuses = [MATCH_UNMATCHED, MATCH_PENDING]
        if include_rejected:
            statuses.append(MATCH_REJECTED)
        changed = 0
        with Session(self._engine) as session:
            stmt = select(ExternalRawTrack).where(
                col(ExternalRawTrack.match_status).in_(statuses)
            )
            now = datetime.now(UTC)
            for row in session.exec(stmt).all():
                dirty = False
                if row.match_next_eligible_at is not None or row.match_fail_count:
                    row.match_next_eligible_at = None
                    row.match_fail_count = 0
                    dirty = True
                if include_rejected and row.match_status == MATCH_REJECTED:
                    row.match_status = MATCH_UNMATCHED
                    dirty = True
                if dirty:
                    row.updated_at = now
                    session.add(row)
                    changed += 1
            if changed:
                session.commit()
        return changed

    def list_matchable(
        self,
        *,
        now: datetime,
        limit: int,
        dir_name: str | None = None,
        ignore_backoff: bool = False,
        dir_names: set[str] | None = None,
    ) -> list[ExternalRawTrack]:
        """Rows eligible for a match attempt (unmatched/pending).

        When ``ignore_backoff`` is False (default for Sync All / scheduled /
        auto-match), also require that ``match_next_eligible_at`` is null or
        already due. Prefer leaving this False — Sync All must not bypass
        cooldown. Callers that need an immediate retry should reset the row
        (manual match-one) rather than ignore backoff globally.

        ``dir_names``, when set, restricts to those playlist directories (e.g.
        enabled-only sets from ``list_enabled_dir_names``).
        """
        with Session(self._engine) as session:
            stmt = (
                select(ExternalRawTrack)
                .where(
                    col(ExternalRawTrack.match_status).in_(
                        [MATCH_UNMATCHED, MATCH_PENDING]
                    )
                )
            )
            if not ignore_backoff:
                stmt = stmt.where(
                    (col(ExternalRawTrack.match_next_eligible_at).is_(None))
                    | (col(ExternalRawTrack.match_next_eligible_at) <= now)
                )
            if dir_name:
                stmt = stmt.where(ExternalRawTrack.dir_name == dir_name)
            elif dir_names is not None:
                if not dir_names:
                    return []
                stmt = stmt.where(col(ExternalRawTrack.dir_name).in_(dir_names))
            stmt = stmt.order_by(col(ExternalRawTrack.updated_at)).limit(limit)
            return list(session.exec(stmt).all())

    def record_meta_verified(
        self,
        rel_path: str,
        *,
        source: str,
        source_id: str,
        source_url: str | None,
        title: str,
        artists: str,
        album: str,
        thumbnail_url: str | None,
        fingerprint: str,
    ) -> None:
        with Session(self._engine) as session:
            row = session.get(ExternalRawTrack, rel_path)
            if row is None:
                return
            row.meta_status = META_VERIFIED
            row.meta_source = (source or "")[:32] or None
            row.meta_source_id = (source_id or "")[:128] or None
            row.meta_source_url = source_url
            row.meta_title = (title or "")[:500] or None
            row.meta_artists = (artists or "")[:500] or None
            row.meta_album = (album or "")[:500] or None
            row.meta_thumbnail_url = thumbnail_url
            row.meta_fingerprint = fingerprint[:600]
            row.meta_verified_at = datetime.now(UTC)
            row.meta_fail_count = 0
            row.meta_next_eligible_at = None
            row.updated_at = datetime.now(UTC)
            session.add(row)
            session.commit()

    def record_meta_rejected(
        self,
        rel_path: str,
        *,
        fingerprint: str,
        next_eligible_at: datetime,
    ) -> None:
        with Session(self._engine) as session:
            row = session.get(ExternalRawTrack, rel_path)
            if row is None:
                return
            row.meta_status = META_REJECTED
            row.meta_source = None
            row.meta_source_id = None
            row.meta_source_url = None
            row.meta_title = None
            row.meta_artists = None
            row.meta_album = None
            row.meta_thumbnail_url = None
            row.meta_fingerprint = fingerprint[:600]
            row.meta_verified_at = None
            row.meta_fail_count = int(row.meta_fail_count or 0) + 1
            row.meta_next_eligible_at = next_eligible_at
            row.updated_at = datetime.now(UTC)
            session.add(row)
            session.commit()

    def defer_meta_retry(
        self,
        rel_path: str,
        *,
        next_eligible_at: datetime,
    ) -> None:
        """Backoff after transport/parse failure without marking rejected."""
        with Session(self._engine) as session:
            row = session.get(ExternalRawTrack, rel_path)
            if row is None:
                return
            # Keep pending (or prior verified invalidation state); only delay retry.
            if row.meta_status == META_REJECTED:
                row.meta_status = META_PENDING
            row.meta_next_eligible_at = next_eligible_at
            row.updated_at = datetime.now(UTC)
            session.add(row)
            session.commit()

    def invalidate_meta(self, rel_path: str) -> None:
        """Clear verification when local tags change (fingerprint mismatch)."""
        with Session(self._engine) as session:
            row = session.get(ExternalRawTrack, rel_path)
            if row is None:
                return
            row.meta_status = META_PENDING
            row.meta_source = None
            row.meta_source_id = None
            row.meta_source_url = None
            row.meta_title = None
            row.meta_artists = None
            row.meta_album = None
            row.meta_thumbnail_url = None
            row.meta_fingerprint = None
            row.meta_verified_at = None
            row.meta_next_eligible_at = None
            row.updated_at = datetime.now(UTC)
            session.add(row)
            session.commit()

    def count_meta_verified_unmatched(self, dir_name: str) -> int:
        with Session(self._engine) as session:
            stmt = select(ExternalRawTrack).where(
                ExternalRawTrack.dir_name == dir_name,
                ExternalRawTrack.meta_status == META_VERIFIED,
                col(ExternalRawTrack.match_status) != MATCH_MATCHED,
            )
            return len(list(session.exec(stmt).all()))

    def list_meta_verified_unmatched(self, dir_name: str) -> list[ExternalRawTrack]:
        with Session(self._engine) as session:
            stmt = (
                select(ExternalRawTrack)
                .where(
                    ExternalRawTrack.dir_name == dir_name,
                    ExternalRawTrack.meta_status == META_VERIFIED,
                    col(ExternalRawTrack.match_status) != MATCH_MATCHED,
                )
                .order_by(col(ExternalRawTrack.rel_path))
            )
            return list(session.exec(stmt).all())

    def record_match_success(
        self,
        rel_path: str,
        *,
        video_id: str,
        confidence: float,
    ) -> None:
        with Session(self._engine) as session:
            row = session.get(ExternalRawTrack, rel_path)
            if row is None:
                return
            row.video_id = video_id
            row.match_status = MATCH_MATCHED
            row.match_confidence = confidence
            row.match_fail_count = 0
            row.match_next_eligible_at = None
            row.updated_at = datetime.now(UTC)
            session.add(row)
            session.commit()

    def record_match_failure(
        self,
        rel_path: str,
        *,
        next_eligible_at: datetime,
        rejected: bool = False,
    ) -> None:
        with Session(self._engine) as session:
            row = session.get(ExternalRawTrack, rel_path)
            if row is None:
                return
            row.match_fail_count += 1
            row.match_status = MATCH_REJECTED if rejected else MATCH_PENDING
            row.match_next_eligible_at = next_eligible_at
            row.updated_at = datetime.now(UTC)
            session.add(row)
            session.commit()

    def reset_match_state(self, rel_path: str) -> ExternalRawTrack | None:
        """Manual reset: clear backoff/fail counters so it is retried immediately."""
        with Session(self._engine) as session:
            row = session.get(ExternalRawTrack, rel_path)
            if row is None:
                return None
            row.match_status = MATCH_UNMATCHED
            row.match_fail_count = 0
            row.match_next_eligible_at = None
            row.video_id = None
            row.match_confidence = None
            row.updated_at = datetime.now(UTC)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def refresh_all_norms(self) -> int:
        """Recompute norm keys from stored tags (no file I/O). Returns rows changed."""
        from yubal.utils.normalize_text import (
            normalize_artist_key,
            normalize_music_text,
        )

        changed = 0
        with Session(self._engine) as session:
            rows = list(session.exec(select(ExternalRawTrack)).all())
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
