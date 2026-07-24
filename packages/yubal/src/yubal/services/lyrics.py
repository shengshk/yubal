"""Lyrics fetching service with composable sources (lrclib, YouTube Music)."""

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from ytmusicapi.exceptions import YTMusicError
from ytmusicapi.models.lyrics import LyricLine

from yubal.client import YTMusicProtocol

logger = logging.getLogger(__name__)

# LRC timestamp like [mm:ss.xx] or [mm:ss]
_LRC_TS_RE = re.compile(r"\[\d{1,3}:\d{2}(?:\.\d{1,3})?]")


def lyrics_look_synced(text: str | None) -> bool:
    """True when lyrics contain at least one LRC-style timestamp."""
    if not text or not text.strip():
        return False
    return _LRC_TS_RE.search(text) is not None

# LRC timestamp like [mm:ss.xx] or [mm:ss]
_LRC_TS_RE = re.compile(r"\[\d{1,3}:\d{2}(?:\.\d{1,3})?]")


def lyrics_look_synced(text: str | None) -> bool:
    """True when lyrics contain at least one LRC-style timestamp."""
    if not text or not text.strip():
        return False
    return _LRC_TS_RE.search(text) is not None



@dataclass(frozen=True)
class LyricsQuery:
    """A lyrics lookup request.

    Sources may use different fields (lrclib needs title/artist/duration,
    YouTube Music needs video_id), so the query carries all of them.
    """

    title: str
    artist: str
    duration_seconds: int
    video_id: str | None = None


class LyricsFetcher(Protocol):
    """Protocol for a single lyrics source."""

    name: str

    def fetch(self, query: LyricsQuery) -> str | None:
        """Return LRC-formatted (or plain) lyrics text, or None if unavailable."""
        ...


class LyricsServiceProtocol(Protocol):
    """Protocol for lyrics fetching services.

    Enables dependency injection and testing of lyrics functionality.
    """

    def fetch_lyrics(
        self,
        title: str,
        artist: str,
        duration_seconds: int,
        video_id: str | None = None,
        *,
        skip_sources: set[str] | None = None,
    ) -> tuple[str | None, str | None, list[str]]:
        """Fetch lyrics for a track.

        Returns (lyrics, hit_source, missed_sources).
        """
        ...

    def save_lyrics(self, lyrics: str, audio_path: Path) -> Path:
        """Save lyrics to disk."""
        ...


class LrclibFetcher:
    """Fetches synced lyrics from lrclib.net.

    lrclib.net is a free, open lyrics database that provides time-synced
    lyrics in LRC format. This fetcher matches by track title, artist, and
    duration; synced lyrics are preferred over plain.
    """

    name = "lrclib"
    LRCLIB_API = "https://lrclib.net/api/get"
    TIMEOUT = 10  # seconds

    def fetch(self, query: LyricsQuery) -> str | None:
        """Fetch synced lyrics for a track from lrclib.net.

        Returns:
            LRC-formatted lyrics string if found, None otherwise.
            Returns synced lyrics if available, falls back to plain lyrics.
        """
        params = {
            "track_name": query.title,
            "artist_name": query.artist,
            "duration": query.duration_seconds,
        }

        try:
            response = httpx.get(
                self.LRCLIB_API,
                params=params,
                timeout=self.TIMEOUT,
            )

            if response.status_code == 404:
                logger.debug(
                    "No lrclib lyrics for '%s' by %s",
                    query.title,
                    query.artist,
                )
                return None

            response.raise_for_status()
            data = response.json()

            lyrics = data.get("syncedLyrics") or data.get("plainLyrics")

            if not lyrics:
                logger.debug(
                    "Empty lrclib lyrics response for '%s' by %s",
                    query.title,
                    query.artist,
                )
                return None

            return lyrics

        except httpx.TimeoutException:
            logger.debug(
                "lrclib lyrics fetch timed out for '%s' by %s",
                query.title,
                query.artist,
            )
            return None
        except httpx.HTTPStatusError as e:
            logger.debug(
                "lrclib lyrics HTTP error for '%s' by %s: %s",
                query.title,
                query.artist,
                e,
            )
            return None
        except httpx.RequestError as e:
            logger.debug(
                "lrclib lyrics request error for '%s' by %s: %s",
                query.title,
                query.artist,
                e,
            )
            return None


def _format_lrc_timestamp(ms: int) -> str:
    """Format milliseconds as an LRC timestamp tag `[mm:ss.xx]`.

    Centiseconds are `ms // 10`, capped to 99. Negative values clamp to zero.
    Minutes overflow naturally past 60 (LRC convention).
    """
    ms = max(ms, 0)
    total_s, sub_ms = divmod(ms, 1000)
    cs = sub_ms // 10
    minutes, seconds = divmod(total_s, 60)
    return f"[{minutes:02d}:{seconds:02d}.{cs:02d}]"


def _timed_lyrics_to_lrc(lines: Sequence[LyricLine]) -> str:
    """Convert a list of LyricLine objects to LRC-formatted text."""
    return "\n".join(
        f"{_format_lrc_timestamp(line.start_time)}{line.text}" for line in lines
    )


def _payload_to_lrc(payload: Mapping[str, Any]) -> str | None:
    """Convert a ytmusicapi `get_lyrics` payload to LRC or plain text.

    When `hasTimestamps` is True, formats each `LyricLine` with `[mm:ss.xx]`.
    Otherwise returns the raw plain-text `lyrics` string verbatim.
    """
    lyrics = payload.get("lyrics")
    if not lyrics:
        return None

    if payload.get("hasTimestamps") and isinstance(lyrics, list):
        if not lyrics:
            return None
        return _timed_lyrics_to_lrc(lyrics)

    if isinstance(lyrics, str):
        return lyrics or None

    return None


