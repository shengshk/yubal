"""Subscription business logic service."""

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from yubal import (
    AuthenticationRequiredError,
    PlaylistNotFoundError,
    PlaylistParseError,
    UnsupportedPlaylistError,
    UpstreamAPIError,
)
from yubal.utils.library import (
    default_subscription_save_folder,
    sanitize_save_folder,
)
from yubal.services.track_index import rewrite_track_index_prefix

from yubal_api.api.exceptions import (
    FolderConflictError,
    MetadataFetchError,
    SubscriptionConflictError,
    SubscriptionNotFoundError,
)
from yubal_api.db.subscription import Subscription, SubscriptionFields, SubscriptionType
from yubal_api.services.exclusive_ops import run_exclusive
from yubal_api.services.playlist_info_service import PlaylistInfoService
from yubal_api.services.protocols import SubscriptionRepository

logger = logging.getLogger(__name__)


def _dir_has_entries(path: Path) -> bool:
    """Return True if path exists and contains any files or directories."""
    if not path.is_dir():
        return False
    try:
        next(path.iterdir())
        return True
    except StopIteration:
        return False


class SubscriptionService:
    """Use-case layer for subscription operations.

    Encapsulates business logic for subscription CRUD, keeping route handlers
    thin (HTTP concerns only). PlaylistInfoService is injected as a concrete
    type (single implementation) — extract to a protocol if a second
    implementation is ever needed.
    """

    def __init__(
        self,
        repository: SubscriptionRepository,
        playlist_info: PlaylistInfoService,
        data_path: Path | None = None,
        *,
        ascii_filenames: bool = False,
    ) -> None:
        self._repository = repository
        self._playlist_info = playlist_info
        self._data_path = data_path
        self._ascii_filenames = ascii_filenames
        self._gate = None
        self._job_executor = None
        self._membership = None

    def bind_maintenance(self, gate, job_executor) -> None:
        """Wire OperationGate + JobExecutor for exclusive save-folder moves."""
        self._gate = gate
        self._job_executor = job_executor

    def bind_membership(self, membership_service) -> None:
        """Wire membership reconciler for reference-safe file operations."""
        self._membership = membership_service

    def list(
        self,
        *,
        enabled: bool | None = None,
        type: SubscriptionType | None = None,
    ) -> list[Subscription]:
        return self._repository.list(enabled=enabled, type=type)

    def get(self, subscription_id: UUID) -> Subscription:
        sub = self._repository.get(subscription_id)
        if sub is None:
            raise SubscriptionNotFoundError(subscription_id)
        return sub

    def create(self, url: str, max_items: int | None = None) -> Subscription:
        existing = self._repository.get_by_url(url)
        if existing is not None:
            raise SubscriptionConflictError(
                f"Subscription with URL already exists: {existing.id}",
                subscription_id=existing.id,
            )

        try:
            metadata = self._playlist_info.get_playlist_metadata(url)
        except (
            PlaylistNotFoundError,
            AuthenticationRequiredError,
            PlaylistParseError,
            UnsupportedPlaylistError,
            UpstreamAPIError,
        ):
            raise  # Known exceptions — propagate to exception handlers
        except Exception as e:
            logger.warning("Unexpected error fetching metadata for %s: %s", url, e)
            raise MetadataFetchError(str(e), upstream_error=type(e).__name__) from e

        save_folder = default_subscription_save_folder(
            metadata.title, ascii_filenames=self._ascii_filenames
        )
        subscription = Subscription(
            type=SubscriptionType.PLAYLIST,
            url=url,
            name=metadata.title,
            save_folder=save_folder,
            thumbnail_url=metadata.thumbnail_url,
            enabled=True,
            max_items=max_items,
            created_at=datetime.now(UTC),
        )
        return self._repository.create(subscription)

    def update(
        self,
        subscription_id: UUID,
        fields: SubscriptionFields,
        *,
        confirm_folder_move: bool = False,
    ) -> Subscription:
        if not fields:
            return self.get(subscription_id)

        if "save_folder" in fields:
            new_folder = sanitize_save_folder(
                fields["save_folder"], ascii_filenames=self._ascii_filenames
            )
            fields = {**fields, "save_folder": new_folder}
            sub = self.get(subscription_id)
            old_folder = sub.save_folder or sub.name
            if new_folder != old_folder:
                run_exclusive(
                    gate=self._gate,
                    job_executor=self._job_executor,
                    reason=f"move save folder {old_folder}→{new_folder}",
                    fn=lambda: self._migrate_save_folder(
                        subscription_id,
                        old_folder,
                        new_folder,
                        confirm=confirm_folder_move,
                    ),
                )

        sub = self._repository.update(subscription_id, fields)
        if sub is None:
            raise SubscriptionNotFoundError(subscription_id)
        return sub

    def _migrate_save_folder(
        self,
        subscription_id: UUID,
        old_folder: str,
        new_folder: str,
        *,
        confirm: bool,
    ) -> None:
        if self._data_path is None:
            return

        rewrite_track_index_prefix(self._data_path, old_folder, new_folder)

        old_path = self._data_path / old_folder
        new_path = self._data_path / new_folder
        new_path.parent.mkdir(parents=True, exist_ok=True)

        sub = self.get(subscription_id)
        members = (
            self._membership.list_membership(subscription_id)
            if self._membership is not None
            else []
        )
        others_on_old = [
            other
            for other in self.list()
            if other.id != subscription_id
            and (other.save_folder or other.name) == old_folder
        ]
        others_on_new = [
            other
            for other in self.list()
            if other.id != subscription_id
            and (other.save_folder or other.name) == new_folder
        ]

        # Shared folders (source or destination) must use membership moves.
        if members and (others_on_old or others_on_new or _dir_has_entries(new_path)):
            if others_on_new and not confirm and _dir_has_entries(new_path):
                raise FolderConflictError(
                    f"Target folder already has files: {new_folder}. "
                    "Confirm to merge membership into the existing folder.",
                    save_folder=new_folder,
                    subscription_id=subscription_id,
                )
            if self._membership is not None:
                moved = self._membership.migrate_save_folder(sub, old_folder, new_folder)
                logger.info(
                    "Migrated %d membership files: %s -> %s",
                    moved,
                    old_folder,
                    new_folder,
                )
            return

        if not old_path.exists():
            return

        if not _dir_has_entries(new_path):
            if new_path.exists() and new_path.is_dir():
                try:
                    new_path.rmdir()
                except OSError:
                    pass
            new_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_path), str(new_path))
            if self._membership is not None:
                self._membership.rewrite_catalog_folder(old_folder, new_folder)
            logger.info("Renamed save folder: %s -> %s", old_folder, new_folder)
            return

        if not confirm:
            raise FolderConflictError(
                f"Target folder already has files: {new_folder}. "
                "Confirm to merge into the existing folder.",
                save_folder=new_folder,
                subscription_id=subscription_id,
            )

        if self._membership is not None and members:
            moved = self._membership.migrate_save_folder(sub, old_folder, new_folder)
            logger.info(
                "Merged %d membership files: %s -> %s",
                moved,
                old_folder,
                new_folder,
            )
            return

        # Legacy fallback before the first trusted sync has populated membership.
        for item in old_path.iterdir():
            target = new_path / item.name
            if item.is_dir():
                continue
            if target.exists():
                logger.info("Keeping existing file during folder merge: %s", target)
                continue
            shutil.move(str(item), str(target))
        try:
            old_path.rmdir()
        except OSError:
            logger.warning("Could not remove old save folder (not empty): %s", old_path)
        logger.info("Merged save folder: %s -> %s", old_folder, new_folder)

    def count(
        self,
        *,
        enabled: bool | None = None,
        type: SubscriptionType | None = None,
    ) -> int:
        return self._repository.count(enabled=enabled, type=type)

    def delete(
        self,
        subscription_id: UUID,
        *,
        file_action: str = "keep",
        direct_folder: str = "direct",
    ) -> None:
        """Delete subscription or wipe files.

        ``file_action``:
        - ``keep_list``: delete files only; keep subscription + membership
        - ``keep``: leave files; delete subscription
        - ``delete``: delete files + subscription
        - ``move_to_direct``: move files into Direct; delete subscription
        """
        sub = self.get(subscription_id)
        if file_action == "keep_list":
            if self._membership is not None:
                self._membership.wipe_files_keep_list(sub)
            return

        if self._membership is not None:
            self._membership.delete_subscription_membership(
                sub,
                file_action=file_action,
                direct_folder=direct_folder,
            )
        elif self._data_path is not None and file_action != "keep":
            logger.warning(
                "Membership service unavailable; leaving files for subscription %s",
                subscription_id,
            )

        if not self._repository.delete(subscription_id):
            raise SubscriptionNotFoundError(subscription_id)

    def folders_in_use(self) -> dict[str, int]:
        """Map save_folder → number of subscriptions using it."""
        counts: dict[str, int] = {}
        for sub in self.list():
            folder = sub.save_folder or sub.name
            counts[folder] = counts.get(folder, 0) + 1
        return counts
