"""Loose meta-tag verification against Wanted-enabled catalog sources."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace

from yubal.lib.matching import match_artists, match_title
from yubal.models.ytmusic import Artist
from yubal.utils.normalize_text import normalize_artist_key, normalize_music_text

from yubal_api.services.meta_search import (
    MetaHit,
    search_discogs,
    search_lastfm,
    search_musicbrainz,
    search_qq,
)

logger = logging.getLogger(__name__)

_DURATION_MIN_SOFT_TOLERANCE_SEC = 5.0
_DURATION_MAX_SOFT_TOLERANCE_SEC = 10.0
_DURATION_SOFT_RATIO = 0.02
_DURATION_MIN_HARD_TOLERANCE_SEC = 15.0
_DURATION_HARD_RATIO = 0.08
_META_SCORE_TITLE_WEIGHT = 0.45
_META_SCORE_ARTIST_WEIGHT = 0.30
_META_SCORE_ALBUM_WEIGHT = 0.15
_META_SCORE_DURATION_WEIGHT = 0.10
_ARTIST_SPLIT_RE = re.compile(
    r"\s*/\s*|\s*&\s*|\s*;\s*|\s*,\s*|\s+feat\.?\s+|\s+ft\.?\s+",
    re.IGNORECASE,
)

_VERSION_SUFFIX_RE = re.compile(
    r"\s*[\(\[]?\s*(?:"
    r"deluxe|expanded|remaster(?:ed)?|anniversary|special|limited|"
    r"edition|version|bonus|explicit|clean|live|acoustic|"
    r"豪华版|精装版|特别版|纪念版|现场版|珍藏版|数字版|版"
    r")\b.*$",
    re.IGNORECASE,
)


def meta_fingerprint(title: str, artists: str, album: str) -> str:
    return "|".join(
        [
            normalize_music_text(title),
            normalize_artist_key(artists),
            normalize_music_text(album),
        ]
    )


def _strip_version(text: str) -> str:
    base = normalize_music_text(text)
    if not base:
        return ""
    stripped = _VERSION_SUFFIX_RE.sub("", base).strip()
    return stripped or base


def _token_set(text: str) -> set[str]:
    return {t for t in text.split() if t}


def _soft_text_equal(a: str, b: str, *, album: bool = False) -> bool:
    """Title/album equality with 简繁/punct fold and bilingual containment."""
    na = _strip_version(a) if album else normalize_music_text(a)
    nb = _strip_version(b) if album else normalize_music_text(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # Bilingual / partial: one side contains the other after fold.
    if len(na) >= 2 and len(nb) >= 2 and (na in nb or nb in na):
        return True
    ta, tb = _token_set(na), _token_set(nb)
    if not ta or not tb:
        return False
    overlap = len(ta & tb) / max(len(ta), len(tb))
    return overlap >= 0.8


def _soft_artist_equal(a: str, b: str) -> bool:
    ka = normalize_artist_key(a)
    kb = normalize_artist_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    ta, tb = set(ka.split()), set(kb.split())
    if not ta or not tb:
        return False
    # Order-insensitive; allow subset (featuring extras on one side).
    if ta <= tb or tb <= ta:
        return True
    overlap = len(ta & tb) / max(len(ta), len(tb))
    return overlap >= 0.8


def _soft_album_equal(a: str, b: str, *, allow_empty: bool) -> bool:
    """Album soft match; empty either side passes when ``allow_empty``."""
    na = _strip_version(a)
    nb = _strip_version(b)
    if not na or not nb:
        return allow_empty
    return _soft_text_equal(a, b, album=True)


def _duration_delta(
    local_ms: int | None, hit_sec: int | None
) -> tuple[float, float, float] | None:
    """Return delta plus soft/hard tolerances when both durations are known."""
    if local_ms is None or local_ms <= 0 or hit_sec is None or hit_sec <= 0:
        return None
    local_sec = local_ms / 1000.0
    delta = abs(local_sec - float(hit_sec))
    soft = max(
        _DURATION_MIN_SOFT_TOLERANCE_SEC,
        min(_DURATION_MAX_SOFT_TOLERANCE_SEC, local_sec * _DURATION_SOFT_RATIO),
    )
    hard = max(_DURATION_MIN_HARD_TOLERANCE_SEC, local_sec * _DURATION_HARD_RATIO)
    return delta, soft, hard


def _duration_close(local_ms: int | None, hit_sec: int | None) -> bool | None:
    """True for a small edition/encoding difference, False for a clear mismatch."""
    measured = _duration_delta(local_ms, hit_sec)
    if measured is None:
        return None
    delta, soft, _hard = measured
    return delta <= soft


def _artist_parts(value: str) -> list[Artist]:
    parts = [
        part.strip() for part in _ARTIST_SPLIT_RE.split(value or "") if part.strip()
    ]
    return [Artist(name=part) for part in parts] or [Artist(name=value or "Unknown")]


def meta_candidate_score(
    *,
    title: str,
    artists: str,
    album: str,
    hit: MetaHit,
    duration_ms: int | None = None,
) -> float:
    """Comparable 0-100 confidence used by every metadata provider."""
    title_match = match_title(title, hit.title)
    title_score = max(title_match.similarity, title_match.base_similarity)
    artist_score = match_artists(
        _artist_parts(artists),
        _artist_parts(hit.artist),
    ).best_score
    if album and hit.album:
        album_match = match_title(album, hit.album)
        album_score = max(album_match.similarity, album_match.base_similarity)
    else:
        album_score = 0.0

    measured = _duration_delta(duration_ms, hit.duration_seconds)
    duration_score = 0.0
    if measured is not None:
        delta, soft, hard = measured
        if delta <= soft:
            duration_score = 100.0
        elif delta < hard:
            duration_score = max(0.0, 100.0 * (hard - delta) / (hard - soft))

    score = (
        title_score * _META_SCORE_TITLE_WEIGHT
        + artist_score * _META_SCORE_ARTIST_WEIGHT
        + album_score * _META_SCORE_ALBUM_WEIGHT
        + duration_score * _META_SCORE_DURATION_WEIGHT
    )
    return round(max(0.0, min(100.0, score)), 1)


def tags_soft_match(
    *,
    title: str,
    artists: str,
    album: str,
    hit: MetaHit,
    duration_ms: int | None = None,
) -> bool:
    """Title+artist required; duration (±3s) preferred; album gated by duration.

    - Both durations present and within ±3s → album may be empty / soft.
    - Otherwise → album must soft-match (both non-empty).
    """
    if not _soft_text_equal(title, hit.title):
        return False
    if not _soft_artist_equal(artists, hit.artist):
        return False

    measured = _duration_delta(duration_ms, hit.duration_seconds)
    if measured is not None and measured[0] > measured[2]:
        return False
    dur_ok = _duration_close(duration_ms, hit.duration_seconds)
    if dur_ok is True:
        return _soft_album_equal(album, hit.album or "", allow_empty=True)
    # No duration, or a moderate edition difference: require album agreement.
    return _soft_album_equal(album, hit.album or "", allow_empty=False)


@dataclass(frozen=True)
class MetaVerifyResult:
    hit: MetaHit | None
    rejected: bool
    # True when at least one enabled source failed transport/parse and no hit.
    errored: bool = False


def _search_one_source(
    source: str,
    query: str,
    *,
    limit: int,
    lastfm_api_key: str = "",
) -> tuple[list[MetaHit], bool]:
    """Return ``(hits, errored)``. ``errored`` means the provider call failed."""
    try:
        if source == "musicbrainz":
            return (
                search_musicbrainz(query, limit=limit, raise_on_error=True),
                False,
            )
        if source == "qq":
            return search_qq(query, limit=limit, raise_on_error=True), False
        if source == "discogs":
            return (
                search_discogs(query, limit=limit, raise_on_error=True),
                False,
            )
        if source == "lastfm":
            return (
                search_lastfm(
                    query,
                    limit=limit,
                    api_key=lastfm_api_key,
                    raise_on_error=True,
                ),
                False,
            )
    except Exception as exc:
        logger.info("Meta verify source %s failed: %s", source, exc)
        return [], True
    return [], False


# Prefer QQ for CJK catalogs when both match; otherwise source order as listed.
_VERIFY_SOURCE_ORDER = ("qq", "musicbrainz", "discogs", "lastfm")


def _enabled_sources(
    *,
    enable_musicbrainz: bool,
    enable_qq: bool,
    enable_discogs: bool,
    enable_lastfm: bool,
) -> dict[str, bool]:
    return {
        "musicbrainz": enable_musicbrainz,
        "qq": enable_qq,
        "discogs": enable_discogs,
        "lastfm": enable_lastfm,
    }


def _artist_title_query(title: str, artists: str) -> str:
    return " ".join(part for part in (artists.strip(), title.strip()) if part).strip()


def _candidate_queries(title: str, artists: str) -> list[str]:
    """Clean recall queries; avoid sending long collaborative credits verbatim."""
    primary = next(
        (
            part.strip()
            for part in _ARTIST_SPLIT_RE.split(artists or "")
            if part.strip()
        ),
        "",
    )
    queries = [_artist_title_query(title, primary), title.strip()]
    seen: set[str] = set()
    return [q for q in queries if q and not (q in seen or seen.add(q))]


def list_meta_candidates(
    *,
    title: str,
    artists: str,
    album: str,
    duration_ms: int | None = None,
    enable_musicbrainz: bool,
    enable_qq: bool,
    enable_discogs: bool,
    enable_lastfm: bool,
    lastfm_api_key: str = "",
    per_source_limit: int = 5,
    limit: int = 5,
    require_soft_match: bool = True,
) -> list[MetaHit]:
    """Return up to ``limit`` soft-matching hits across Wanted sources.

    Each enabled source is queried independently (no merge truncation that
    lets MusicBrainz crowd out QQ). Dedupes by source+source_id.
    """
    enabled = _enabled_sources(
        enable_musicbrainz=enable_musicbrainz,
        enable_qq=enable_qq,
        enable_discogs=enable_discogs,
        enable_lastfm=enable_lastfm,
    )
    queries = _candidate_queries(title, artists)
    if not queries or not any(enabled.values()):
        return []

    by_source: dict[str, list[MetaHit]] = {}
    for source in _VERIFY_SOURCE_ORDER:
        if not enabled.get(source):
            continue
        source_hits: list[MetaHit] = []
        source_seen: set[str] = set()
        for query in queries:
            hits, errored = _search_one_source(
                source,
                query,
                limit=per_source_limit,
                lastfm_api_key=lastfm_api_key,
            )
            if errored:
                # A source transport error cannot be improved by immediately
                # repeating the fallback query; it may also have opened a
                # provider cooldown.
                break
            for hit in hits:
                key = f"{hit.source}:{hit.source_id}"
                if key in source_seen:
                    continue
                if require_soft_match and not tags_soft_match(
                    title=title,
                    artists=artists,
                    album=album,
                    hit=hit,
                    duration_ms=duration_ms,
                ):
                    continue
                source_seen.add(key)
                source_hits.append(
                    replace(
                        hit,
                        score=meta_candidate_score(
                            title=title,
                            artists=artists,
                            album=album,
                            hit=hit,
                            duration_ms=duration_ms,
                        ),
                    )
                )
            # The second, title-only query is a recall fallback. Avoid another
            # provider request once the cleaner primary-artist query succeeded.
            if source_hits:
                break
        source_hits.sort(key=lambda hit: float(hit.score or 0), reverse=True)
        if source_hits:
            by_source[source] = source_hits[:per_source_limit]

    # Keep provider diversity, then fill remaining slots by confidence.
    selected: list[MetaHit] = []
    selected_keys: set[str] = set()
    for source in _VERIFY_SOURCE_ORDER:
        hits = by_source.get(source) or []
        if not hits or len(selected) >= limit:
            continue
        hit = hits[0]
        selected.append(hit)
        selected_keys.add(f"{hit.source}:{hit.source_id}")

    ranked = sorted(
        (hit for hits in by_source.values() for hit in hits),
        key=lambda hit: (
            -float(hit.score or 0),
            _VERIFY_SOURCE_ORDER.index(hit.source)
            if hit.source in _VERIFY_SOURCE_ORDER
            else len(_VERIFY_SOURCE_ORDER),
        ),
    )
    for hit in ranked:
        if len(selected) >= limit:
            break
        key = f"{hit.source}:{hit.source_id}"
        if key in selected_keys:
            continue
        selected.append(hit)
        selected_keys.add(key)

    return sorted(selected, key=lambda hit: float(hit.score or 0), reverse=True)


def pick_fill_hit_for_empty_fields(
    *,
    title: str,
    artists: str,
    album: str,
    duration_ms: int | None = None,
    enable_musicbrainz: bool,
    enable_qq: bool,
    enable_discogs: bool,
    enable_lastfm: bool,
    lastfm_api_key: str = "",
) -> MetaHit | None:
    """Best hit for filling *empty* local fields only (pre-YTM pass).

    Requires at least one non-empty local field to query; prefers hits that
    soft-match whatever local fields exist. Used before YTM match so incomplete
    rows get QQ/MB titles without YTM romanization.
    """
    if not (title.strip() or artists.strip() or album.strip()):
        return None
    # Soft-match is too strict when title/artist empty; loosen per-field.
    candidates = list_meta_candidates(
        title=title or artists or album,
        artists=artists or title,
        album=album,
        duration_ms=duration_ms,
        enable_musicbrainz=enable_musicbrainz,
        enable_qq=enable_qq,
        enable_discogs=enable_discogs,
        enable_lastfm=enable_lastfm,
        lastfm_api_key=lastfm_api_key,
        per_source_limit=5,
        limit=8,
        require_soft_match=False,
    )
    local_t = title.strip()
    local_a = artists.strip()
    local_al = album.strip()
    best: MetaHit | None = None
    best_score = -1
    for hit in candidates:
        score = 0
        if local_t:
            if not _soft_text_equal(local_t, hit.title):
                continue
            score += 3
        if local_a:
            if not _soft_artist_equal(local_a, hit.artist):
                continue
            score += 3
        if local_al and hit.album:
            if _soft_album_equal(local_al, hit.album, allow_empty=False):
                score += 1
        measured = _duration_delta(duration_ms, hit.duration_seconds)
        if measured is not None and measured[0] > measured[2]:
            continue
        if measured is not None and measured[0] <= measured[1]:
            score += 2
        # Prefer hits that actually supply missing fields.
        if not local_t and hit.title:
            score += 1
        if not local_a and hit.artist:
            score += 1
        if not local_al and hit.album:
            score += 1
        if score > best_score:
            best_score = score
            best = hit
    return best


def verify_tags_against_wanted_sources(
    *,
    title: str,
    artists: str,
    album: str,
    duration_ms: int | None = None,
    enable_musicbrainz: bool,
    enable_qq: bool,
    enable_discogs: bool,
    enable_lastfm: bool,
    lastfm_api_key: str = "",
    limit: int = 8,
) -> MetaVerifyResult:
    """Search each enabled source with clean primary-artist/title fallbacks."""
    enabled = _enabled_sources(
        enable_musicbrainz=enable_musicbrainz,
        enable_qq=enable_qq,
        enable_discogs=enable_discogs,
        enable_lastfm=enable_lastfm,
    )
    if not any(enabled.values()):
        return MetaVerifyResult(hit=None, rejected=True, errored=False)

    queries = _candidate_queries(title, artists)
    if not queries:
        return MetaVerifyResult(hit=None, rejected=True, errored=False)

    any_error = False
    for source in _VERIFY_SOURCE_ORDER:
        if not enabled.get(source):
            continue
        for query in queries:
            hits, errored = _search_one_source(
                source,
                query,
                limit=limit,
                lastfm_api_key=lastfm_api_key,
            )
            if errored:
                any_error = True
                break
            matches = [
                hit
                for hit in hits
                if tags_soft_match(
                    title=title,
                    artists=artists,
                    album=album,
                    hit=hit,
                    duration_ms=duration_ms,
                )
            ]
            if matches:
                best = max(
                    matches,
                    key=lambda hit: meta_candidate_score(
                        title=title,
                        artists=artists,
                        album=album,
                        hit=hit,
                        duration_ms=duration_ms,
                    ),
                )
                return MetaVerifyResult(hit=best, rejected=False, errored=False)

    if any_error:
        # An enabled source was unavailable.  Preserve the row as pending
        # rather than recording a negative verification from incomplete data.
        return MetaVerifyResult(hit=None, rejected=False, errored=True)
    return MetaVerifyResult(hit=None, rejected=True, errored=any_error)