class YouTubeMusicLyricsFetcher:
    """Fetches lyrics from YouTube Music via ytmusicapi.

    Uses `get_watch_playlist(video_id)` to obtain the lyrics browseId, then
    `get_lyrics(browse_id, timestamps=True)` to retrieve timed lyrics when
    available. Falls back to plain text if YouTube Music has no timestamps.

    All failures (no lyrics tab, API error, malformed payload) are non-fatal:
    they are logged at DEBUG and the fetcher returns None.
    """

    name = "YouTube Music"

    def __init__(self, client: YTMusicProtocol) -> None:
        self._client = client

    def fetch(self, query: LyricsQuery) -> str | None:
        if not query.video_id:
            return None

        try:
            browse_id = self._client.get_lyrics_browse_id(query.video_id)
            if not browse_id:
                logger.debug("No YT Music lyrics tab for video %s", query.video_id)
                return None

            payload = self._client.get_lyrics(browse_id)
            if not payload:
                return None

            return _payload_to_lrc(payload)
        except (YTMusicError, KeyError, TypeError, ValueError) as e:
            logger.debug("YT Music lyrics fetch failed for %s: %s", query.video_id, e)
            return None


class LyricsService:
    """Composite lyrics service trying multiple sources in order.

    Iterates the configured fetchers and returns the first non-None result.
    Defaults to a single `LrclibFetcher` for backward compatibility.

    Example:
        >>> service = LyricsService(fetchers=[LrclibFetcher()])
        >>> lyrics = service.fetch_lyrics("Bohemian Rhapsody", "Queen", 354)
        >>> if lyrics:
        ...     service.save_lyrics(lyrics, Path("01 - Bohemian Rhapsody.opus"))
    """

    def __init__(self, fetchers: Sequence[LyricsFetcher] | None = None) -> None:
        """Initialize with an ordered list of fetchers.

        Args:
            fetchers: Fetchers tried in order; first non-None wins.
                      Defaults to a single LrclibFetcher.
        """
        self._fetchers: list[LyricsFetcher] = (
            list(fetchers) if fetchers is not None else [LrclibFetcher()]
        )

    def fetch_lyrics(
        self,
        title: str,
        artist: str,
        duration_seconds: int,
        video_id: str | None = None,
        *,
        skip_sources: set[str] | None = None,
    ) -> tuple[str | None, str | None, list[str]]:
        """Try each fetcher in order; prefer synced lyrics in the same pass.

        Plain (unsynced) hits do not stop the chain — later sources are still
        tried for timestamps. If nobody returns synced text, the first plain
        hit is kept as a fallback.

        Returns:
            (lyrics_or_none, hit_source_or_none, missed_sources_tried)
        """
        skip = skip_sources or set()
        query = LyricsQuery(
            title=title,
            artist=artist,
            duration_seconds=duration_seconds,
            video_id=video_id,
        )
        missed: list[str] = []
        tried_names: list[str] = []
        plain_lyrics: str | None = None
        plain_source: str | None = None

        for i, fetcher in enumerate(self._fetchers):
            if fetcher.name in skip:
                continue
            tried_names.append(fetcher.name)
            try:
                lyrics = fetcher.fetch(query)
            except Exception:
                logger.debug(
                    "Lyrics source %s failed for '%s' by %s",
                    fetcher.name,
                    title,
                    artist,
                    exc_info=True,
                )
                lyrics = None
            if lyrics is None:
                missed.append(fetcher.name)
                remaining = [
                    f for f in self._fetchers[i + 1 :] if f.name not in skip
                ]
                if remaining:
                    logger.info(
                        "No lyrics from %s for '%s' by %s, falling back to %s",
                        fetcher.name,
                        title,
                        artist,
                        remaining[0].name,
                    )
                continue

            if lyrics_look_synced(lyrics):
                logger.info(
                    "Found synced lyrics from %s for '%s' by %s",
                    fetcher.name,
                    title,
                    artist,
                )
                return lyrics, fetcher.name, missed

            if plain_lyrics is None:
                plain_lyrics = lyrics
                plain_source = fetcher.name
                logger.info(
                    "Plain lyrics from %s for '%s' by %s; "
                    "continuing for synced sources",
                    fetcher.name,
                    title,
                    artist,
                )
            else:
                logger.debug(
                    "Ignoring additional plain lyrics from %s for '%s'",
                    fetcher.name,
                    title,
                )
            remaining = [
                f for f in self._fetchers[i + 1 :] if f.name not in skip
            ]
            if remaining:
                logger.info(
                    "Falling back from plain %s to %s for '%s' by %s",
                    fetcher.name,
                    remaining[0].name,
                    title,
                    artist,
                )

        if plain_lyrics is not None:
            logger.info(
                "Using plain lyrics from %s for '%s' by %s "
                "(no synced source hit)",
                plain_source,
                title,
                artist,
            )
            return plain_lyrics, plain_source, missed

        if tried_names:
            logger.info(
                "No lyrics found for '%s' by %s (tried: %s)",
                title,
                artist,
                ", ".join(tried_names),
            )
        return None, None, missed

    def save_lyrics(self, lyrics: str, audio_path: Path) -> Path:
        """Save lyrics to an LRC file alongside the audio file.

        Creates an .lrc file with the same name as the audio file.
        For example, "01 - Track.opus" becomes "01 - Track.lrc".

        Returns:
            Path to the saved .lrc file.
        """
        lrc_path = audio_path.with_suffix(".lrc")
        lrc_path.write_text(lyrics, encoding="utf-8")
        return lrc_path
