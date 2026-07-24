"""Search and resolve track metadata suggestions for the edit-tags modal."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from yubal import APIConfig
from yubal.client import YTMusicClient
from yubal.lib.matching import match_artists, match_title
from yubal.models.enums import MatchResult
from yubal.models.ytmusic import Artist
from yubal.services.extractor import MetadataExtractorService
from yubal.services.lyrics import (
    LrclibFetcher,
    LyricsFetcher,
    LyricsService,
    YouTubeMusicLyricsFetcher,
)
from yubal.services.qq_lyrics import QQMusicLyricsFetcher

from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.schemas.track_metadata import (
    MetadataCandidate,
    MetadataSearchResponse,
    MetadataSuggestion,
)
from yubal_api.services.preferences import PreferencesStore

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://", re.IGNORECASE)


class TrackMetadataService:
    """Stateless metadata scrape helpers (does not touch the global search snapshot)."""

    def __init__(
        self,
        *,
        catalog: TrackCatalogRepository,
        cookies_path: Path | None,
        preferences: PreferencesStore,
    ) -> None:
        self._catalog = catalog
        self._cookies_path = cookies_path
        self._preferences = preferences
        self._client = YTMusicClient(
            config=APIConfig(search_limit=10),
            cookies_path=cookies_path,
        )
        self._extractor = MetadataExtractorService(self._client)

    def default_query(self, video_id: str) -> str:
        record = self._catalog.get_track(video_id)
        if record is None:
            raise FileNotFoundError(f"track not found: {video_id}")
        parts = [record.artist or "", record.title or ""]
        return " ".join(p.strip() for p in parts if p and p.strip())

    def search(
        self,
        video_id: str,
        query: str | None = None,
    ) -> MetadataSearchResponse:
        record = self._catalog.get_track(video_id)
        if record is None:
            raise FileNotFoundError(f"track not found: {video_id}")

        default = self._build_default_query(record.artist, record.title)
        normalized = self._validate_query(query if query is not None else default)
        if not normalized:
            raise ValueError("search query must not be empty")

        logger.info("Track metadata search for %s: %r", video_id, normalized)
        results = self._client.search_songs(normalized)[:10]

        target_artists = [
            Artist(name=part)
            for part in re.split(r"\s*/\s*|\s*&\s*|\s*;\s*", record.artist or "")
            if part.strip()
        ] or [Artist(name=record.artist or "Unknown Artist")]

        target_title = record.title or ""
        target_artist_str = record.artist or ""

        candidates: list[MetadataCandidate] = []
        for rank, result in enumerate(results, start=1):
            artists = " & ".join(a.name for a in result.artists if a.name)
            candidate_artist_str = artists or "Unknown Artist"

            # Score normally, then again with title/artist swapped in case the
            # candidate's tags have title and artist mixed up — take the max
            # of the two so genuinely-swapped results still rank well.
            title_m = match_title(target_title, result.title)
            swapped_title_m = match_title(target_title, candidate_artist_str)
            title_score = max(title_m.similarity, swapped_title_m.similarity)

            artist_m = match_artists(target_artists, list(result.artists))
            swapped_artist_m = match_title(target_artist_str, result.title)
            artist_score = max(artist_m.best_score, swapped_artist_m.similarity)

            thumb = result.thumbnails[-1].url if result.thumbnails else None
            candidates.append(
                MetadataCandidate(
                    rank=rank,
                    candidate_video_id=result.video_id,
                    title=result.title,
                    artist=candidate_artist_str,
                    album=result.album.name if result.album else None,
                    album_id=result.album.id if result.album else None,
                    thumbnail_url=thumb,
                    duration_seconds=result.duration_seconds,
                    title_score=round(title_score, 1),
                    artist_score=round(artist_score, 1),
                )
            )

        return MetadataSearchResponse(
            query=normalized,
            default_query=default,
            candidates=candidates,
        )

    def resolve(
        self,
        video_id: str,
        candidate_video_id: str,
        *,
        fetch_lyrics: bool = True,
    ) -> MetadataSuggestion:
        record = self._catalog.get_track(video_id)
        if record is None:
            raise FileNotFoundError(f"track not found: {video_id}")

        track = self._client.get_track(candidate_video_id)
        metadata, _skip = self._extractor._extract_single_track(track)
        if metadata is None:
            # Fallback to raw watch-playlist fields when extract skips (UGC etc.)
            artists = [a.name for a in track.artists if a.name] or ["Unknown Artist"]
            album = track.album.name if track.album else track.title
            thumb = track.thumbnails[-1].url if track.thumbnails else None
            suggestion = MetadataSuggestion(
                candidate_video_id=candidate_video_id,
                title=track.title,
                artist=" / ".join(artists),
                album_artist=" / ".join(artists),
                album=album or track.title,
                year=self._client.get_upload_year(candidate_video_id),
                track_number=None,
                total_tracks=None,
                cover_url=thumb,
                match_result=MatchResult.UNMATCHED.value,
                source="fallback",
            )
        else:
            suggestion = MetadataSuggestion(
                candidate_video_id=candidate_video_id,
                title=metadata.title,
                artist=metadata.artist,
                album_artist=metadata.album_artist,
                album=metadata.album,
                year=metadata.year,
                track_number=metadata.track_number,
                total_tracks=metadata.total_tracks,
                cover_url=metadata.cover_url,
                match_result=metadata.match_result.value,
                source=(
                    "album"
                    if metadata.match_result == MatchResult.MATCHED
                    else metadata.match_result.value
                ),
            )

        if fetch_lyrics:
            lyrics, source = self._fetch_lyrics(
                title=suggestion.title,
                artist=suggestion.artist.split(" / ")[0],
                duration_seconds=track.duration_seconds,
                lyrics_video_id=candidate_video_id,
            )
            suggestion = suggestion.model_copy(
                update={"lyrics": lyrics, "lyrics_source": source}
            )

        return suggestion

    def _fetch_lyrics(
        self,
        *,
        title: str,
        artist: str,
        duration_seconds: int | None,
        lyrics_video_id: str,
    ) -> tuple[str | None, str | None]:
        if not duration_seconds and not lyrics_video_id:
            return None, None
        prefs = self._preferences.effective()
        if not prefs.fetch_lyrics:
            return None, None
        fetchers: list[LyricsFetcher] = [LrclibFetcher()]
        if prefs.ytmusic_lyrics_fallback:
            fetchers.append(YouTubeMusicLyricsFetcher(self._client))
        if prefs.qq_lyrics_fallback:
            fetchers.append(QQMusicLyricsFetcher())
        service = LyricsService(fetchers=fetchers)
        try:
            lyrics, source, _missed = service.fetch_lyrics(
                title=title,
                artist=artist,
                duration_seconds=int(duration_seconds or 0),
                video_id=lyrics_video_id,
            )
        except Exception:
            logger.exception("Lyrics scrape failed for %s", lyrics_video_id)
            return None, None
        if not lyrics:
            return None, None
        return lyrics, source or "lrclib"

    @staticmethod
    def _build_default_query(artist: str | None, title: str | None) -> str:
        return " ".join(
            p.strip() for p in (artist or "", title or "") if p and p.strip()
        )

    @staticmethod
    def _validate_query(query: str) -> str:
        normalized = " ".join(query.strip().split())
        if len(normalized) > 200:
            raise ValueError("search query must contain 1 to 200 characters")
        if _URL_RE.search(normalized):
            raise ValueError("metadata search accepts text, not URLs")
        if any(ord(char) < 32 for char in normalized):
            raise ValueError("search query contains control characters")
        return normalized
