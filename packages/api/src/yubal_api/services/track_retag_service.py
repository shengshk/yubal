"""Edit track tags and relocate files to match updated metadata."""

from __future__ import annotations

import base64
import binascii
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from yubal.models.enums import MatchResult
from yubal.models.track import TrackMetadata
from yubal.services.tagging_service import AudioFileTaggingService
from yubal.services.track_index import TrackFileIndex
from yubal.utils.audio_assets import (
    write_embedded_lyrics,
    write_lyrics_sidecar,
)
from yubal.utils.cover import fetch_cover, write_better_image
from yubal.utils.filename import (
    build_track_path,
    build_unmatched_track_path,
    build_unofficial_track_path,
)
from yubal.utils.library import (
    AUDIO_SUFFIXES,
    STORAGE_DOWNLOAD,
    STORAGE_ROOTS,
    resolve_storage_path,
    resolve_under_data,
)

from yubal_api.api.exceptions import TrackImmutableError
from yubal_api.db.track_catalog import TrackLocation, TrackRecord
from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.schemas.tracks import (
    TrackLocationUpdate,
    TrackTagUpdate,
    TrackTagUpdateResponse,
)
from yubal_api.services.library_ops import (
    _remove_empty_parents,
    parse_m3u_files,
    rewrite_path_in_m3u,
)

logger = logging.getLogger(__name__)


class TrackRetagConflictError(Exception):
    """Destination path already occupied by another file."""


def _load_cover_bytes(url: str | None) -> bytes | None:
    """Resolve cover bytes from an http(s) URL or a browser ``data:`` upload."""
    if not url:
        return None
    if url.startswith("data:"):
        header, _, encoded = url.partition(",")
        if not encoded:
            return None
        if ";base64" in header:
            try:
                return base64.b64decode(encoded)
            except (binascii.Error, ValueError):
                logger.warning("Invalid base64 cover upload")
                return None
        # Non-base64 data URLs are not expected for images.
        return None
    return fetch_cover(url)


@dataclass(frozen=True, slots=True)
class _MergedTags:
    title: str
    artist: str
    album_artist: str
    album: str
    year: str | None
    track_number: int | None
    artists: list[str]
    album_artists: list[str]


def _join_names(values: list[str] | None, fallback: str) -> str:
    if values:
        cleaned = [v.strip() for v in values if v and v.strip()]
        if cleaned:
            return " / ".join(cleaned)
    return fallback.strip()


def _split_names(value: str) -> list[str]:
    parts = [p.strip() for p in value.split(" / ") if p.strip()]
    return parts or [value.strip() or "Unknown Artist"]


def _merge_tags(record: TrackRecord, update: TrackTagUpdate) -> _MergedTags:
    title = (update.title or record.title or "").strip()
    artist = _join_names(update.artists, update.artist or record.artist or "")
    album_artist = _join_names(
        update.album_artists,
        update.album_artist or record.album_artist or artist,
    )
    album = (update.album if update.album is not None else record.album or "").strip()
    year = update.year if update.year is not None else record.year
    track_number = (
        update.track_number
        if update.track_number is not None
        else record.track_number
    )
    if not title:
        raise ValueError("title must not be empty")
    if not artist:
        raise ValueError("artist must not be empty")
    if not album_artist:
        raise ValueError("album artist must not be empty")
    if not album:
        raise ValueError("album must not be empty")
    return _MergedTags(
        title=title,
        artist=artist,
        album_artist=album_artist,
        album=album,
        year=year,
        track_number=track_number,
        artists=_split_names(artist),
        album_artists=_split_names(album_artist),
    )


def _layout_for_relative(relative_path: str) -> MatchResult:
    norm = relative_path.strip().replace("\\", "/")
    if norm.startswith("Unmatched/"):
        return MatchResult.UNMATCHED
    if norm.startswith("Unofficial/"):
        return MatchResult.UNOFFICIAL
    return MatchResult.MATCHED


def _relative_path_for_tags(
    *,
    layout: MatchResult,
    merged: _MergedTags,
    video_id: str,
    suffix: str,
    ascii_filenames: bool,
) -> str:
    primary_artist = (
        merged.album_artists[0] if merged.album_artists else merged.album_artist
    )
    if layout == MatchResult.UNMATCHED:
        stem = build_unmatched_track_path(
            base=Path("."),
            artist=primary_artist,
            title=merged.title,
            video_id=video_id,
            ascii_filenames=ascii_filenames,
        )
    elif layout == MatchResult.UNOFFICIAL:
        stem = build_unofficial_track_path(
            base=Path("."),
            artist=primary_artist,
            title=merged.title,
            video_id=video_id,
            ascii_filenames=ascii_filenames,
        )
    else:
        stem = build_track_path(
            base=Path("."),
            artist=primary_artist,
            year=merged.year,
            album=merged.album,
            track_number=merged.track_number,
            title=merged.title,
            ascii_filenames=ascii_filenames,
        )
    rel = str(stem).replace("\\", "/")
    if suffix and not rel.endswith(suffix):
        rel = f"{rel}{suffix}"
    return rel


