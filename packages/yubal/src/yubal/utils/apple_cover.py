"""Apple / iTunes cover search and best-cover selection."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from rapidfuzz import fuzz

from yubal.utils.cover import fetch_cover
from yubal.utils.image_quality import cover_quality_score, image_dimensions

logger = logging.getLogger(__name__)

# High bar — wrong covers are worse than missing ones, but 88 was so strict
# that many real matches (esp. CN titles) never ran in the download pass.
_APPLE_ARTIST_MIN = 80
_APPLE_TITLE_MIN = 80
_ITUNES_SEARCH = "https://itunes.apple.com/search"


@dataclass(frozen=True)
class CoverCandidate:
    source: str
    data: bytes

    @property
    def score(self) -> int:
        return cover_quality_score(self.data)

    @property
    def dims(self) -> tuple[int, int] | None:
        return image_dimensions(self.data)


def _normalize(text: str) -> str:
    text = text.casefold().strip()
    text = re.sub(r"\s+", " ", text)
    # Drop common live / edition noise for matching only
    text = re.sub(
        r"[\(\[（].*?(live|remix|version|版|现场).*?[\)\]）]",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip()


def _hires_artwork_url(url: str) -> str:
    """Bump iTunes artworkUrl100 to the largest available size."""
    return re.sub(r"\d+x\d+([a-z]*)(\.[a-z]+)?$", r"1200x1200\1\2", url, count=1)


def artwork_size_from_url(url: str) -> tuple[int, int] | None:
    """Parse ``NNNxNNN`` size hints from an artwork URL, if present."""
    match = re.search(r"(\d{2,5})x(\d{2,5})", url or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def probe_image_dimensions(
    url: str | None, *, timeout: float = 10.0, max_bytes: int = 65536
) -> tuple[int, int] | None:
    """Best-effort remote dimensions without downloading the full image."""
    if not url:
        return None
    hinted = artwork_size_from_url(url)
    if hinted:
        return hinted
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            headers = {"Range": f"bytes=0-{max_bytes - 1}"}
            resp = client.get(url, headers=headers)
            if resp.status_code not in {200, 206}:
                return None
            return image_dimensions(resp.content)
    except Exception:
        logger.debug("probe_image_dimensions failed for %s", url, exc_info=True)
        return None


def search_apple_cover_url(
    *,
    artist: str,
    album: str | None,
    title: str,
    timeout: float = 15.0,
) -> tuple[str, int, int] | None:
    """Search iTunes for artwork URL + claimed size (no image download)."""
    queries: list[tuple[str, str]] = []
    if album and album.strip():
        queries.append((f"{artist} {album}".strip(), "album"))
    queries.append((f"{artist} {title}".strip(), "song"))
    queries.append((f"{artist} {title}".strip(), "album"))

    best_url: str | None = None
    best_score = 0

    for query, entity in queries:
        if not query:
            continue
        result = _itunes_search(
            query,
            artist=artist,
            name=album if entity == "album" and album else title,
            entity=entity,
            timeout=timeout,
        )
        if result is None:
            continue
        url, score = result
        if score > best_score:
            best_score = score
            best_url = url

    if not best_url:
        return None
    dims = artwork_size_from_url(best_url) or (1200, 1200)
    return best_url, dims[0], dims[1]


def search_apple_cover(
    *,
    artist: str,
    album: str | None,
    title: str,
    timeout: float = 15.0,
) -> bytes | None:
    """Search iTunes for a high-confidence album/song artwork.

    Returns image bytes only when artist + (album or title) match strongly.
    """
    meta = search_apple_cover_url(
        artist=artist, album=album, title=title, timeout=timeout
    )
    if meta is None:
        logger.debug(
            "Apple cover: no high-confidence match for '%s' / '%s'",
            artist,
            title,
        )
        return None
    best_url, _, _ = meta
    data = fetch_cover(best_url, timeout=timeout)
    if data:
        dims = image_dimensions(data)
        logger.info(
            "Apple cover matched for '%s' - '%s' (%s)",
            artist,
            title,
            f"{dims[0]}x{dims[1]}" if dims else "unknown",
        )
    return data


def _itunes_search(
    term: str,
    *,
    artist: str,
    name: str,
    entity: str,
    timeout: float,
) -> tuple[str, int] | None:
    params = {
        "term": term,
        "media": "music",
        "entity": entity,
        "limit": 5,
    }
    for country in ("CN", None):
        q = dict(params)
        if country:
            q["country"] = country
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(_ITUNES_SEARCH, params=q)
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            logger.debug("iTunes search failed (%s): %s", country, e)
            continue

        results = payload.get("results") or []
        picked = _pick_itunes_result(
            artist=artist, name=name, results=results, entity=entity
        )
        if picked is not None:
            return picked
    return None


def _pick_itunes_result(
    *,
    artist: str,
    name: str,
    results: list[dict[str, Any]],
    entity: str,
) -> tuple[str, int] | None:
    best: tuple[str, int] | None = None
    want_artist = _normalize(artist)
    want_name = _normalize(name)
    if not want_artist or not want_name:
        return None

    for item in results:
        artist_name = str(item.get("artistName") or "")
        if entity == "album":
            item_name = str(item.get("collectionName") or "")
        else:
            item_name = str(
                item.get("trackName") or item.get("collectionName") or ""
            )
        art = item.get("artworkUrl100") or item.get("artworkUrl60")
        if not art or not artist_name or not item_name:
            continue

        artist_score = fuzz.token_set_ratio(want_artist, _normalize(artist_name))
        name_score = fuzz.token_set_ratio(want_name, _normalize(item_name))
        if artist_score < _APPLE_ARTIST_MIN or name_score < _APPLE_TITLE_MIN:
            continue

        combined = (artist_score + name_score) // 2
        url = _hires_artwork_url(str(art))
        if best is None or combined > best[1]:
            best = (url, combined)
    return best


def select_best_cover(
    *,
    embedded: bytes | None,
    ytm_url: str | None,
    artist: str,
    album: str | None,
    title: str,
    allow_apple: bool = True,
    fetch_ytm: bool = True,
) -> CoverCandidate | None:
    """Compare available covers and return the highest quality.

    Args:
        allow_apple: When False, skip Apple/iTunes search entirely.
        fetch_ytm: When False, do not download ``ytm_url`` (use embedded only).
    """
    candidates: list[CoverCandidate] = []

    if embedded:
        candidates.append(CoverCandidate("embedded", embedded))

    if fetch_ytm:
        ytm = fetch_cover(ytm_url)
        if ytm:
            candidates.append(CoverCandidate("ytmusic", ytm))

    if allow_apple:
        apple = search_apple_cover(artist=artist, album=album, title=title)
        if apple:
            candidates.append(CoverCandidate("apple", apple))

    if not candidates:
        return None

    best = max(candidates, key=lambda c: c.score)
    dims = best.dims
    logger.info(
        "Best cover for '%s': %s (%s, score=%d) among %s",
        title,
        best.source,
        f"{dims[0]}x{dims[1]}" if dims else "unknown",
        best.score,
        ", ".join(f"{c.source}:{c.score}" for c in candidates),
    )
    return best
