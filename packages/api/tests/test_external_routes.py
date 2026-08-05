"""External-library route behavior."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from yubal_api.api.routes.external import list_playlist_tracks_page, list_playlists


def test_list_playlists_reconciles_top_level_directories() -> None:
    service = Mock()
    service.list_playlists.return_value = []
    preferences = Mock()
    preferences.effective.return_value = SimpleNamespace(external_library_enabled=True)

    assert list_playlists(service, preferences) == []

    service.sync_playlists_from_disk.assert_called_once_with()
    service.list_playlists.assert_called_once_with()


def test_list_playlists_skips_disk_when_feature_is_disabled() -> None:
    service = Mock()
    preferences = Mock()
    preferences.effective.return_value = SimpleNamespace(external_library_enabled=False)

    assert list_playlists(service, preferences) == []

    service.sync_playlists_from_disk.assert_not_called()
    service.list_playlists.assert_not_called()


def test_external_track_page_is_bounded_and_reports_next_offset() -> None:
    service = Mock()
    service.list_playlist_tracks_page.return_value = (250, [])
    preferences = Mock()
    preferences.effective.return_value = SimpleNamespace(
        external_library_enabled=True,
        track_sort_key="title",
    )

    result = list_playlist_tracks_page(
        "large",
        service,
        preferences,
        offset=100,
        limit=100,
    )

    assert result.total == 250
    assert result.offset == 100
    assert result.next_offset is None
    service.list_playlist_tracks_page.assert_called_once_with(
        "large",
        offset=100,
        limit=100,
        sort_key="title",
        show_raw=None,
    )
