"""Subscription business logic service."""

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from yubal import (
    AuthenticationRequiredError,
    PlaylistNotFoundError,
    PlaylistParseError,
    UnsupportedPlaylistError,
    UpstreamAPIError,
    parse_playlist_id,
)
from yubal.services.track_index import rewrite_track_index_prefix
from yubal.utils.library import (
    default_subscription_save_folder,
    sanitize_save_folder,
)

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

LIKED_MUSIC_PLAYLIST_ID = "LM"
LIKED_MUSIC_NAME = "Liked Music"
LIKED_MUSIC_SAVE_FOLDER = "liked"


def is_liked_music_url(url: str) -> bool:
    """Whether a subscription URL points to the account's Liked Music."""
    try:
        return parse_playlist_id(url) == LIKED_MUSIC_PLAYLIST_ID
    except PlaylistParseError:
        return False


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
        self._media_changed = None

    def bind_maintenance(self, gate: Any, job_executor: Any) -> None:
        """Wire OperationGate + JobExecutor for exclusive save-folder moves."""
        self._gate = gate
        self._job_executor = job_executor

    def bind_membership(self, membership_service: Any) -> None:
        """Wire membership reconciler for reference-safe file operations."""
        self._membership = membership_service

    def bind_media_changed(self, callback: Any) -> None:
        self._media_changed = callback

    def _notify_media_changed(self) -> None:
        if callable(self._media_changed):
            self._media_changed()

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

        is_liked_music = is_liked_music_url(url)
        if is_liked_music:
            name = LIKED_MUSIC_NAME
            save_folder = LIKED_MUSIC_SAVE_FOLDER
            account_fingerprint = self._playlist_info.get_account_fingerprint()
        else:
            name = metadata.title
            save_folder = default_subscription_save_folder(
                metadata.title, ascii_filenames=self._ascii_filenames
            )
            account_fingerprint = None
        subscription = Subscription(
            type=SubscriptionType.PLAYLIST,
            url=url,
            name=name,
            save_folder=save_folder,
            thumbnail_url=metadata.thumbnail_url,
            enabled=True,
            max_items=max_items,
            source_account_fingerprint=account_fingerprint,
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

        sub = self.get(subscription_id)
        if is_liked_music_url(sub.url):
            requested_folder = fields.get("save_folder")
            if (
                requested_folder is not None
                and sanitize_save_folder(
                    requested_folder,
                    ascii_filenames=self._ascii_filenames,
                )
                != LIKED_MUSIC_SAVE_FOLDER
            ):
                raise SubscriptionConflictError(
                    "Liked Music uses the fixed folder 'liked'.",
                    subscription_id=subscription_id,
                )
            fields = {**fields, "name": LIKED_MUSIC_NAME}

        if "save_folder" in fields:
            new_folder = sanitize_save_folder(
                fields["save_folder"], ascii_filenames=self._ascii_filenames
            )
            fields = {**fields, "save_folder": new_folder}
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
                    ),
                )

        sub = self._repository.update(subscription_id, fields)
        if sub is None:
            raise SubscriptionNotFoundError(subscription_id)
        return sub

    def prepare_for_sync(self, subscription: Subscription) -> Subscription:
        """Validate fixed Liked Music settings and prevent account mixing."""
        if not is_liked_music_url(subscription.url):
            return subscription
        if subscription.save_folder != LIKED_MUSIC_SAVE_FOLDER:
            raise SubscriptionConflictError(
                "Liked Music must use the fixed folder 'liked'.",
                subscription_id=subscription.id,
            )

        current_fingerprint = self._playlist_info.get_account_fingerprint()
        bound_fingerprint = subscription.source_account_fingerprint
        if bound_fingerprint and bound_fingerprint != current_fingerprint:
            raise SubscriptionConflictError(
                "Liked Music belongs to a different YouTube Music account. "
                "Sync was blocked to prevent mixing libraries.",
                subscription_id=subscription.id,
            )

        updates: SubscriptionFields = {"name": LIKED_MUSIC_NAME}
        if not bound_fingerprint:
            updates["source_account_fingerprint"] = current_fingerprint
        updated = self._repository.update(subscription.id, updates)
        if updated is None:
            raise SubscriptionNotFoundError(subscription.id)
        return updated

    def rate_liked_song(
        self,
        subscription_id: UUID,
        video_id: str,
        *,
        liked: bool,
    ) -> Subscription:
        """Change an account Like, restricted to the special Liked Music list."""
        subscription = self.get(subscription_id)
        if not is_liked_music_url(subscription.url):
            raise SubscriptionConflictError(
                "Only the Liked Music subscription can change YTM likes.",
                subscription_id=subscription_id,
            )
        self.prepare_for_sync(subscription)
        self._playlist_info.rate_song(video_id, liked=liked)
        return subscription

    def _migrate_save_folder(
        self,
        subscription_id: UUID,
        old_folder: str,
        new_folder: str,
    ) -> None:
        if self._data_path is None:
            return

        old_path = self._data_path / old_folder
        new_path = self._data_path / new_folder
        new_path.parent.mkdir(parents=True, exist_ok=True)

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

        # Subscription paths are the only editable paths, but they remain
        # exclusive.  No shared-folder split and no merge mode: the target
        # must be empty so a rename has one unambiguous physical outcome.
        if others_on_old or others_on_new:
            raise FolderConflictError(
                "Subscription folders must be exclusive; choose an unused folder.",
                save_folder=new_folder,
                subscription_id=subscription_id,
            )
        if _dir_has_entries(new_path):
            raise FolderConflictError(
                f"Target folder must be empty: {new_folder}.",
                save_folder=new_folder,
                subscription_id=subscription_id,
            )
        if old_path.exists():
            if new_path.exists():
                new_path.rmdir()
            shutil.move(str(old_path), str(new_path))
        if self._membership is not None:
            self._membership.rewrite_catalog_folder(old_folder, new_folder)
        rewrite_track_index_prefix(self._data_path, old_folder, new_folder)
        self._notify_media_changed()
        logger.info("Renamed save folder: %s -> %s", old_folder, new_folder)

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
