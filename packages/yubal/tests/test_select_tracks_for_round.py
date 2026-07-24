"""Tests for round track selection under max_items budget."""

from yubal.models.ytmusic import Artist, PlaylistTrack
from yubal.services.extractor import select_tracks_for_round


def _track(video_id: str, title: str | None = None) -> PlaylistTrack:
    return PlaylistTrack.model_validate(
        {
            "videoId": video_id,
            "title": title or video_id,
            "artists": [{"name": "A"}],
            "duration_seconds": 180,
        }
    )


class TestSelectTracksForRound:
    def test_no_limit_returns_all(self) -> None:
        tracks = [_track("a"), _track("b"), _track("c")]
        selected, limited = select_tracks_for_round(tracks, None)
        assert selected == tracks
        assert limited is False

    def test_prefix_without_local_check(self) -> None:
        tracks = [_track("a"), _track("b"), _track("c")]
        selected, limited = select_tracks_for_round(tracks, 2)
        assert [t.video_id for t in selected] == ["a", "b"]
        assert limited is True

    def test_local_aware_keeps_known_and_caps_new(self) -> None:
        tracks = [_track("known1"), _track("new1"), _track("known2"), _track("new2"), _track("new3")]
        local = {"known1", "known2"}
        selected, limited = select_tracks_for_round(
            tracks,
            2,
            is_already_local=lambda vid: vid in local,
        )
        assert [t.video_id for t in selected] == [
            "known1",
            "new1",
            "known2",
            "new2",
        ]
        assert limited is True

    def test_local_aware_all_known_not_limited_by_budget(self) -> None:
        tracks = [_track("a"), _track("b"), _track("c")]
        selected, limited = select_tracks_for_round(
            tracks,
            1,
            is_already_local=lambda _vid: True,
        )
        assert len(selected) == 3
        assert limited is False
