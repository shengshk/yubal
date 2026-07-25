from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from yubal_api.api.routes.sync_ledger import list_sync_ledger
from yubal_api.db.subscription_membership import MembershipStatus
from yubal_api.db.sync_ledger import LedgerKind, SyncLedgerEntry
from yubal_api.services.sync_ledger_service import FolderTrackSummary


def test_ledger_list_includes_compact_subscription_summary() -> None:
    subscription_id = uuid4()
    entry = SyncLedgerEntry(
        key=f"subscription:{subscription_id}",
        kind=LedgerKind.SUBSCRIPTION,
        subscription_id=subscription_id,
        save_folder="sublist/Test",
        title="Test",
        total_count=4,
        synced_count=1,
        real_download_count=1,
    )
    service = MagicMock()
    service.list.return_value = [entry]
    service.folder_track_summary.return_value = FolderTrackSummary(
        present_video_ids=frozenset({"present"}),
        missing_active_count=0,
        cover_track_path="sublist/Test/Artist/Track.mp3",
    )
    membership = MagicMock()
    membership.list_membership.return_value = [
        SimpleNamespace(
            membership_status=MembershipStatus.ACTIVE,
            catalog_video_id="present",
        ),
        SimpleNamespace(
            membership_status=MembershipStatus.ACTIVE,
            catalog_video_id="missing",
        ),
        SimpleNamespace(
            membership_status=MembershipStatus.OFFLINE,
            catalog_video_id="offline",
        ),
        SimpleNamespace(
            membership_status=MembershipStatus.ID_INVALID,
            catalog_video_id="invalid",
        ),
        SimpleNamespace(
            membership_status=MembershipStatus.BLOCKED,
            catalog_video_id="blocked",
        ),
    ]

    result = list_sync_ledger(service, membership)
    item = result.items[0]

    assert item.offline_count == 1
    assert item.id_invalid_count == 1
    assert item.blocked_count == 1
    assert item.missing_count == 1
    assert item.cover_track_path == "sublist/Test/Artist/Track.mp3"
