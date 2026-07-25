"""Repositories for subscription membership and trusted sync snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Engine
from sqlmodel import Session, col, select

from yubal_api.db.subscription import Subscription, SubscriptionSyncMode
from yubal_api.db.subscription_membership import (
    MembershipStatus,
    SnapshotStatus,
    SubscriptionSyncSnapshot,
    SubscriptionTrack,
)


@dataclass(frozen=True, slots=True)
class RemoteMembership:
    video_id: str
    catalog_video_id: str
    title: str
    artist: str
    album_artist: str
    position: int


@dataclass(frozen=True, slots=True)
class MembershipDelta:
    added: tuple[SubscriptionTrack, ...] = ()
    restored: tuple[SubscriptionTrack, ...] = ()
    offline: tuple[SubscriptionTrack, ...] = ()
    id_invalid: tuple[SubscriptionTrack, ...] = ()
    removed: tuple[SubscriptionTrack, ...] = ()


class SubscriptionMembershipRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @property
    def engine(self) -> Engine:
        return self._engine

    def list_for_subscription(
        self,
        subscription_id: UUID,
        *,
        status: MembershipStatus | None = None,
    ) -> list[SubscriptionTrack]:
        with Session(self._engine) as session:
            stmt = (
                select(SubscriptionTrack)
                .where(SubscriptionTrack.subscription_id == subscription_id)
                .order_by(col(SubscriptionTrack.position), col(SubscriptionTrack.title))
            )
            if status is not None:
                stmt = stmt.where(SubscriptionTrack.membership_status == status)
            return list(session.exec(stmt).all())

    def get(
        self,
        subscription_id: UUID,
        video_id: str,
    ) -> SubscriptionTrack | None:
        with Session(self._engine) as session:
            stmt = select(SubscriptionTrack).where(
                SubscriptionTrack.subscription_id == subscription_id,
                SubscriptionTrack.video_id == video_id,
            )
            return session.exec(stmt).first()

    def get_by_catalog(
        self,
        subscription_id: UUID,
        catalog_video_id: str,
    ) -> SubscriptionTrack | None:
        with Session(self._engine) as session:
            stmt = select(SubscriptionTrack).where(
                SubscriptionTrack.subscription_id == subscription_id,
                SubscriptionTrack.catalog_video_id == catalog_video_id,
            )
            return session.exec(stmt).first()

    def set_membership_status(
        self,
        subscription_id: UUID,
        video_id: str,
        status: MembershipStatus,
    ) -> SubscriptionTrack | None:
        with Session(self._engine) as session:
            stmt = select(SubscriptionTrack).where(
                SubscriptionTrack.subscription_id == subscription_id,
                SubscriptionTrack.video_id == video_id,
            )
            row = session.exec(stmt).first()
            if row is None:
                return None
            row.membership_status = status
            if status == MembershipStatus.ACTIVE:
                row.missing_since = None
            elif status == MembershipStatus.BLOCKED:
                row.missing_since = None
            row.updated_at = datetime.now(UTC)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def upsert_membership(
        self,
        subscription_id: UUID,
        *,
        video_id: str,
        catalog_video_id: str,
        title: str = "",
        artist: str = "",
        album_artist: str = "",
        status: MembershipStatus = MembershipStatus.ACTIVE,
        position: int | None = None,
    ) -> SubscriptionTrack:
        """Create or update a membership row (used when blocking without prior sync)."""
        now = datetime.now(UTC)
        with Session(self._engine) as session:
            stmt = select(SubscriptionTrack).where(
                SubscriptionTrack.subscription_id == subscription_id,
                SubscriptionTrack.video_id == video_id,
            )
            row = session.exec(stmt).first()
            if row is None:
                row = SubscriptionTrack(
                    subscription_id=subscription_id,
                    video_id=video_id,
                    catalog_video_id=catalog_video_id,
                    title=title,
                    artist=artist,
                    album_artist=album_artist,
                    position=position,
                    membership_status=status,
                    first_seen_at=now,
                    last_seen_at=now,
                    missing_since=None,
                    updated_at=now,
                )
            else:
                row.catalog_video_id = catalog_video_id
                if title:
                    row.title = title
                if artist:
                    row.artist = artist
                if album_artist:
                    row.album_artist = album_artist
                if position is not None:
                    row.position = position
                row.membership_status = status
                row.missing_since = None
                row.updated_at = now
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def adopt_local_members(
        self,
        subscription_id: UUID,
        entries: list[RemoteMembership],
        *,
        seen_at: datetime | None = None,
    ) -> int:
        """Seed ACTIVE membership rows for pre-existing local tracks.

        Used once on the first authoritative sync so that files downloaded
        before membership tracking existed become known members. Rows already
        present (by video_id) are left untouched. Reconcile against the current
        remote afterwards flips the ones no longer upstream to offline/removed.
        """
        if not entries:
            return 0
        now = seen_at or datetime.now(UTC)
        with Session(self._engine) as session:
            stmt = select(SubscriptionTrack.video_id).where(
                SubscriptionTrack.subscription_id == subscription_id
            )
            existing = set(session.exec(stmt).all())
            added = 0
            for item in entries:
                if not item.video_id or item.video_id in existing:
                    continue
                session.add(
                    SubscriptionTrack(
                        subscription_id=subscription_id,
                        video_id=item.video_id,
                        catalog_video_id=item.catalog_video_id,
                        title=item.title,
                        artist=item.artist,
                        album_artist=item.album_artist,
                        position=item.position,
                        membership_status=MembershipStatus.ACTIVE,
                        first_seen_at=now,
                        last_seen_at=now,
                        updated_at=now,
                    )
                )
                existing.add(item.video_id)
                added += 1
            session.commit()
            return added

    def reconcile(
        self,
        subscription: Subscription,
        remote: list[RemoteMembership],
        *,
        unavailable_video_ids: set[str] | None = None,
        seen_at: datetime | None = None,
    ) -> MembershipDelta:
        """Atomically reconcile one authoritative remote membership snapshot."""
        now = seen_at or datetime.now(UTC)
        remote_by_id = {item.video_id: item for item in remote if item.video_id}
        unavailable = unavailable_video_ids or set()
        added: list[SubscriptionTrack] = []
        restored: list[SubscriptionTrack] = []
        offline: list[SubscriptionTrack] = []
        id_invalid: list[SubscriptionTrack] = []
        removed: list[SubscriptionTrack] = []

        with Session(self._engine) as session:
            stmt = select(SubscriptionTrack).where(
                SubscriptionTrack.subscription_id == subscription.id
            )
            current = {
                row.video_id: row for row in session.exec(stmt).all()
            }

            for video_id, item in remote_by_id.items():
                row = current.pop(video_id, None)
                if row is None:
                    row = SubscriptionTrack(
                        subscription_id=subscription.id,
                        video_id=video_id,
                        catalog_video_id=item.catalog_video_id,
                        title=item.title,
                        artist=item.artist,
                        album_artist=item.album_artist,
                        position=item.position,
                        membership_status=MembershipStatus.ACTIVE,
                        first_seen_at=now,
                        last_seen_at=now,
                        updated_at=now,
                    )
                    session.add(row)
                    added.append(row)
                    continue
                was_offline = row.membership_status in (
                    MembershipStatus.OFFLINE,
                    MembershipStatus.ID_INVALID,
                )
                was_blocked = row.membership_status == MembershipStatus.BLOCKED
                row.title = item.title
                row.catalog_video_id = item.catalog_video_id
                row.artist = item.artist
                row.album_artist = item.album_artist
                row.position = item.position
                row.last_seen_at = now
                row.updated_at = now
                if was_blocked:
                    # Keep user blacklist; do not revive for download.
                    session.add(row)
                    continue
                row.membership_status = MembershipStatus.ACTIVE
                row.missing_since = None
                session.add(row)
                if was_offline:
                    restored.append(row)

            for row in current.values():
                if row.membership_status == MembershipStatus.BLOCKED:
                    # Stay blocked even if absent upstream.
                    row.updated_at = now
                    session.add(row)
                    continue
                if subscription.sync_mode == SubscriptionSyncMode.MIRROR:
                    removed.append(row)
                    session.delete(row)
                    continue

                # Mutually exclusive: dead ID vs removed-from-playlist.
                in_unavailable = row.video_id in unavailable
                target: MembershipStatus | None = None
                if in_unavailable and getattr(
                    subscription, "id_invalid_marking_enabled", True
                ):
                    target = MembershipStatus.ID_INVALID
                elif (
                    not in_unavailable
                    and subscription.offline_marking_enabled
                ):
                    target = MembershipStatus.OFFLINE
                elif (
                    in_unavailable
                    and not getattr(
                        subscription, "id_invalid_marking_enabled", True
                    )
                    and subscription.offline_marking_enabled
                ):
                    # ID-invalid marking off: fall back to not-in-playlist mark.
                    target = MembershipStatus.OFFLINE

                if target is None:
                    row.updated_at = now
                    session.add(row)
                    continue

                if row.membership_status != target:
                    row.membership_status = target
                    row.missing_since = now
                    if target == MembershipStatus.ID_INVALID:
                        id_invalid.append(row)
                    else:
                        offline.append(row)
                row.updated_at = now
                session.add(row)

            session.commit()

        return MembershipDelta(
            added=tuple(added),
            restored=tuple(restored),
            offline=tuple(offline),
            id_invalid=tuple(id_invalid),
            removed=tuple(removed),
        )

    def delete_membership(
        self,
        subscription_id: UUID,
        video_id: str,
    ) -> SubscriptionTrack | None:
        with Session(self._engine) as session:
            stmt = select(SubscriptionTrack).where(
                SubscriptionTrack.subscription_id == subscription_id,
                SubscriptionTrack.video_id == video_id,
            )
            row = session.exec(stmt).first()
            if row is None:
                return None
            session.delete(row)
            session.commit()
            return row

    def delete_for_subscription(self, subscription_id: UUID) -> list[SubscriptionTrack]:
        with Session(self._engine) as session:
            stmt = select(SubscriptionTrack).where(
                SubscriptionTrack.subscription_id == subscription_id
            )
            rows = list(session.exec(stmt).all())
            for row in rows:
                session.delete(row)
            session.commit()
            return rows

    def count_refs_in_folder(
        self,
        catalog_video_id: str,
        save_folder: str,
        *,
        exclude_subscription_id: UUID | None = None,
    ) -> int:
        folder = save_folder.strip().replace("\\", "/").rstrip("/")
        with Session(self._engine) as session:
            stmt = (
                select(SubscriptionTrack, Subscription)
                .join(
                    Subscription,
                    col(SubscriptionTrack.subscription_id) == col(Subscription.id),
                )
                .where(SubscriptionTrack.catalog_video_id == catalog_video_id)
            )
            if exclude_subscription_id is not None:
                stmt = stmt.where(
                    SubscriptionTrack.subscription_id != exclude_subscription_id
                )
            count = 0
            for _row, sub in session.exec(stmt).all():
                effective = (sub.save_folder or sub.name or "").strip().replace(
                    "\\", "/"
                ).rstrip("/")
                if effective == folder:
                    count += 1
            return count

    def list_due_offline(self, before: datetime) -> list[SubscriptionTrack]:
        with Session(self._engine) as session:
            stmt = (
                select(SubscriptionTrack)
                .join(
                    Subscription,
                    col(SubscriptionTrack.subscription_id) == col(Subscription.id),
                )
                .where(
                    SubscriptionTrack.membership_status == MembershipStatus.OFFLINE,
                    SubscriptionTrack.missing_since.is_not(None),
                    SubscriptionTrack.missing_since <= before,
                    Subscription.offline_cleanup_enabled == True,  # noqa: E712
                    Subscription.sync_mode == SubscriptionSyncMode.INCREMENTAL,
                )
            )
            return list(session.exec(stmt).all())


class SubscriptionSnapshotRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def start(self, subscription_id: UUID, job_id: str) -> SubscriptionSyncSnapshot:
        with Session(self._engine) as session:
            row = SubscriptionSyncSnapshot(
                subscription_id=subscription_id,
                job_id=job_id,
                status=SnapshotStatus.RUNNING,
                authoritative=False,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def finish(
        self,
        snapshot_id: UUID,
        *,
        status: SnapshotStatus,
        authoritative: bool,
        source_track_count: int = 0,
        unavailable_count: int = 0,
        limited_by_max_items: bool = False,
        error_message: str | None = None,
        finished_at: datetime | None = None,
    ) -> SubscriptionSyncSnapshot | None:
        with Session(self._engine) as session:
            row = session.get(SubscriptionSyncSnapshot, snapshot_id)
            if row is None:
                return None
            row.status = status
            row.authoritative = authoritative
            row.source_track_count = source_track_count
            row.unavailable_count = unavailable_count
            row.limited_by_max_items = limited_by_max_items
            row.error_message = error_message
            row.finished_at = finished_at or datetime.now(UTC)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row

    def latest_trusted(
        self,
        subscription_id: UUID,
    ) -> SubscriptionSyncSnapshot | None:
        with Session(self._engine) as session:
            stmt = (
                select(SubscriptionSyncSnapshot)
                .where(
                    SubscriptionSyncSnapshot.subscription_id == subscription_id,
                    SubscriptionSyncSnapshot.authoritative == True,  # noqa: E712
                    SubscriptionSyncSnapshot.status
                    == SnapshotStatus.TRUSTED_COMPLETE,
                )
                .order_by(col(SubscriptionSyncSnapshot.finished_at).desc())
            )
            return session.exec(stmt).first()
