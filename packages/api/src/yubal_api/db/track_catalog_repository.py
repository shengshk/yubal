"""Repository for track catalog (tracks + locations)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, or_
from sqlmodel import Session, col, select

from yubal.utils.library import STORAGE_DOWNLOAD, STORAGE_ROOTS

from yubal_api.db.track_catalog import (
    LocationMembershipStatus,
    TrackLocation,
    TrackRecord,
)


def format_track_display(artist: str, album_artist: str, title: str) -> str:
    """UI label: albumartist · artist - title when they differ."""
    artist = (artist or "").strip()
    album_artist = (album_artist or "").strip()
    title = (title or "").strip() or "Unknown"
    if album_artist and artist and album_artist != artist:
        return f"{album_artist} · {artist} - {title}"
    if artist:
        return f"{artist} - {title}"
    if album_artist:
        return f"{album_artist} - {title}"
    return title


class TrackCatalogRepository:
    """Upsert track facts and list locations under a save folder."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_track(self, video_id: str) -> TrackRecord | None:
        with Session(self._engine) as session:
            return session.get(TrackRecord, video_id)

    def list_locations_for_video(
        self, video_id: str
    ) -> list[TrackLocation]:
        with Session(self._engine) as session:
            stmt = (
                select(TrackLocation)
                .where(TrackLocation.video_id == video_id)
                .order_by(col(TrackLocation.save_folder))
            )
            return list(session.exec(stmt).all())

    def search_tracks(self, query: str, *, limit: int = 20) -> list[TrackRecord]:
        """Fuzzy match tracks by title / artist / album (case-insensitive).

        Only returns tracks that still have at least one catalog location.
        """
        needle = (query or "").strip()
        if not needle or limit <= 0:
            return []
        # Escape LIKE wildcards so user input is literal.
        escaped = (
            needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        with Session(self._engine) as session:
            stmt = (
                select(TrackRecord)
                .join(
                    TrackLocation,
                    col(TrackLocation.video_id) == col(TrackRecord.video_id),
                )
                .where(
                    or_(
                        col(TrackRecord.title).ilike(pattern, escape="\\"),
                        col(TrackRecord.artist).ilike(pattern, escape="\\"),
                        col(TrackRecord.album_artist).ilike(pattern, escape="\\"),
                        col(TrackRecord.album).ilike(pattern, escape="\\"),
                    )
                )
                .distinct()
                .order_by(col(TrackRecord.title))
                .limit(limit)
            )
            return list(session.exec(stmt).all())

    def upsert_track(
        self,
        *,
        video_id: str,
        title: str,
        artist: str,
        album_artist: str,
        album: str = "",
        track_number: int | None = None,
        year: str | None = None,
        cover_url: str | None = None,
        lyrics: str | None = None,
        has_embedded_cover: bool = False,
        has_lyrics_embedded: bool = False,
        has_lyrics_sidecar: bool = False,
        lyrics_source: str | None = None,
        authoritative_assets: bool = False,
    ) -> TrackRecord:
        with Session(self._engine) as session:
            existing = session.get(TrackRecord, video_id)
            now = datetime.now(UTC)
            if existing is None:
                row = TrackRecord(
                    video_id=video_id,
                    title=title,
                    artist=artist,
                    album_artist=album_artist or artist,
                    album=album,
                    track_number=track_number,
                    year=year,
                    cover_url=cover_url,
                    lyrics=lyrics,
                    has_embedded_cover=has_embedded_cover,
                    has_lyrics_embedded=has_lyrics_embedded,
                    has_lyrics_sidecar=has_lyrics_sidecar,
                    lyrics_source=lyrics_source,
                    updated_at=now,
                )
                session.add(row)
                session.commit()
                session.refresh(row)
                return row

            existing.title = title
            existing.artist = artist
            existing.album_artist = album_artist or artist
            existing.album = album
            existing.track_number = track_number
            existing.year = year
            if cover_url:
                existing.cover_url = cover_url
            if lyrics:
                existing.lyrics = lyrics
            if lyrics_source:
                existing.lyrics_source = lyrics_source
            if authoritative_assets:
                # Fresh disk probe: reflect reality, allowing flags to clear.
                existing.has_embedded_cover = has_embedded_cover
                existing.has_lyrics_embedded = has_lyrics_embedded
                existing.has_lyrics_sidecar = has_lyrics_sidecar
            else:
                # Best-effort caller: never clear a flag we already trust.
                existing.has_embedded_cover = (
                    existing.has_embedded_cover or has_embedded_cover
                )
                existing.has_lyrics_embedded = (
                    existing.has_lyrics_embedded or has_lyrics_embedded
                )
                existing.has_lyrics_sidecar = (
                    existing.has_lyrics_sidecar or has_lyrics_sidecar
                )
            existing.updated_at = now
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

    def update_asset_state(
        self,
        *,
        video_id: str,
        has_embedded_cover: bool,
        has_lyrics_embedded: bool,
        has_lyrics_sidecar: bool,
        lyrics: str | None = None,
        cover_source: str | None = None,
        lyrics_source: str | None = None,
        last_enriched_at: datetime | None = None,
        last_enrich_error: str | None = None,
    ) -> None:
        """Authoritatively write asset flags + enrichment result for a track.

        Used by the library enrichment pass after re-probing the file on disk,
        so flags reflect reality (and may clear a stale True).
        """
        with Session(self._engine) as session:
            row = session.get(TrackRecord, video_id)
            if row is None:
                return
            row.has_embedded_cover = has_embedded_cover
            row.has_lyrics_embedded = has_lyrics_embedded
            row.has_lyrics_sidecar = has_lyrics_sidecar
            if lyrics:
                row.lyrics = lyrics
            if cover_source:
                row.cover_source = cover_source
            if lyrics_source:
                row.lyrics_source = lyrics_source
            row.last_enriched_at = last_enriched_at
            row.last_enrich_error = last_enrich_error
            row.updated_at = datetime.now(UTC)
            session.add(row)
            session.commit()

    def mark_enriched(
        self,
        video_id: str,
        *,
        at: datetime,
        error: str | None = None,
    ) -> None:
        """Record only the enrichment timestamp + error (leave flags untouched)."""
        with Session(self._engine) as session:
            row = session.get(TrackRecord, video_id)
            if row is None:
                return
            row.last_enriched_at = at
            row.last_enrich_error = error
            session.add(row)
            session.commit()

    def upsert_location(
        self,
        *,
        video_id: str,
        save_folder: str,
        relative_path: str,
        origin: str = "download",
        storage_root: str = STORAGE_DOWNLOAD,
    ) -> TrackLocation:
        folder = save_folder.strip().replace("\\", "/").rstrip("/")
        rel = relative_path.strip().replace("\\", "/")
        with Session(self._engine) as session:
            stmt = select(TrackLocation).where(
                TrackLocation.video_id == video_id,
                TrackLocation.save_folder == folder,
                TrackLocation.relative_path == rel,
            )
            existing = session.exec(stmt).first()
            now = datetime.now(UTC)
            if existing is not None:
                existing.updated_at = now
                if origin:
                    existing.origin = origin
                if storage_root:
                    existing.storage_root = storage_root
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing

            # Same save_folder + video_id but path changed → update path
            stmt2 = select(TrackLocation).where(
                TrackLocation.video_id == video_id,
                TrackLocation.save_folder == folder,
            )
            same_folder = session.exec(stmt2).first()
            if same_folder is not None:
                same_folder.relative_path = rel
                same_folder.updated_at = now
                if origin:
                    same_folder.origin = origin
                if storage_root:
                    same_folder.storage_root = storage_root
                session.add(same_folder)
                session.commit()
                session.refresh(same_folder)
                return same_folder

            row = TrackLocation(
                video_id=video_id,
                save_folder=folder,
                relative_path=rel,
                origin=origin or "download",
                storage_root=storage_root or STORAGE_DOWNLOAD,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def set_canonical(
        self, video_id: str, *, storage: str, relative_path: str
    ) -> None:
        """Record the single physical file other locations hardlink from."""
        rel = relative_path.strip().replace("\\", "/")
        with Session(self._engine) as session:
            row = session.get(TrackRecord, video_id)
            if row is None:
                return
            row.canonical_storage = storage
            row.canonical_rel = rel
            row.updated_at = datetime.now(UTC)
            session.add(row)
            session.commit()

    def set_immutable(self, video_id: str, immutable: bool) -> None:
        with Session(self._engine) as session:
            row = session.get(TrackRecord, video_id)
            if row is None:
                return
            row.immutable = immutable
            row.updated_at = datetime.now(UTC)
            session.add(row)
            session.commit()

    def stamp_origin_hukou(
        self,
        video_id: str,
        *,
        playlist_uid: str,
        immutable: bool,
    ) -> bool:
        """Attach origin hukou on first stamp only; refresh immutable if same origin.

        Returns True when this call set or refreshed hukou for ``playlist_uid``.
        """
        with Session(self._engine) as session:
            row = session.get(TrackRecord, video_id)
            if row is None:
                return False
            now = datetime.now(UTC)
            if row.origin_playlist_uid is None:
                row.origin_playlist_uid = playlist_uid
                row.immutable = immutable
                row.updated_at = now
                session.add(row)
                session.commit()
                return True
            if row.origin_playlist_uid == playlist_uid:
                if row.immutable != immutable:
                    row.immutable = immutable
                    row.updated_at = now
                    session.add(row)
                    session.commit()
                return True
            # Different origin already claimed — first wins.
            return False

    def set_immutable_for_origin(
        self, playlist_uid: str, *, immutable: bool
    ) -> int:
        """Flip immutable for every track stamped with this origin hukou."""
        changed = 0
        with Session(self._engine) as session:
            stmt = select(TrackRecord).where(
                TrackRecord.origin_playlist_uid == playlist_uid
            )
            now = datetime.now(UTC)
            for row in session.exec(stmt).all():
                if row.immutable == immutable:
                    continue
                row.immutable = immutable
                row.updated_at = now
                session.add(row)
                changed += 1
            if changed:
                session.commit()
        return changed

    def liberate_origin(self, playlist_uid: str) -> int:
        """Clear hukou for all tracks from a cancelled playlist (become Direct-like)."""
        changed = 0
        with Session(self._engine) as session:
            stmt = select(TrackRecord).where(
                TrackRecord.origin_playlist_uid == playlist_uid
            )
            now = datetime.now(UTC)
            for row in session.exec(stmt).all():
                row.origin_playlist_uid = None
                row.immutable = False
                row.updated_at = now
                session.add(row)
                changed += 1
            if changed:
                session.commit()
        return changed

    def liberate_tracks(self, video_ids: list[str]) -> int:
        """Clear hukou on specific tracks (e.g. move to Direct / hard delete)."""
        if not video_ids:
            return 0
        changed = 0
        with Session(self._engine) as session:
            now = datetime.now(UTC)
            for video_id in video_ids:
                row = session.get(TrackRecord, video_id)
                if row is None:
                    continue
                if row.origin_playlist_uid is None and not row.immutable:
                    continue
                row.origin_playlist_uid = None
                row.immutable = False
                row.updated_at = now
                session.add(row)
                changed += 1
            if changed:
                session.commit()
        return changed

    def resolve_canonical_path(self, video_id: str) -> Path | None:
        """Absolute path to the canonical physical file, if recorded and present."""
        with Session(self._engine) as session:
            row = session.get(TrackRecord, video_id)
            if row is None or not row.canonical_storage or not row.canonical_rel:
                return None
            root = STORAGE_ROOTS.get(row.canonical_storage)
            if root is None:
                return None
            path = root / row.canonical_rel
            return path if path.is_file() else None

    def count_by_origins(self, origins: list[str]) -> int:
        """Count track_locations whose origin is in ``origins``."""
        if not origins:
            return 0
        with Session(self._engine) as session:
            stmt = select(TrackLocation).where(col(TrackLocation.origin).in_(origins))
            return len(list(session.exec(stmt).all()))

    def list_all_by_video_id(
        self,
    ) -> dict[str, list[tuple[TrackLocation, TrackRecord]]]:
        """Group all catalog locations by video_id."""
        with Session(self._engine) as session:
            stmt = (
                select(TrackLocation, TrackRecord)
                .join(
                    TrackRecord,
                    col(TrackLocation.video_id) == col(TrackRecord.video_id),
                )
                .order_by(col(TrackLocation.video_id), col(TrackLocation.save_folder))
            )
            grouped: dict[str, list[tuple[TrackLocation, TrackRecord]]] = {}
            for loc, rec in session.exec(stmt).all():
                grouped.setdefault(loc.video_id, []).append((loc, rec))
            return grouped

    def list_for_save_folder(
        self, save_folder: str, *, order_by_recent: bool = False
    ) -> list[tuple[TrackLocation, TrackRecord]]:
        folder = save_folder.strip().replace("\\", "/").rstrip("/")
        with Session(self._engine) as session:
            order = (
                col(TrackLocation.updated_at).desc()
                if order_by_recent
                else col(TrackLocation.relative_path)
            )
            stmt = (
                select(TrackLocation, TrackRecord)
                .join(
                    TrackRecord,
                    col(TrackLocation.video_id) == col(TrackRecord.video_id),
                )
                .where(TrackLocation.save_folder == folder)
                .order_by(order)
            )
            return list(session.exec(stmt).all())

    def get_location(
        self,
        video_id: str,
        save_folder: str,
    ) -> TrackLocation | None:
        folder = save_folder.strip().replace("\\", "/").rstrip("/")
        with Session(self._engine) as session:
            stmt = select(TrackLocation).where(
                TrackLocation.video_id == video_id,
                TrackLocation.save_folder == folder,
            )
            return session.exec(stmt).first()

    def resolve_existing_paths(
        self,
        video_ids: list[str],
        *,
        data_root: Path,
    ) -> dict[str, str]:
        """Return one existing library-relative path for each requested video ID."""
        ids = {value for value in video_ids if value}
        if not ids:
            return {}
        resolved: dict[str, str] = {}
        with Session(self._engine) as session:
            stmt = (
                select(TrackLocation)
                .where(col(TrackLocation.video_id).in_(ids))
                .order_by(
                    col(TrackLocation.save_folder),
                    col(TrackLocation.relative_path),
                )
            )
            for location in session.exec(stmt).all():
                if location.video_id in resolved:
                    continue
                relative = str(
                    Path(location.save_folder) / Path(location.relative_path)
                ).replace("\\", "/")
                if (data_root / relative).is_file():
                    resolved[location.video_id] = relative
        return resolved

    def get_lyrics_by_library_path(self, library_path: str) -> str | None:
        """Look up catalog lyrics for ``save_folder/relative_path`` under data root."""
        rel = library_path.strip().replace("\\", "/").lstrip("/")
        if not rel:
            return None
        parts = [p for p in rel.split("/") if p]
        if len(parts) < 2:
            return None
        with Session(self._engine) as session:
            for i in range(1, len(parts)):
                folder = "/".join(parts[:i])
                rest = "/".join(parts[i:])
                stmt = (
                    select(TrackRecord)
                    .join(
                        TrackLocation,
                        col(TrackLocation.video_id) == col(TrackRecord.video_id),
                    )
                    .where(
                        TrackLocation.save_folder == folder,
                        TrackLocation.relative_path == rest,
                    )
                )
                row = session.exec(stmt).first()
                if row is not None and row.lyrics and row.lyrics.strip():
                    return row.lyrics.strip()
        return None

    def get_lyrics_source_by_library_path(self, library_path: str) -> str | None:
        """Return the recorded lyrics provenance for a library-relative path."""
        rel = library_path.strip().replace("\\", "/").lstrip("/")
        parts = [p for p in rel.split("/") if p]
        if len(parts) < 2:
            return None
        with Session(self._engine) as session:
            for i in range(1, len(parts)):
                folder = "/".join(parts[:i])
                rest = "/".join(parts[i:])
                stmt = (
                    select(TrackRecord)
                    .join(
                        TrackLocation,
                        col(TrackLocation.video_id) == col(TrackRecord.video_id),
                    )
                    .where(
                        TrackLocation.save_folder == folder,
                        TrackLocation.relative_path == rest,
                    )
                )
                row = session.exec(stmt).first()
                if row is not None and row.lyrics and row.lyrics.strip():
                    return row.lyrics_source
        return None

    def set_lyrics_by_library_path(
        self, library_path: str, lyrics: str, *, source: str | None = "manual"
    ) -> bool:
        """Update catalog lyrics for a library-relative audio path.

        Returns True when a matching track row was found and updated.
        """
        rel = library_path.strip().replace("\\", "/").lstrip("/")
        if not rel:
            return False
        parts = [p for p in rel.split("/") if p]
        if len(parts) < 2:
            return False
        text = lyrics.strip()
        with Session(self._engine) as session:
            for i in range(1, len(parts)):
                folder = "/".join(parts[:i])
                rest = "/".join(parts[i:])
                stmt = (
                    select(TrackRecord)
                    .join(
                        TrackLocation,
                        col(TrackLocation.video_id) == col(TrackRecord.video_id),
                    )
                    .where(
                        TrackLocation.save_folder == folder,
                        TrackLocation.relative_path == rest,
                    )
                )
                row = session.exec(stmt).first()
                if row is None:
                    continue
                row.lyrics = text or None
                row.has_lyrics_sidecar = bool(text)
                row.has_lyrics_embedded = bool(text)
                row.lyrics_source = source if text else None
                row.updated_at = datetime.now(UTC)
                session.add(row)
                session.commit()
                return True
        return False

    def delete_location(self, save_folder: str, relative_path: str) -> None:
        folder = save_folder.strip().replace("\\", "/").rstrip("/")
        rel = relative_path.strip().replace("\\", "/")
        with Session(self._engine) as session:
            stmt = select(TrackLocation).where(
                TrackLocation.save_folder == folder,
                TrackLocation.relative_path == rel,
            )
            row = session.exec(stmt).first()
            if row is None:
                return
            session.delete(row)
            session.commit()

    def delete_all_for_save_folder(self, save_folder: str) -> int:
        """Remove every catalog location under ``save_folder``. Returns count."""
        folder = save_folder.strip().replace("\\", "/").rstrip("/")
        if not folder:
            return 0
        with Session(self._engine) as session:
            stmt = select(TrackLocation).where(TrackLocation.save_folder == folder)
            rows = list(session.exec(stmt).all())
            for row in rows:
                session.delete(row)
            session.commit()
            return len(rows)

    def set_membership_status(
        self,
        save_folder: str,
        video_id: str,
        status: LocationMembershipStatus,
        *,
        missing_since: datetime | None = None,
    ) -> TrackLocation | None:
        """Update Direct-list membership for one video in a save folder."""
        folder = save_folder.strip().replace("\\", "/").rstrip("/")
        with Session(self._engine) as session:
            stmt = select(TrackLocation).where(
                TrackLocation.save_folder == folder,
                TrackLocation.video_id == video_id,
            )
            row = session.exec(stmt).first()
            if row is None:
                return None
            row.membership_status = status
            if status == LocationMembershipStatus.OFFLINE:
                row.missing_since = missing_since or datetime.now(UTC)
            else:
                row.missing_since = None
            row.updated_at = datetime.now(UTC)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def get_location_by_relative_path(
        self,
        save_folder: str,
        relative_path: str,
    ) -> TrackLocation | None:
        folder = save_folder.strip().replace("\\", "/").rstrip("/")
        rel = relative_path.strip().replace("\\", "/").lstrip("/")
        with Session(self._engine) as session:
            stmt = select(TrackLocation).where(
                TrackLocation.save_folder == folder,
                TrackLocation.relative_path == rel,
            )
            return session.exec(stmt).first()

    def rewrite_save_folder(self, old_folder: str, new_folder: str) -> int:
        """Rewrite catalog locations after an exclusive folder rename/merge."""
        old_n = old_folder.strip().replace("\\", "/").rstrip("/")
        new_n = new_folder.strip().replace("\\", "/").rstrip("/")
        if not old_n or old_n == new_n:
            return 0
        with Session(self._engine) as session:
            stmt = select(TrackLocation).where(TrackLocation.save_folder == old_n)
            rows = list(session.exec(stmt).all())
            updated = 0
            for row in rows:
                # Skip if destination already has this video_id.
                existing = session.exec(
                    select(TrackLocation).where(
                        TrackLocation.save_folder == new_n,
                        TrackLocation.video_id == row.video_id,
                    )
                ).first()
                if existing is not None:
                    session.delete(row)
                else:
                    row.save_folder = new_n
                    row.updated_at = datetime.now(UTC)
                    session.add(row)
                updated += 1
            session.commit()
            return updated

    def record_from_download(
        self,
        *,
        video_id: str,
        title: str,
        artist: str,
        album_artist: str,
        album: str,
        track_number: int | None,
        year: str | None,
        cover_url: str | None,
        save_folder: str,
        absolute_path: Path,
        data_root: Path,
        origin: str = "download",
    ) -> None:
        """Upsert track + location after a successful download/hardlink/skip."""
        try:
            rel_to_data = absolute_path.resolve().relative_to(data_root.resolve())
        except ValueError:
            return
        parts = rel_to_data.parts
        folder = save_folder.strip().replace("\\", "/").rstrip("/")
        folder_parts = tuple(p for p in folder.split("/") if p)
        if parts[: len(folder_parts)] != folder_parts:
            # Path not under this save folder
            return
        relative_path = str(Path(*parts[len(folder_parts) :])) if len(parts) > len(
            folder_parts
        ) else absolute_path.name

        lrc = absolute_path.with_suffix(".lrc")
        lyrics_text: str | None = None
        if lrc.is_file():
            try:
                lyrics_text = (
                    lrc.read_text(encoding="utf-8", errors="ignore").strip() or None
                )
            except OSError:
                lyrics_text = None

        has_cover = False
        has_lyrics_tag = False
        probe_ok = False
        try:
            from mediafile import MediaFile

            audio = MediaFile(absolute_path)
            has_cover = bool(audio.images)
            has_lyrics_tag = bool(audio.lyrics and str(audio.lyrics).strip())
            probe_ok = True
        except Exception:
            pass

        self.upsert_track(
            video_id=video_id,
            title=title,
            artist=artist,
            album_artist=album_artist or artist,
            album=album,
            track_number=track_number,
            year=year,
            cover_url=cover_url,
            lyrics=lyrics_text,
            has_embedded_cover=has_cover,
            has_lyrics_embedded=has_lyrics_tag,
            has_lyrics_sidecar=bool(lyrics_text),
            # We just read the actual file, so the flags are authoritative and
            # may legitimately clear a stale True (cover/lyrics removed on disk).
            authoritative_assets=probe_ok,
        )
        self.upsert_location(
            video_id=video_id,
            save_folder=folder,
            relative_path=relative_path,
            origin=origin or "download",
            storage_root=STORAGE_DOWNLOAD,
        )
        existing = self.get_track(video_id)
        if existing is not None and not existing.canonical_rel:
            self.set_canonical(
                video_id,
                storage=STORAGE_DOWNLOAD,
                relative_path=str(rel_to_data).replace("\\", "/"),
            )
