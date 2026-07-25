from __future__ import annotations

from typing import Any

from yubal_api.services import meta_search, meta_verify
from yubal_api.services.meta_search import MetaHit


def _hit(source: str, source_id: str, *, duration: int = 210) -> MetaHit:
    return MetaHit(
        source=source,
        source_id=source_id,
        title="好运来",
        artist="常思思",
        album="好运来",
        duration_seconds=duration,
    )


def test_small_duration_difference_is_scored_not_rejected() -> None:
    hit = MetaHit(
        source="musicbrainz",
        source_id="mb-1",
        title="我的楼兰",
        artist="云朵",
        album="倔强",
        duration_seconds=325,
    )

    assert meta_verify.tags_soft_match(
        title="我的楼兰",
        artists="云朵",
        album="倔强",
        hit=hit,
        duration_ms=329_586,
    )
    assert (
        meta_verify.meta_candidate_score(
            title="我的楼兰",
            artists="云朵",
            album="倔强",
            hit=hit,
            duration_ms=329_586,
        )
        == 100
    )


def test_candidate_limit_keeps_provider_diversity(
    monkeypatch: Any,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_search(
        source: str,
        query: str,
        *,
        limit: int,
        lastfm_api_key: str = "",
    ) -> tuple[list[MetaHit], bool]:
        del limit, lastfm_api_key
        calls.append((source, query))
        if source == "qq":
            return [_hit("qq", f"qq-{i}") for i in range(5)], False
        if source == "musicbrainz":
            return [_hit("musicbrainz", "mb-1")], False
        return [], False

    monkeypatch.setattr(meta_verify, "_search_one_source", fake_search)

    hits = meta_verify.list_meta_candidates(
        title="好运来",
        artists="常思思 & 逆水寒 & 雷火音频",
        album="好运来",
        duration_ms=210_600,
        enable_musicbrainz=True,
        enable_qq=True,
        enable_discogs=False,
        enable_lastfm=False,
        per_source_limit=5,
        limit=5,
    )

    assert len(hits) == 5
    assert {hit.source for hit in hits} == {"qq", "musicbrainz"}
    assert ("qq", "常思思 好运来") in calls
    assert ("musicbrainz", "常思思 好运来") in calls
    assert all(hit.score is not None for hit in hits)


def test_qq_and_musicbrainz_expose_cover_urls(monkeypatch: Any) -> None:
    def fake_get(url: str, **_kwargs: Any) -> dict[str, Any]:
        if "musicbrainz" in url:
            return {
                "recordings": [
                    {
                        "id": "recording-1",
                        "title": "Song",
                        "artist-credit": [{"name": "Artist"}],
                        "releases": [{"id": "release-1", "title": "Album"}],
                    }
                ]
            }
        return {
            "data": {
                "song": {
                    "list": [
                        {
                            "songmid": "song-1",
                            "songname": "Song",
                            "singer": [{"name": "Artist"}],
                            "albumname": "Album",
                            "albummid": "album-1",
                        }
                    ]
                }
            }
        }

    monkeypatch.setattr(meta_search, "_get_json", fake_get)

    mb = meta_search.search_musicbrainz("Song", raise_on_error=True)[0]
    qq = meta_search.search_qq("Song", raise_on_error=True)[0]

    assert mb.thumbnail_url == (
        "https://coverartarchive.org/release/release-1/front-250"
    )
    assert qq.thumbnail_url == (
        "https://y.qq.com/music/photo_new/T002R300x300M000album-1.jpg"
    )


def test_meta_search_merges_same_recording_across_sources() -> None:
    mb = MetaHit(
        source="musicbrainz",
        source_id="mb-1",
        title="如愿",
        artist="王菲",
        album="如愿",
        duration_seconds=265,
    )
    qq = MetaHit(
        source="qq",
        source_id="qq-1",
        title="如愿",
        artist="王菲",
        album="不同发行",
        duration_seconds=266,
    )

    merged = meta_search.merge_meta_hits([[mb], [qq]], limit=5)

    assert [hit.source_id for hit in merged] == ["mb-1"]


def test_meta_search_keeps_distinct_versions() -> None:
    studio = MetaHit(
        source="musicbrainz",
        source_id="studio",
        title="如愿",
        artist="王菲",
        album="如愿",
        duration_seconds=265,
    )
    live = MetaHit(
        source="qq",
        source_id="live",
        title="如愿 (Live)",
        artist="王菲",
        album="现场",
        duration_seconds=280,
    )

    merged = meta_search.merge_meta_hits([[studio], [live]], limit=5)

    assert {hit.source_id for hit in merged} == {"studio", "live"}
