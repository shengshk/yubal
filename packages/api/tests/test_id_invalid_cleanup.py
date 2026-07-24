"""ID-invalid auto-cleanup for Direct (aligned with subscription offline cleanup)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from yubal_api.db.track_catalog import LocationMembershipStatus
from yubal_api.services.sync_ledger_service import SyncLedgerService


def test_direct_id_invalid_cleanup_respects_delay(tmp_path: Path) -> None:
    data = tmp_path / "Download"
    data.mkdir()
    folder = "Direct"
    album = data / folder / "Artist" / "Album"
    album.mkdir(parents=True)
    track = album / "01 - Song.flac"
    track.write_bytes(b"audio")

    loc = SimpleNamespace(
        video_id="vid1",
        save_folder=folder,
        relative_path="Artist/Album/01 - Song.flac",
        membership_status=LocationMembershipStatus.OFFLINE,
        missing_since=datetime.now(UTC) - timedelta(hours=2),
    )
    rec = SimpleNamespace(
        video_id="vid1",
        title="Song",
        artist="Artist",
        album="Album",
        album_artist="Artist",
        year=None,
        track_number=None,
    )
    catalog = MagicMock()
    catalog.list_for_save_folder.return_value = [(loc, rec)]

    prefs_obj = SimpleNamespace(
        direct_folder=folder,
        direct_offline_cleanup_enabled=True,
        direct_offline_cleanup_action="delete",
        direct_offline_cleanup_delay_hours=72,
    )
    prefs = MagicMock()
    prefs.effective.return_value = prefs_obj

    service = SyncLedgerService(
        repository=MagicMock(),
        data_path=data,
        preferences_store=prefs,
        track_catalog=catalog,
    )
    service.reconcile_direct = MagicMock()  # type: ignore[method-assign]

    assert service.run_id_invalid_cleanup(now=datetime.now(UTC)) == 0
    assert track.is_file()

    prefs_obj.direct_offline_cleanup_delay_hours = 1
    cleared = service.run_id_invalid_cleanup(now=datetime.now(UTC))
    assert cleared == 1
    assert not track.is_file()
    catalog.delete_location.assert_called_once_with(
        folder, "Artist/Album/01 - Song.flac"
    )