def _to_track_metadata(
    video_id: str, merged: _MergedTags, cover_url: str | None
) -> TrackMetadata:
    return TrackMetadata(
        source_video_id=video_id,
        title=merged.title,
        artists=merged.artists,
        album=merged.album,
        album_artists=merged.album_artists,
        track_number=merged.track_number,
        year=merged.year,
        cover_url=cover_url,
        match_result=MatchResult.MATCHED,
    )


class TrackRetagService:
    """Apply user tag edits and relocate catalog file paths."""

    def __init__(
        self,
        catalog: TrackCatalogRepository,
        data_path: Path,
        *,
        ascii_filenames: bool = False,
    ) -> None:
        self._catalog = catalog
        self._data_path = data_path
        self._ascii_filenames = ascii_filenames
        self._tagger = AudioFileTaggingService()

    def update_tags(
        self,
        video_id: str,
        update: TrackTagUpdate,
    ) -> TrackTagUpdateResponse:
        record = self._catalog.get_track(video_id)
        if record is None:
            raise FileNotFoundError(f"track not found: {video_id}")

        fields = update.model_dump(exclude_unset=True)
        tag_keys = {
            "title",
            "artist",
            "album_artist",
            "album",
            "year",
            "track_number",
        }
        wants_tag_edit = any(k in fields for k in tag_keys)
        if record.immutable and wants_tag_edit:
            raise TrackImmutableError(
                f"Track {video_id} is sourced from a read-only External "
                "playlist and cannot be retagged"
            )

        locations = self._catalog.list_locations_for_video(video_id)
        if not locations:
            raise FileNotFoundError(f"no catalog locations for: {video_id}")

        apply_lyrics = "lyrics" in fields
        new_lyrics = (update.lyrics or "").strip() if apply_lyrics else None
        new_cover_url = (
            update.cover_url if "cover_url" in fields else record.cover_url
        )
        refresh_cover = bool(fields.get("refresh_cover")) or "cover_url" in fields
        # A ``data:`` upload has no stable URL to persist; keep the prior URL
        # reference in the catalog while still embedding the uploaded bytes.
        is_upload = bool(new_cover_url) and new_cover_url.startswith("data:")
        stored_cover_url = record.cover_url if is_upload else new_cover_url

        merged = _merge_tags(record, update)
        warnings: list[str] = []

        planned: list[tuple[TrackLocation, Path, Path, str, str]] = []
        tag_source: Path | None = None

        for loc in locations:
            folder = loc.save_folder.strip().replace("\\", "/").rstrip("/")
            old_rel = loc.relative_path.strip().replace("\\", "/")
            storage = loc.storage_root or STORAGE_DOWNLOAD
            try:
                if storage in STORAGE_ROOTS and storage != STORAGE_DOWNLOAD:
                    src = resolve_storage_path(storage, f"{folder}/{old_rel}")
                else:
                    src = resolve_under_data(self._data_path, f"{folder}/{old_rel}")
            except ValueError as e:
                warnings.append(f"invalid location {folder}/{old_rel}: {e}")
                continue
            if not src.is_file():
                warnings.append(f"missing file: {folder}/{old_rel}")
                continue
            if tag_source is None:
                tag_source = src
            suffix = src.suffix.lower()
            if suffix not in AUDIO_SUFFIXES:
                suffix = src.suffix
            # Immutable / assets-only: never relocate files.
            if record.immutable or not wants_tag_edit:
                planned.append((loc, src, src, old_rel, old_rel))
                continue
            layout = _layout_for_relative(old_rel)
            new_rel = _relative_path_for_tags(
                layout=layout,
                merged=merged,
                video_id=video_id,
                suffix=suffix,
                ascii_filenames=self._ascii_filenames,
            )
            try:
                if storage in STORAGE_ROOTS and storage != STORAGE_DOWNLOAD:
                    dest = resolve_storage_path(storage, f"{folder}/{new_rel}")
                else:
                    dest = resolve_under_data(self._data_path, f"{folder}/{new_rel}")
            except ValueError as e:
                raise ValueError(f"invalid destination for {folder}: {e}") from e
            if src.resolve() == dest.resolve():
                planned.append((loc, src, dest, old_rel, new_rel))
                continue
            if dest.exists() and dest.resolve() != src.resolve():
                raise TrackRetagConflictError(
                    f"destination already exists: {folder}/{new_rel}"
                )
            planned.append((loc, src, dest, old_rel, new_rel))

        if tag_source is None:
            raise FileNotFoundError(f"no on-disk file for: {video_id}")

        cover_bytes: bytes | None = None
        cover_applied = False
        if refresh_cover and new_cover_url:
            cover_bytes = _load_cover_bytes(new_cover_url)
            if cover_bytes is None:
                warnings.append("could not download cover art")
            else:
                cover_applied = True

        lyrics_for_tag = new_lyrics if apply_lyrics else record.lyrics
        track_meta = _to_track_metadata(video_id, merged, stored_cover_url)
        try:
            if record.immutable or not wants_tag_edit:
                # Assets-only: embed cover / lyrics without rewriting text tags.
                if cover_bytes:
                    from mediafile import Image, MediaFile

                    audio = MediaFile(tag_source)
                    audio.images = [Image(data=cover_bytes)]
                    audio.save()
                if apply_lyrics and new_lyrics:
                    write_embedded_lyrics(tag_source, new_lyrics)
            else:
                self._tagger.apply_metadata_tags(
                    tag_source,
                    track_meta,
                    cover=cover_bytes,
                    lyrics=lyrics_for_tag,
                )
        except Exception as e:
            logger.exception("Failed to tag %s during retag: %s", video_id, e)
            raise

        location_updates: list[TrackLocationUpdate] = []
        final_paths: list[Path] = []
        for loc, src, dest, old_rel, new_rel in planned:
            folder = loc.save_folder.strip().replace("\\", "/").rstrip("/")
            if src.resolve() != dest.resolve():
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.rename(src, dest)
                src_lrc = src.with_suffix(".lrc")
                dest_lrc = dest.with_suffix(".lrc")
                if src_lrc.is_file():
                    try:
                        if dest_lrc.exists():
                            dest_lrc.unlink()
                        os.rename(src_lrc, dest_lrc)
                    except OSError:
                        try:
                            os.link(src_lrc, dest_lrc)
                        except OSError:
                            warnings.append(
                                f"could not move lyrics for {folder}/{old_rel}"
                            )
                save_root = resolve_under_data(self._data_path, folder)
                for m3u in parse_m3u_files(save_root):
                    rewrite_path_in_m3u(m3u, src, dest)
                _remove_empty_parents(src.parent, save_root)
                location_updates.append(
                    TrackLocationUpdate(
                        save_folder=folder,
                        old_relative_path=old_rel,
                        new_relative_path=new_rel,
                    )
                )
            final_paths.append(dest if dest.is_file() else src)
            self._catalog.upsert_location(
                video_id=video_id,
                save_folder=folder,
                relative_path=new_rel,
                origin=loc.origin or "download",
                storage_root=loc.storage_root or STORAGE_DOWNLOAD,
            )

        lyrics_applied = False
        if apply_lyrics and new_lyrics:
            seen: set[Path] = set()
            for path in final_paths:
                resolved = path.resolve()
                if resolved in seen or not path.is_file():
                    continue
                seen.add(resolved)
                try:
                    write_lyrics_sidecar(path, new_lyrics)
                    write_embedded_lyrics(path, new_lyrics)
                    lyrics_applied = True
                except Exception as e:
                    warnings.append(f"lyrics write failed for {path.name}: {e}")
            if cover_bytes:
                for path in final_paths:
                    try:
                        write_better_image(path.parent / "cover.jpg", cover_bytes)
                    except OSError:
                        pass

        if cover_bytes and not apply_lyrics:
            for path in final_paths:
                try:
                    write_better_image(path.parent / "cover.jpg", cover_bytes)
                except OSError:
                    pass

        catalog_lyrics = new_lyrics if apply_lyrics else record.lyrics
        self._catalog.upsert_track(
            video_id=video_id,
            title=merged.title,
            artist=merged.artist,
            album_artist=merged.album_artist,
            album=merged.album,
            track_number=merged.track_number,
            year=merged.year,
            cover_url=stored_cover_url,
            lyrics=catalog_lyrics,
            has_embedded_cover=cover_applied or record.has_embedded_cover,
            has_lyrics_embedded=lyrics_applied or record.has_lyrics_embedded,
            has_lyrics_sidecar=lyrics_applied or record.has_lyrics_sidecar,
            lyrics_source="manual" if (apply_lyrics and new_lyrics) else None,
        )

        index = TrackFileIndex(self._data_path)
        final_path = tag_source
        for path in final_paths:
            if path.is_file():
                final_path = path
                break
        index.set(video_id, final_path)

        return TrackTagUpdateResponse(
            video_id=video_id,
            title=merged.title,
            artist=merged.artist,
            album_artist=merged.album_artist,
            album=merged.album,
            year=merged.year,
            track_number=merged.track_number,
            cover_url=stored_cover_url,
            lyrics_applied=lyrics_applied,
            cover_applied=cover_applied,
            locations=location_updates,
            warnings=warnings,
        )
