"""QQ Music lyrics fetcher with strict title/artist matching."""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

import httpx
from rapidfuzz import fuzz

from yubal.services.lyrics import LyricsQuery

logger = logging.getLogger(__name__)

_TITLE_MIN = 90
_ARTIST_MIN = 85
_DURATION_TOLERANCE_SEC = 3

_SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
_LYRIC_URL = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://y.qq.com/",
}


def _normalize(text: str) -> str:
    text = text.casefold().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(
        r"[\(\[（].*?(live|remix|version|版|现场).*?[\)\]）]",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip()


class QQMusicLyricsFetcher:
    """Fetch lyrics from QQ Music only when match confidence is high."""

    name = "qq"

    def __init__(self, timeout: float = 12.0) -> None:
        self._timeout = timeout

    def fetch(self, query: LyricsQuery) -> str | None:
        songmid = self._search_songmid(query)
        if not songmid:
            return None
        lyrics = self._fetch_lyric(songmid)
        if lyrics:
            logger.info(
                "Found lyrics from qq for '%s' by %s",
                query.title,
                query.artist,
            )
        return lyrics

    def _search_songmid(self, query: LyricsQuery) -> str | None:
        params = {
            "w": f"{query.artist} {query.title}",
            "format": "json",
            "p": 1,
            "n": 8,
            "cr": 1,
            "t": 0,
        }
        try:
            with httpx.Client(timeout=self._timeout, headers=_HEADERS) as client:
                resp = client.get(_SEARCH_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as e:
            logger.debug("QQ search failed for '%s': %s", query.title, e)
            return None

        songs = (
            ((data.get("data") or {}).get("song") or {}).get("list") or []
        )
        if not isinstance(songs, list):
            return None

        want_title = _normalize(query.title)
        want_artist = _normalize(query.artist)
        best_mid: str | None = None
        best_score = 0

        for song in songs:
            if not isinstance(song, dict):
                continue
            title = str(song.get("songname") or "")
            singers = song.get("singer") or []
            if isinstance(singers, list):
                artist = " / ".join(
                    str(s.get("name") or "") for s in singers if isinstance(s, dict)
                )
            else:
                artist = ""
            mid = str(song.get("songmid") or "")
            if not title or not mid:
                continue

            title_score = fuzz.token_set_ratio(want_title, _normalize(title))
            artist_score = fuzz.token_set_ratio(want_artist, _normalize(artist))
            if title_score < _TITLE_MIN or artist_score < _ARTIST_MIN:
                logger.debug(
                    "QQ reject '%s' by %s (title=%d artist=%d)",
                    title,
                    artist,
                    title_score,
                    artist_score,
                )
                continue

            # Interval is in seconds on this endpoint
            interval = song.get("interval")
            if (
                query.duration_seconds
                and isinstance(interval, (int, float))
                and interval > 0
            ):
                if abs(int(interval) - query.duration_seconds) > _DURATION_TOLERANCE_SEC:
                    logger.debug(
                        "QQ reject '%s' duration %s vs %s",
                        title,
                        interval,
                        query.duration_seconds,
                    )
                    continue

            combined = (title_score * 2 + artist_score) // 3
            if combined > best_score:
                best_score = combined
                best_mid = mid

        if best_mid is None:
            logger.debug(
                "QQ: no high-confidence match for '%s' by %s",
                query.title,
                query.artist,
            )
        return best_mid

    def _fetch_lyric(self, songmid: str) -> str | None:
        params = {
            "songmid": songmid,
            "format": "json",
            "nobase64": 1,
            "g_tk": 5381,
        }
        try:
            with httpx.Client(timeout=self._timeout, headers=_HEADERS) as client:
                resp = client.get(_LYRIC_URL, params=params)
                resp.raise_for_status()
                text = resp.text.strip()
                # Sometimes JSONP
                if text.startswith("callback(") or text.startswith("MusicJsonCallback("):
                    text = text[text.find("(") + 1 : text.rfind(")")]
                data: dict[str, Any] = json.loads(text)
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as e:
            logger.debug("QQ lyric fetch failed for %s: %s", songmid, e)
            return None

        lyric = data.get("lyric")
        if not lyric:
            return None
        if isinstance(lyric, str):
            # Some responses still base64 even with nobase64=1
            decoded = lyric
            if not lyric.lstrip().startswith("["):
                try:
                    decoded = base64.b64decode(lyric).decode("utf-8", errors="ignore")
                except Exception:
                    decoded = lyric
            decoded = decoded.strip()
            return decoded or None
        return None
