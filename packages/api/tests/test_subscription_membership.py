"""Tests for subscription membership reconciliation."""

from pathlib import Path
from uuid import uuid4

from sqlmodel import SQLModel, create_engine

from yubal_api.db.subscription import (
    OfflineCleanupAction,
    Subscription,
    SubscriptionSyncMode,
    SubscriptionType,
)
from yubal_api.db.subscription_membership import MembershipStatus
from yubal_api.db.subscription_membership_repository import (
    RemoteMembership,
    SubscriptionMembershipRepository,
    SubscriptionSnapshotRepository,
)
from yubal_api.db.subscription_repository import SubscriptionRepository
from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.services.subscription_membership_service import (
    SubscriptionMembershipService,
)


def _engine(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _sub(engine, *, sync_mode=SubscriptionSyncMode.INCREMENTAL, folder="X"):
    repo = SubscriptionRepository(engine)
    return repo.create(
        Subscription(
            type=SubscriptionType.PLAYLIST,
            url=f"https://music.youtube.com/playlist?list={uuid4().hex}",
            name=folder,
            save_folder=folder,
            sync_mode=sync_mode,
            offline_marking_enabled=True,
        )
    )


def test_incremental_marks_offline_without_deleting_file(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    data = tmp_path / "data"
    folder = data / "X"
    folder.mkdir(parents=True)
    audio = folder / "song.opus"
    audio.write_bytes(b"audio")

    sub = _sub(engine)
    catalog = TrackCatalogRepository(engine)
    catalog.upsert_track(
        video_id="vid1",
        title="Song",
        artist="Artist",
        album_artist="Artist",
    )
    catalog.upsert_location(
        video_id="vid1",
        save_folder="X",
        relative_path="song.opus",
    )

    membership = SubscriptionMembershipRepository(engine)
    membership.reconcile(
        sub,
        [
            RemoteMembership(
                video_id="vid1",
                catalog_video_id="vid1",
                title="Song",
                artist="Artist",
                album_artist="Artist",
                position=0,
            )
        ],
    )
    delta = membership.reconcile(sub, [])
    assert len(delta.offline) == 1
    assert audio.is_file()

    rows = membership.list_for_subscription(sub.id)
    assert len(rows) == 1
    assert rows[0].membership_status == MembershipStatus.OFFLINE


def test_mirror_deletes_unreferenced_file(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    data = tmp_path / "data"
    folder = data / "X"
    folder.mkdir(parents=True)
    audio = folder / "song.opus"
    audio.write_bytes(b"audio")

    sub = _sub(engine, sync_mode=SubscriptionSyncMode.MIRROR)
    catalog = TrackCatalogRepository(engine)
    catalog.upsert_track(
        video_id="vid1",
        title="Song",
        artist="Artist",
        album_artist="Artist",
    )
    catalog.upsert_location(
        video_id="vid1",
        save_folder="X",
        relative_path="song.opus",
    )

    service = SubscriptionMembershipService(
        membership_repo=SubscriptionMembershipRepository(engine),
        snapshot_repo=SubscriptionSnapshotRepository(engine),
        subscription_repo=SubscriptionRepository(engine),
        track_catalog=catalog,
        data_path=data,
    )
    snap = service.begin_snapshot(sub.id, "job-1")
    from yubal.models.track import TrackMetadata

    service.apply_trusted_sync(
        sub,
        snapshot_id=snap.id,
        remote_tracks=[
            TrackMetadata(
                source_video_id="vid1",
                title="Song",
                artists=["Artist"],
                album="Album",
                album_artists=["Artist"],
            )
        ],
    )
    assert audio.is_file()

    snap2 = service.begin_snapshot(sub.id, "job-2")
    service.apply_trusted_sync(
        sub,
        snapshot_id=snap2.id,
        remote_tracks=[],
    )
    assert not audio.exists()
    assert catalog.get_location("vid1", "X") is None


def test_shared_folder_keeps_file_when_other_subscription_refs(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    data = tmp_path / "data"
    folder = data / "Shared"
    folder.mkdir(parents=True)
    audio = folder / "song.opus"
    audio.write_bytes(b"audio")

    a = _sub(engine, sync_mode=SubscriptionSyncMode.MIRROR, folder="Shared")
    b = _sub(engine, sync_mode=SubscriptionSyncMode.INCREMENTAL, folder="Shared")
    catalog = TrackCatalogRepository(engine)
    catalog.upsert_track(
        video_id="vid1",
        title="Song",
        artist="Artist",
        album_artist="Artist",
    )
    catalog.upsert_location(
        video_id="vid1",
        save_folder="Shared",
        relative_path="song.opus",
    )

    membership = SubscriptionMembershipRepository(engine)
    remote = [
        RemoteMembership(
            video_id="vid1",
            catalog_video_id="vid1",
            title="Song",
            artist="Artist",
            album_artist="Artist",
            position=0,
        )
    ]
    membership.reconcile(a, remote)
    membership.reconcile(b, remote)

    service = SubscriptionMembershipService(
        membership_repo=membership,
        snapshot_repo=SubscriptionSnapshotRepository(engine),
        subscription_repo=SubscriptionRepository(engine),
        track_catalog=catalog,
        data_path=data,
    )
    service.dispose_membership(a, "vid1", action=OfflineCleanupAction.DELETE)
    assert audio.is_file()
    assert catalog.get_location("vid1", "Shared") is not None


def test_remove_from_list_deletes_blocked_membership_and_file(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    data = tmp_path / "data"
    folder = data / "X"
    folder.mkdir(parents=True)
    audio = folder / "song.opus"
    audio.write_bytes(b"audio")

    sub = _sub(engine)
    catalog = TrackCatalogRepository(engine)
    catalog.upsert_track(
        video_id="vid1",
        title="Song",
        artist="Artist",
        album_artist="Artist",
    )
    catalog.upsert_location(
        video_id="vid1",
        save_folder="X",
        relative_path="song.opus",
    )

    membership = SubscriptionMembershipRepository(engine)
    membership.upsert_membership(
        sub.id,
        video_id="vid1",
        catalog_video_id="vid1",
        title="Song",
        artist="Artist",
        album_artist="Artist",
        status=MembershipStatus.BLOCKED,
    )

    service = SubscriptionMembershipService(
        membership_repo=membership,
        snapshot_repo=SubscriptionSnapshotRepository(engine),
        subscription_repo=SubscriptionRepository(engine),
        track_catalog=catalog,
        data_path=data,
    )
    result = service.remove_from_list(sub, "vid1")
    assert result.action == "deleted"
    assert not audio.exists()
    assert catalog.get_location("vid1", "X") is None
    assert membership.get(sub.id, "vid1") is None
