"""Meta providers for wishlist search (no ytmid / no download)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from yubal.utils.normalize_text import normalize_artist_key, normalize_music_text

logger = logging.getLogger(__name__)

_UA = {
    "User-Agent": "yubal-wanted/1.0 (https://github.com/shengshk/yubal)",
    "Accept": "application/json",
}

_QQ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://y.qq.com/",
}

_QQ_SEARCH = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
_MB_SEARCH = "https://musicbrainz.org/ws/2/recording"
_DISCOGS_SEARCH = "https://api.discogs.com/database/search"
_LASTFM_SEARCH = "https://ws.audioscrobbler.com/2.0/"


@dataclass(frozen=True)
class MetaHit:
    source: str
    source_id: str
    title: str
    artist: str
    album: str | None
    source_url: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: int | None = None
    score: float | None = None

    @property
    def dedupe_key(self) -> str:
        return "|".join(
            [
                normalize_music_text(self.title),
                normalize_artist_key(self.artist),
                normalize_music_text(self.album or ""),
            ]
        )


def _get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> Any:
    with httpx.Client(timeout=timeout, headers=headers or _UA) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


def _safe_get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> Any | None:
    try:
        return _get_json(url, params=params, headers=headers, timeout=timeout)
    except Exception as exc:
        logger.info("Meta search failed %s: %s", url, exc)
        return None


def search_musicbrainz(
    query: str, *, limit: int = 8, raise_on_error: bool = False
) -> list[MetaHit]:
    getter = _get_json if raise_on_error else _safe_get_json
    data = getter(
        _MB_SEARCH,
        params={"query": query, "fmt": "json", "limit": limit},
        headers={**_UA, "Accept": "application/json"},
    )
    if not isinstance(data, dict):
        return []
    hits: list[MetaHit] = []
    for rec in data.get("recordings") or []:
        if not isinstance(rec, dict):
            continue
        title = str(rec.get("title") or "").strip()
        rid = str(rec.get("id") or "").strip()
        if not title or not rid:
            continue
        artists = []
        for ac in rec.get("artist-credit") or []:
            if isinstance(ac, dict):
                name = (
                    ac.get("name") or (ac.get("artist") or {}).get("name") or ""
                ).strip()
                if name:
                    artists.append(name)
        artist = " & ".join(artists) or "Unknown Artist"
        album = None
        release_id = None
        releases = rec.get("releases") or []
        if releases and isinstance(releases[0], dict):
            release = releases[0]
            album = str(release.get("title") or "").strip() or None
            release_id = str(release.get("id") or "").strip() or None
        length_ms = rec.get("length")
        duration = (
            int(length_ms / 1000) if isinstance(length_ms, (int, float)) else None
        )
        hits.append(
            MetaHit(
                source="musicbrainz",
                source_id=rid,
                title=title,
                artist=artist,
                album=album,
                source_url=f"https://musicbrainz.org/recording/{rid}",
                thumbnail_url=(
                    f"https://coverartarchive.org/release/{release_id}/front-250"
                    if release_id
                    else None
                ),
                duration_seconds=duration,
            )
        )
    return hits


def search_qq(
    query: str, *, limit: int = 8, raise_on_error: bool = False
) -> list[MetaHit]:
    getter = _get_json if raise_on_error else _safe_get_json
    data = getter(
        _QQ_SEARCH,
        params={"w": query, "format": "json", "p": 1, "n": limit, "cr": 1, "t": 0},
        headers=_QQ_HEADERS,
    )
    if not isinstance(data, dict):
        return []
    song_list = (
        ((data.get("data") or {}).get("song") or {}).get("list")
        if isinstance(data.get("data"), dict)
        else None
    )
    if not isinstance(song_list, list):
        return []
    hits: list[MetaHit] = []
    for song in song_list:
        if not isinstance(song, dict):
            continue
        title = str(song.get("songname") or "").strip()
        mid = str(song.get("songmid") or song.get("songid") or "").strip()
        if not title or not mid:
            continue
        singers = song.get("singer") or []
        names = []
        if isinstance(singers, list):
            for s in singers:
                if isinstance(s, dict) and s.get("name"):
                    names.append(str(s["name"]))
        artist = " & ".join(names) or "Unknown Artist"
        album = str(song.get("albumname") or "").strip() or None
        album_mid = str(song.get("albummid") or "").strip()
        interval = song.get("interval")
        duration = int(interval) if isinstance(interval, (int, float)) else None
        hits.append(
            MetaHit(
                source="qq",
                source_id=mid,
                title=title,
                artist=artist,
                album=album,
                source_url=f"https://y.qq.com/n/ryqq/songDetail/{mid}",
                thumbnail_url=(
                    f"https://y.qq.com/music/photo_new/T002R300x300M000{album_mid}.jpg"
                    if album_mid
                    else None
                ),
                duration_seconds=duration,
            )
        )
    return hits


def search_discogs(
    query: str, *, limit: int = 8, raise_on_error: bool = False
) -> list[MetaHit]:
    # Discogs requires a token for higher quotas; anonymous still works lightly.
    getter = _get_json if raise_on_error else _safe_get_json
    data = getter(
        _DISCOGS_SEARCH,
        params={"q": query, "type": "release", "per_page": limit},
        headers={
            **_UA,
            "User-Agent": "yubal-wanted/1.0 +https://github.com/shengshk/yubal",
        },
    )
    if not isinstance(data, dict):
        return []
    hits: list[MetaHit] = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        title_raw = str(item.get("title") or "").strip()
        rid = str(item.get("id") or "").strip()
        if not title_raw or not rid:
            continue
        # Discogs titles are often "Artist - Album"
        artist, album = title_raw, None
        if " - " in title_raw:
            artist, album = [p.strip() for p in title_raw.split(" - ", 1)]
        thumb = item.get("thumb") or item.get("cover_image")
        uri = item.get("uri")
        hits.append(
            MetaHit(
                source="discogs",
                source_id=rid,
                title=album or title_raw,
                artist=artist or "Unknown Artist",
                album=album,
                source_url=f"https://www.discogs.com{uri}" if uri else None,
                thumbnail_url=str(thumb) if thumb else None,
            )
        )
    return hits


def search_lastfm(
    query: str,
    *,
    limit: int = 8,
    api_key: str = "",
    raise_on_error: bool = False,
) -> list[MetaHit]:
    # Last.fm track.search needs an API key; without it return empty.
    key = (api_key or "").strip()
    if not key:
        logger.debug("Last.fm search skipped (no API key)")
        return []
    getter = _get_json if raise_on_error else _safe_get_json
    data = getter(
        _LASTFM_SEARCH,
        params={
            "method": "track.search",
            "track": query,
            "api_key": key,
            "format": "json",
            "limit": limit,
        },
    )
    if not isinstance(data, dict):
        return []
    matches = (
        ((data.get("results") or {}).get("trackmatches") or {}).get("track")
        if isinstance(data.get("results"), dict)
        else None
    )
    if not isinstance(matches, list):
        if isinstance(matches, dict):
            matches = [matches]
        else:
            return []
    hits: list[MetaHit] = []
    for track in matches:
        if not isinstance(track, dict):
            continue
        title = str(track.get("name") or "").strip()
        artist = str(track.get("artist") or "").strip() or "Unknown Artist"
        if not title:
            continue
        url = str(track.get("url") or "").strip() or None
        mbid = str(track.get("mbid") or "").strip()
        source_id = mbid or f"{artist}|{title}"
        images = track.get("image") or []
        thumb = None
        if isinstance(images, list) and images:
            last = images[-1]
            if isinstance(last, dict):
                thumb = last.get("#text") or None
        hits.append(
            MetaHit(
                source="lastfm",
                source_id=source_id[:128],
                title=title,
                artist=artist,
                album=None,
                source_url=url,
                thumbnail_url=str(thumb) if thumb else None,
            )
        )
    return hits


_SOURCE_PRIORITY = {
    "musicbrainz": 0,
    "qq": 1,
    "discogs": 2,
    "lastfm": 3,
}

_DEDUPE_DURATION_TOLERANCE_SECONDS = 8


def same_recording(
    *,
    left_title: str,
    left_artist: str,
    left_duration: int | None,
    right_title: str,
    right_artist: str,
    right_duration: int | None,
) -> bool:
    """Return whether two search hits represent the same usable recording.

    Version markers remain part of the normalized title, so ``Live``,
    ``Remix`` and similar variants stay separate. Duration only vetoes a
    match when both providers supplied it and the gap is meaningful.
    """
    left_title_key = normalize_music_text(left_title)
    right_title_key = normalize_music_text(right_title)
    if not left_title_key or left_title_key != right_title_key:
        return False

    left_artist_key = normalize_artist_key(left_artist)
    right_artist_key = normalize_artist_key(right_artist)
    if not left_artist_key or left_artist_key != right_artist_key:
        return False

    if left_duration is not None and right_duration is not None:
        if (
            abs(int(left_duration) - int(right_duration))
            > _DEDUPE_DURATION_TOLERANCE_SECONDS
        ):
            return False
    return True


def _hit_preference(hit: MetaHit) -> tuple[int, int, int, int]:
    return (
        _SOURCE_PRIORITY.get(hit.source, 9),
        0 if hit.album else 1,
        0 if hit.thumbnail_url else 1,
        0 if hit.duration_seconds is not None else 1,
    )


def merge_meta_hits(groups: list[list[MetaHit]], *, limit: int) -> list[MetaHit]:
    """Merge provider hits by recording; prefer richer, prioritized sources."""
    best: list[MetaHit] = []
    for group in groups:
        for hit in group:
            if not normalize_music_text(hit.title) or not normalize_artist_key(
                hit.artist
            ):
                continue
            duplicate_index = next(
                (
                    index
                    for index, previous in enumerate(best)
                    if same_recording(
                        left_title=previous.title,
                        left_artist=previous.artist,
                        left_duration=previous.duration_seconds,
                        right_title=hit.title,
                        right_artist=hit.artist,
                        right_duration=hit.duration_seconds,
                    )
                ),
                None,
            )
            if duplicate_index is None:
                best.append(hit)
                continue
            if _hit_preference(hit) < _hit_preference(best[duplicate_index]):
                best[duplicate_index] = hit
    ordered = sorted(
        best,
        key=lambda h: (_SOURCE_PRIORITY.get(h.source, 9), h.title.lower()),
    )
    return ordered[:limit]


def search_meta_sources(
    query: str,
    *,
    limit: int,
    enable_musicbrainz: bool = True,
    enable_qq: bool = True,
    enable_discogs: bool = False,
    enable_lastfm: bool = False,
    lastfm_api_key: str = "",
) -> list[MetaHit]:
    """Query enabled providers and return merged top ``limit`` hits."""
    q = re.sub(r"\s+", " ", (query or "").strip())
    if not q:
        return []
    per = max(limit, 5)
    groups: list[list[MetaHit]] = []
    if enable_musicbrainz:
        groups.append(search_musicbrainz(q, limit=per))
    if enable_qq:
        groups.append(search_qq(q, limit=per))
    if enable_discogs:
        groups.append(search_discogs(q, limit=per))
    if enable_lastfm:
        groups.append(search_lastfm(q, limit=per, api_key=lastfm_api_key))
    return merge_meta_hits(groups, limit=limit)


def meta_source_url(source: str, source_id: str) -> str | None:
    if not source_id:
        return None
    if source == "musicbrainz":
        return f"https://musicbrainz.org/recording/{source_id}"
    if source == "qq":
        return f"https://y.qq.com/n/ryqq/songDetail/{source_id}"
    if source == "discogs":
        return f"https://www.discogs.com/release/{source_id}"
    if source == "lastfm":
        return f"https://www.last.fm/search?q={quote(source_id)}"
    return None
