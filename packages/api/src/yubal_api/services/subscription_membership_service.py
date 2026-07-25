"""Trusted membership reconciliation and reference-safe file disposal."""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from yubal.models.track import TrackMetadata
from yubal.services.track_index import TrackFileIndex
from yubal.utils.library import AUDIO_SUFFIXES, resolve_under_data

from yubal_api.db.subscription import (
    OfflineCleanupAction,
    Subscription,
    SubscriptionSyncMode,
)
from yubal_api.db.subscription_membership import (
    MembershipStatus,
    SnapshotStatus,
    SubscriptionSyncSnapshot,
    SubscriptionTrack,
)
from yubal_api.db.subscription_membership_repository import (
    MembershipDelta,
    RemoteMembership,
    SubscriptionMembershipRepository,
    SubscriptionSnapshotRepository,
)
from yubal_api.db.subscription_repository import SubscriptionRepository
from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.services.library_ops import cleanup_after_audio_removed

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FileActionResult:
    video_id: str
    action: str
    path: str | None = None
    kept_reason: str | None = None


class SubscriptionMembershipService:
    """Owns subscription↔track membership and reference-counted file GC."""

    def __init__(
        self,
        *,
        membership_repo: SubscriptionMembershipRepository,
        snapshot_repo: SubscriptionSnapshotRepository,
        subscription_repo: SubscriptionRepository,
        track_catalog: TrackCatalogRepository,
        data_path: Path,
        archive_folder: str = "direct",
    ) -> None:
        self._membership = membership_repo
        self._snapshots = snapshot_repo
        self._subscriptions = subscription_repo
        self._catalog = track_catalog
        self._data_path = data_path
        # "Archive" relocates the file into the Direct library so it stays
        # visible and manageable there instead of a hidden folder.
        self._archive_folder = archive_folder.strip().replace("\\", "/").rstrip("/")
        self._index = TrackFileIndex(data_path)
        self._external = None
        self._wanted = None

    def bind_external_library(self, external: object) -> None:
        self._external = external

    def bind_wanted_service(self, wanted: object) -> None:
        self._wanted = wanted

    def rewrite_catalog_folder(self, old_folder: str, new_folder: str) -> int:
        return self._catalog.rewrite_save_folder(old_folder, new_folder)

    def begin_snapshot(
        self,
        subscription_id: UUID,
        job_id: str,
    ) -> SubscriptionSyncSnapshot:
        return self._snapshots.start(subscription_id, job_id)

    def abort_snapshot(
        self,
        snapshot_id: UUID,
        *,
        status: SnapshotStatus,
        error_message: str | None = None,
    ) -> None:
        self._snapshots.finish(
            snapshot_id,
            status=status,
            authoritative=False,
            error_message=error_message,
        )

    def apply_trusted_sync(
        self,
        subscription: Subscription,
        *,
        snapshot_id: UUID,
        remote_tracks: list[TrackMetadata],
        unavailable_count: int = 0,
        unavailable_video_ids: list[str] | None = None,
        cancelled: bool = False,
        failed: bool = False,
        error_message: str | None = None,
    ) -> MembershipDelta | None:
        """Finish the snapshot gate and reconcile membership when trusted."""
        if cancelled:
            self.abort_snapshot(
                snapshot_id,
                status=SnapshotStatus.CANCELLED,
                error_message=error_message or "cancelled",
            )
            return None
        if failed:
            self.abort_snapshot(
                snapshot_id,
                status=SnapshotStatus.FAILED,
                error_message=error_message or "sync failed",
            )
            return None

        remote = self._to_remote_membership(remote_tracks)
        self._snapshots.finish(
            snapshot_id,
            status=SnapshotStatus.TRUSTED_COMPLETE,
            authoritative=True,
            source_track_count=len(remote),
            unavailable_count=unavailable_count,
            limited_by_max_items=False,
        )
        # Adopt pre-existing local files into membership so tracks downloaded
        # before membership tracking (or otherwise never recorded) that are now
        # absent upstream still get marked offline / removed by reconcile. This
        # is idempotent: already-known tracks are skipped, and files claimed by
        # another subscription in the same folder are left alone.
        self._backfill_local_members(subscription, remote)
        delta = self._membership.reconcile(
            subscription,
            remote,
            unavailable_video_ids=set(unavailable_video_ids or []),
        )
        self._apply_file_delta(subscription, delta)
        if subscription.sync_mode == SubscriptionSyncMode.INCREMENTAL:
            if (
                subscription.offline_cleanup_enabled
                and int(subscription.offline_cleanup_delay_hours) == 0
            ):
                for row in delta.offline:
                    self._cleanup_membership_row(
                        subscription,
                        row,
                        action=self._offline_cleanup_action(subscription),
                    )
            if (
                getattr(subscription, "id_invalid_cleanup_enabled", False)
                and int(
                    getattr(subscription, "id_invalid_cleanup_delay_hours", 72) or 72
                )
                == 0
            ):
                for row in delta.id_invalid:
                    self._cleanup_membership_row(
                        subscription,
                        row,
                        action=self._id_invalid_cleanup_action(subscription),
                    )
        return delta

    def list_membership(
        self,
        subscription_id: UUID,
        *,
        status: MembershipStatus | None = None,
    ) -> list[SubscriptionTrack]:
        return self._membership.list_for_subscription(
            subscription_id,
            status=status,
        )

    def dispose_membership(
        self,
        subscription: Subscription,
        video_id: str,
        *,
        action: OfflineCleanupAction | str,
    ) -> FileActionResult:
        """Manually remove one membership row and GC/archive its file if unused."""
        row = self._membership.get(subscription.id, video_id)
        if row is None:
            return FileActionResult(
                video_id=video_id,
                action="missing",
                kept_reason="membership not found",
            )
        if str(action) == "to_wanted":
            if row.membership_status != MembershipStatus.ID_INVALID:
                raise ValueError(
                    "to_wanted is only allowed for id_invalid memberships"
                )
            return self._migrate_membership_to_wanted(subscription, row)

        self._membership.delete_membership(subscription.id, video_id)
        return self._dispose_catalog_file(
            catalog_video_id=row.catalog_video_id,
            save_folder=subscription.save_folder or subscription.name,
            exclude_subscription_id=subscription.id,
            action=OfflineCleanupAction(action),
        )

    def _migrate_membership_to_wanted(
        self,
        subscription: Subscription,
        row: SubscriptionTrack,
    ) -> FileActionResult:
        """Strip membership, hardlink file into wishlist, drop source list+file."""
        if self._wanted is None:
            raise RuntimeError("wanted service not configured")
        folder = subscription.save_folder or subscription.name
        location = self._catalog.get_location(row.catalog_video_id, folder)
        abs_path = None
        if location is not None:
            abs_path = resolve_under_data(
                self._data_path, f"{folder}/{location.relative_path}"
            )
        track = self._catalog.get_track(row.catalog_video_id)
        title = row.title or (
            location.relative_path if location is not None else row.video_id
        )
        artists = row.artist or ""
        album = (track.album if track else "") or ""
        add_from_offline = getattr(self._wanted, "add_from_offline", None)
        if not callable(add_from_offline):
            raise RuntimeError("wanted service missing add_from_offline")
        add_from_offline(
            title=title,
            artists=artists,
            album=album,
            source_path=abs_path if abs_path is not None and abs_path.is_file() else None,
            thumbnail_url=getattr(track, "thumbnail_url", None) if track else None,
        )
        self._membership.delete_membership(subscription.id, row.video_id)
        if location is not None:
            refs = self._membership.count_refs_in_folder(
                row.catalog_video_id,
                folder,
                exclude_subscription_id=subscription.id,
            )
            if refs == 0:
                if abs_path is not None and abs_path.is_file():
                    try:
                        abs_path.unlink(missing_ok=True)
                        lrc = abs_path.with_suffix(".lrc")
                        if lrc.is_file():
                            lrc.unlink(missing_ok=True)
                        stop_at = resolve_under_data(self._data_path, folder)
                        cleanup_after_audio_removed(abs_path.parent, stop_at)
                    except OSError:
                        logger.exception(
                            "Failed removing source after wanted migrate %s",
                            row.video_id,
                        )
                self._catalog.delete_location(folder, location.relative_path)
        return FileActionResult(
            video_id=row.video_id,
            action="to_wanted",
            path=str(abs_path) if abs_path else None,
        )

    def clear_offline(
        self,
        subscription: Subscription,
        *,
        to_raw_delete: bool = False,
        to_wanted: bool = False,
        status: MembershipStatus = MembershipStatus.OFFLINE,
    ) -> dict[str, int]:
        """Clear memberships in one status: hard-delete, Raw/Delete, or Wanted."""
        if to_wanted and to_raw_delete:
            raise ValueError("choose either to_wanted or to_raw_delete")
        if to_wanted and status == MembershipStatus.OFFLINE:
            raise ValueError(
                "to_wanted is only allowed for id_invalid, not not-in-playlist"
            )
        rows = [
            r
            for r in self._membership.list_for_subscription(subscription.id)
            if r.membership_status == status
        ]
        folder = subscription.save_folder or subscription.name
        cleared = moved = errors = 0
        for row in rows:
            location = self._catalog.get_location(row.catalog_video_id, folder)
            abs_path = None
            if location is not None:
                abs_path = resolve_under_data(
                    self._data_path, f"{folder}/{location.relative_path}"
                )
            if to_wanted:
                try:
                    self._migrate_membership_to_wanted(subscription, row)
                    cleared += 1
                    moved += 1
                except Exception:
                    errors += 1
                    logger.exception(
                        "Failed offline→wanted for %s", row.video_id
                    )
            elif to_raw_delete:
                if self._external is None:
                    raise RuntimeError("external library not configured")
                try:
                    title = row.title or (
                        location.relative_path if location else row.video_id
                    )
                    artist = row.artist or ""
                    track = self._catalog.get_track(row.catalog_video_id)
                    if abs_path is not None and abs_path.is_file():
                        dest = self._external.ingest_file_to_raw_delete(  # type: ignore[attr-defined]
                            abs_path,
                            title=title,
                            artists=artist,
                            album=(track.album if track else "") or "",
                            album_artist=row.album_artist or "",
                            year=track.year if track else None,
                            track_number=track.track_number if track else None,
                        )
                        if dest is not None:
                            moved += 1
                    self._membership.delete_membership(subscription.id, row.video_id)
                    if location is not None:
                        refs = self._membership.count_refs_in_folder(
                            row.catalog_video_id,
                            folder,
                            exclude_subscription_id=subscription.id,
                        )
                        if refs == 0:
                            self._catalog.delete_location(
                                folder, location.relative_path
                            )
                    cleared += 1
                except Exception:
                    errors += 1
                    logger.exception(
                        "Failed offline→Raw/Delete for %s", row.video_id
                    )
            else:
                self._membership.delete_membership(subscription.id, row.video_id)
                result = self._dispose_catalog_file(
                    catalog_video_id=row.catalog_video_id,
                    save_folder=folder,
                    exclude_subscription_id=subscription.id,
                    action=OfflineCleanupAction.DELETE,
                )
                if result.action in ("deleted", "missing", "noop"):
                    cleared += 1
                else:
                    errors += 1
        return {"cleared": cleared, "moved": moved, "errors": errors}

    def wipe_files_keep_list(
        self,
        subscription: Subscription,
    ) -> list[FileActionResult]:
        """Delete on-disk files for all members; keep subscription + membership."""
        rows = self._membership.list_for_subscription(subscription.id)
        folder = subscription.save_folder or subscription.name
        results: list[FileActionResult] = []
        for row in rows:
            if row.membership_status == MembershipStatus.BLOCKED:
                # Still wipe files for blocked rows if present.
                pass
            results.append(
                self._dispose_catalog_file(
                    catalog_video_id=row.catalog_video_id,
                    save_folder=folder,
                    exclude_subscription_id=subscription.id,
                    action=OfflineCleanupAction.DELETE,
                )
            )
            # Restore ACTIVE so missing tracks can be recovered on next sync,
            # unless the user had blocked this track.
            if row.membership_status != MembershipStatus.BLOCKED:
                self._membership.set_membership_status(
                    subscription.id,
                    row.video_id,
                    MembershipStatus.ACTIVE,
                )
        return results

    def delete_track_keep_membership(
        self,
        subscription: Subscription,
        video_id: str,
        *,
        block: bool = False,
    ) -> FileActionResult:
        """Delete the local file but keep the membership row (optionally blocked).

        Works even when the subscription has no membership row yet (e.g. never
        synced): still deletes the catalog file under this folder, and when
        ``block`` is set upserts a blocked membership so auto-sync skips it.
        """
        row = self._membership.get(subscription.id, video_id)
        if row is None:
            row = self._membership.get_by_catalog(subscription.id, video_id)

        catalog_id = row.catalog_video_id if row else video_id
        membership_video_id = row.video_id if row else video_id
        result = self._dispose_catalog_file(
            catalog_video_id=catalog_id,
            save_folder=subscription.save_folder or subscription.name,
            exclude_subscription_id=subscription.id,
            action=OfflineCleanupAction.DELETE,
        )
        status = (
            MembershipStatus.BLOCKED if block else MembershipStatus.ACTIVE
        )
        if row is not None:
            self._membership.set_membership_status(
                subscription.id,
                membership_video_id,
                status,
            )
        elif block:
            rec = self._catalog.get_track(catalog_id)
            self._membership.upsert_membership(
                subscription.id,
                video_id=membership_video_id,
                catalog_video_id=catalog_id,
                title=(rec.title if rec else "") or "",
                artist=(rec.artist if rec else "") or "",
                album_artist=(rec.album_artist if rec else "") or "",
                status=MembershipStatus.BLOCKED,
            )
        return result

    def unblock_membership(
        self,
        subscription: Subscription,
        video_id: str,
    ) -> SubscriptionTrack | None:
        """Clear sync blacklist so the track can be recovered on next sync."""
        return self._membership.set_membership_status(
            subscription.id,
            video_id,
            MembershipStatus.ACTIVE,
        )

    def remove_from_list(
        self,
        subscription: Subscription,
        video_id: str,
    ) -> FileActionResult:
        """Drop membership entirely and GC the file when unreferenced."""
        return self.dispose_membership(
            subscription,
            video_id,
            action=OfflineCleanupAction.DELETE,
        )

    def blocked_video_ids(self, subscription_id: UUID) -> set[str]:
        rows = self._membership.list_for_subscription(
            subscription_id,
            status=MembershipStatus.BLOCKED,
        )
        return {row.video_id for row in rows if row.video_id}

    def delete_subscription_membership(
        self,
        subscription: Subscription,
        *,
        file_action: str,
        direct_folder: str,
    ) -> list[FileActionResult]:
        """Remove all membership for a subscription and optionally dispose files."""
        rows = self._membership.delete_for_subscription(subscription.id)
        if file_action == "keep":
            return [
                FileActionResult(video_id=row.video_id, action="kept") for row in rows
            ]

        results: list[FileActionResult] = []
        folder = subscription.save_folder or subscription.name
        for row in rows:
            if file_action == "move_to_direct":
                results.append(
                    self._relocate_catalog_file(
                        catalog_video_id=row.catalog_video_id,
                        source_folder=folder,
                        dest_folder=direct_folder,
                        exclude_subscription_id=subscription.id,
                        origin="direct",
                    )
                )
            elif file_action == "delete":
                results.append(
                    self._dispose_catalog_file(
                        catalog_video_id=row.catalog_video_id,
                        save_folder=folder,
                        exclude_subscription_id=subscription.id,
                        action=OfflineCleanupAction.DELETE,
                    )
                )
        return results

    def migrate_save_folder(
        self,
        subscription: Subscription,
        old_folder: str,
        new_folder: str,
    ) -> int:
        """Move or hardlink this subscription's members from old_folder to new_folder."""
        old_n = old_folder.strip().replace("\\", "/").rstrip("/")
        new_n = new_folder.strip().replace("\\", "/").rstrip("/")
        if not old_n or old_n == new_n:
            return 0

        members = self._membership.list_for_subscription(subscription.id)
        moved = 0
        for row in members:
            result = self._relocate_catalog_file(
                catalog_video_id=row.catalog_video_id,
                source_folder=old_n,
                dest_folder=new_n,
                exclude_subscription_id=subscription.id,
                origin="download",
                keep_source_if_referenced=True,
            )
            if result.action in {"moved", "hardlinked", "already_present"}:
                moved += 1
        return moved

    def run_offline_cleanup(self, *, now: datetime | None = None) -> int:
        """Process due offline / ID-invalid memberships with cleanup enabled."""
        now = now or datetime.now(UTC)
        processed = 0
        for sub in self._subscriptions.list(enabled=None):
            if sub.sync_mode != SubscriptionSyncMode.INCREMENTAL:
                continue
            if sub.offline_cleanup_enabled:
                delay = max(0, int(sub.offline_cleanup_delay_hours))
                cutoff = now - timedelta(hours=delay)
                action = self._offline_cleanup_action(sub)
                due = [
                    row
                    for row in self._membership.list_for_subscription(
                        sub.id,
                        status=MembershipStatus.OFFLINE,
                    )
                    if row.missing_since is not None and row.missing_since <= cutoff
                ]
                for row in due:
                    self._cleanup_membership_row(sub, row, action=action)
                    processed += 1
            if getattr(sub, "id_invalid_cleanup_enabled", False):
                delay = max(
                    0, int(getattr(sub, "id_invalid_cleanup_delay_hours", 72) or 72)
                )
                cutoff = now - timedelta(hours=delay)
                action = self._id_invalid_cleanup_action(sub)
                due = [
                    row
                    for row in self._membership.list_for_subscription(
                        sub.id,
                        status=MembershipStatus.ID_INVALID,
                    )
                    if row.missing_since is not None and row.missing_since <= cutoff
                ]
                for row in due:
                    self._cleanup_membership_row(sub, row, action=action)
                    processed += 1
        return processed

    @staticmethod
    def _offline_cleanup_action(subscription: Subscription) -> OfflineCleanupAction:
        """Not-in-playlist cleanup never migrates to Wanted."""
        action = subscription.offline_cleanup_action
        if action == OfflineCleanupAction.TO_WANTED or str(action) == "to_wanted":
            return OfflineCleanupAction.ARCHIVE
        return OfflineCleanupAction(action)

    @staticmethod
    def _id_invalid_cleanup_action(
        subscription: Subscription,
    ) -> OfflineCleanupAction:
        action = getattr(
            subscription, "id_invalid_cleanup_action", OfflineCleanupAction.ARCHIVE
        )
        try:
            return OfflineCleanupAction(action)
        except ValueError:
            return OfflineCleanupAction.ARCHIVE

    def _cleanup_membership_row(
        self,
        subscription: Subscription,
        row: SubscriptionTrack,
        *,
        action: OfflineCleanupAction,
    ) -> FileActionResult:
        """Apply a configured cleanup action to one membership row."""
        if action == OfflineCleanupAction.TO_WANTED:
            return self._migrate_membership_to_wanted(subscription, row)
        self._membership.delete_membership(subscription.id, row.video_id)
        return self._dispose_catalog_file(
            catalog_video_id=row.catalog_video_id,
            save_folder=subscription.save_folder or subscription.name,
            exclude_subscription_id=subscription.id,
            action=action,
        )

    def _backfill_local_members(
        self,
        subscription: Subscription,
        remote: list[RemoteMembership],
    ) -> int:
        """Seed membership from local catalog files on the first sync.

        Only adopts catalog files under this subscription's folder that are not
        in the current remote snapshot and not already claimed by another
        subscription sharing the folder. These become ACTIVE members, so the
        subsequent reconcile marks them offline (incremental) or removes them
        (mirror), matching the user's expectation for tracks deleted upstream.
        """
        folder = subscription.save_folder or subscription.name
        remote_ids = {r.catalog_video_id for r in remote} | {
            r.video_id for r in remote
        }
        orphans: list[RemoteMembership] = []
        for index, (_loc, rec) in enumerate(
            self._catalog.list_for_save_folder(folder)
        ):
            if not rec.video_id or rec.video_id in remote_ids:
                continue
            refs = self._membership.count_refs_in_folder(
                rec.video_id,
                folder,
                exclude_subscription_id=subscription.id,
            )
            if refs > 0:
                continue
            orphans.append(
                RemoteMembership(
                    video_id=rec.video_id,
                    catalog_video_id=rec.video_id,
                    title=rec.title,
                    artist=rec.artist,
                    album_artist=rec.album_artist or rec.artist,
                    position=10_000 + index,
                )
            )
        if not orphans:
            return 0
        adopted = self._membership.adopt_local_members(subscription.id, orphans)
        if adopted:
            logger.info(
                "Adopted %d pre-existing local track(s) into subscription %s",
                adopted,
                subscription.id,
            )
        return adopted

    def _apply_file_delta(
        self,
        subscription: Subscription,
        delta: MembershipDelta,
    ) -> None:
        if subscription.sync_mode != SubscriptionSyncMode.MIRROR:
            return
        folder = subscription.save_folder or subscription.name
        for row in delta.removed:
            self._dispose_catalog_file(
                catalog_video_id=row.catalog_video_id,
                save_folder=folder,
                exclude_subscription_id=subscription.id,
                action=OfflineCleanupAction.DELETE,
            )

    def _dispose_catalog_file(
        self,
        *,
        catalog_video_id: str,
        save_folder: str,
        exclude_subscription_id: UUID,
        action: OfflineCleanupAction,
    ) -> FileActionResult:
        refs = self._membership.count_refs_in_folder(
            catalog_video_id,
            save_folder,
            exclude_subscription_id=exclude_subscription_id,
        )
        location = self._catalog.get_location(catalog_video_id, save_folder)
        if refs > 0:
            return FileActionResult(
                video_id=catalog_video_id,
                action="kept",
                path=location.relative_path if location else None,
                kept_reason="still referenced by another subscription in folder",
            )
        if location is None:
            return FileActionResult(
                video_id=catalog_video_id,
                action="noop",
                kept_reason="no catalog location",
            )

        abs_path = resolve_under_data(
            self._data_path,
            f"{save_folder}/{location.relative_path}",
        )
        if action == OfflineCleanupAction.ARCHIVE:
            archived = self._relocate_catalog_file(
                catalog_video_id=catalog_video_id,
                source_folder=save_folder,
                dest_folder=self._archive_folder,
                exclude_subscription_id=exclude_subscription_id,
                origin="direct",
                keep_source_if_referenced=False,
            )
            return FileActionResult(
                video_id=catalog_video_id,
                action="archived",
                path=archived.path,
            )

        deleted = False
        if abs_path.is_file():
            try:
                abs_path.unlink()
                deleted = True
            except OSError as e:
                logger.warning("Could not delete %s: %s", abs_path, e)
        lrc = abs_path.with_suffix(".lrc")
        if lrc.is_file():
            lrc.unlink(missing_ok=True)
        self._catalog.delete_location(save_folder, location.relative_path)
        cleanup_after_audio_removed(abs_path.parent, self._data_path)
        return FileActionResult(
            video_id=catalog_video_id,
            action="deleted" if deleted else "missing",
            path=location.relative_path,
        )

    def _relocate_catalog_file(
        self,
        *,
        catalog_video_id: str,
        source_folder: str,
        dest_folder: str,
        exclude_subscription_id: UUID,
        origin: str,
        keep_source_if_referenced: bool = True,
    ) -> FileActionResult:
        source_folder = source_folder.strip().replace("\\", "/").rstrip("/")
        dest_folder = dest_folder.strip().replace("\\", "/").rstrip("/")
        location = self._catalog.get_location(catalog_video_id, source_folder)
        if location is None:
            # Already present at destination (merge case).
            existing = self._catalog.get_location(catalog_video_id, dest_folder)
            if existing is not None:
                return FileActionResult(
                    video_id=catalog_video_id,
                    action="already_present",
                    path=existing.relative_path,
                )
            return FileActionResult(
                video_id=catalog_video_id,
                action="noop",
                kept_reason="source location missing",
            )

        src = resolve_under_data(
            self._data_path,
            f"{source_folder}/{location.relative_path}",
        )
        dest = resolve_under_data(
            self._data_path,
            f"{dest_folder}/{location.relative_path}",
        )
        dest.parent.mkdir(parents=True, exist_ok=True)

        action = "already_present"
        if dest.exists():
            if src.resolve() != dest.resolve() and src.is_file():
                # Destination wins; drop source only when unreferenced.
                pass
            else:
                self._catalog.upsert_location(
                    video_id=catalog_video_id,
                    save_folder=dest_folder,
                    relative_path=location.relative_path,
                    origin=origin,
                )
                return FileActionResult(
                    video_id=catalog_video_id,
                    action="already_present",
                    path=location.relative_path,
                )
        elif src.is_file():
            try:
                os.link(src, dest)
                action = "hardlinked"
            except OSError:
                try:
                    os.rename(src, dest)
                    action = "moved"
                except OSError:
                    try:
                        shutil.copy2(src, dest)
                        action = "copied"
                    except OSError as e:
                        logger.warning("Could not relocate %s → %s: %s", src, dest, e)
                        return FileActionResult(
                            video_id=catalog_video_id,
                            action="failed",
                            path=location.relative_path,
                            kept_reason=str(e),
                        )
            src_lrc = src.with_suffix(".lrc")
            dest_lrc = dest.with_suffix(".lrc")
            if src_lrc.is_file() and not dest_lrc.exists():
                try:
                    os.link(src_lrc, dest_lrc)
                except OSError:
                    try:
                        shutil.copy2(src_lrc, dest_lrc)
                    except OSError:
                        pass
            if dest.suffix.lower() in AUDIO_SUFFIXES:
                self._index.set(catalog_video_id, dest)

        self._catalog.upsert_location(
            video_id=catalog_video_id,
            save_folder=dest_folder,
            relative_path=location.relative_path,
            origin=origin,
        )

        refs = self._membership.count_refs_in_folder(
            catalog_video_id,
            source_folder,
            exclude_subscription_id=exclude_subscription_id,
        )
        if action == "moved":
            self._catalog.delete_location(source_folder, location.relative_path)
            cleanup_after_audio_removed(src.parent, self._data_path)
        elif keep_source_if_referenced and refs > 0:
            # Shared folder split/merge: keep source inode for remaining refs.
            pass
        elif src.is_file() and src.resolve() != dest.resolve():
            try:
                src.unlink()
                src_lrc = src.with_suffix(".lrc")
                if src_lrc.is_file():
                    src_lrc.unlink(missing_ok=True)
            except OSError as e:
                logger.warning("Could not prune source %s: %s", src, e)
            self._catalog.delete_location(source_folder, location.relative_path)
            cleanup_after_audio_removed(src.parent, self._data_path)

        return FileActionResult(
            video_id=catalog_video_id,
            action=action,
            path=location.relative_path,
        )

    @staticmethod
    def _to_remote_membership(
        tracks: list[TrackMetadata],
    ) -> list[RemoteMembership]:
        remote: list[RemoteMembership] = []
        seen: set[str] = set()
        for index, track in enumerate(tracks):
            # Membership key prefers stable source id; catalog uses download id.
            membership_id = (
                track.source_video_id or track.video_id or track.atv_video_id or ""
            ).strip()
            catalog_id = (track.video_id or membership_id).strip()
            if not membership_id or membership_id in seen:
                continue
            seen.add(membership_id)
            remote.append(
                RemoteMembership(
                    video_id=membership_id,
                    catalog_video_id=catalog_id,
                    title=track.title,
                    artist=track.artist,
                    album_artist=track.album_artist,
                    position=index,
                )
            )
        return remote
