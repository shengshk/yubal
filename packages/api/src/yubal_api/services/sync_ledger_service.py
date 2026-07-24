"""Sync ledger business logic."""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from yubal import DownloadStatus, PhaseStats, SkipReason
from yubal.services.track_index import rewrite_track_index_prefix
from yubal.utils.library import (
    DIRECT_FOLDER,
    resolve_under_data,
    sanitize_direct_folder,
)

from yubal_api.api.exceptions import FolderConflictError
from yubal_api.db.track_catalog import LocationMembershipStatus
from yubal_api.db.sync_ledger import DIRECT_LEDGER_KEY, LedgerKind, SyncLedgerEntry
from yubal_api.db.sync_ledger_repository import SyncLedgerRepository
from yubal_api.db.track_catalog_repository import (
    TrackCatalogRepository,
    format_track_display,
)
from yubal_api.domain.job import ContentInfo, Job
from yubal_api.services.exclusive_ops import run_exclusive
from yubal_api.services.library_ops import (
    cleanup_after_audio_removed,
    delete_track_file,
    delete_tree_audio,
    list_folder_tracks,
    reconcile_folder_counts,
)
from yubal_api.services.operation_gate import OperationGate
from yubal_api.services.preferences import PreferencesStore

if TYPE_CHECKING:
    from yubal_api.services.external_library_service import ExternalLibraryService
    from yubal_api.services.job_executor import JobExecutor

logger = logging.getLogger(__name__)


def subscription_ledger_key(subscription_id: UUID) -> str:
    return f"subscription:{subscription_id}"


def _classify_existing(path: Path | None) -> str:
    """Classify an on-disk file as real download or hardlink via nlink."""
    if path is None or not path.exists():
        return "real"
    try:
        if path.stat().st_nlink > 1:
            return "hardlink"
    except OSError:
        pass
    return "real"


def compute_ledger_counts(
    stats: PhaseStats | None,
    *,
    results_paths: list[tuple[DownloadStatus, Path | None, SkipReason | None]]
    | None = None,
) -> dict[str, int]:
    """Compute ledger counters from phase stats and optional per-result paths."""
    if stats is None and not results_paths:
        return {
            "total_count": 0,
            "synced_count": 0,
            "real_download_count": 0,
            "hardlink_count": 0,
            "failed_count": 0,
            "skipped_ugc": 0,
            "skipped_region": 0,
            "skipped_other": 0,
        }

    if results_paths is not None:
        real = 0
        hard = 0
        synced = 0
        failed = 0
        ugc = 0
        region = 0
        other = 0
        for status, path, skip_reason in results_paths:
            if status == DownloadStatus.SUCCESS:
                real += 1
                synced += 1
            elif status == DownloadStatus.PRESELECTED:
                real += 1
                synced += 1
            elif status == DownloadStatus.HARDLINKED:
                hard += 1
                synced += 1
            elif status == DownloadStatus.SKIPPED:
                if skip_reason == SkipReason.FILE_EXISTS:
                    synced += 1
                    if _classify_existing(path) == "hardlink":
                        hard += 1
                    else:
                        real += 1
                elif skip_reason == SkipReason.UGC:
                    ugc += 1
                elif skip_reason == SkipReason.REGION_UNAVAILABLE:
                    region += 1
                else:
                    other += 1
            elif status == DownloadStatus.FAILED:
                failed += 1
        total = len(results_paths)
        return {
            "total_count": total,
            "synced_count": synced,
            "real_download_count": real,
            "hardlink_count": hard,
            "failed_count": failed,
            "skipped_ugc": ugc,
            "skipped_region": region,
            "skipped_other": other,
        }

    assert stats is not None
    skipped = stats.skipped_by_reason
    file_exists = skipped.get(SkipReason.FILE_EXISTS, 0)
    ugc = skipped.get(SkipReason.UGC, 0)
    region = skipped.get(SkipReason.REGION_UNAVAILABLE, 0)
    other = stats.skipped - file_exists - ugc - region
    synced = stats.success + stats.hardlinked + file_exists
    return {
        "total_count": stats.total,
        "synced_count": synced,
        "real_download_count": stats.success + file_exists,
        "hardlink_count": stats.hardlinked,
        "failed_count": stats.failed,
        "skipped_ugc": ugc,
        "skipped_region": region,
        "skipped_other": max(0, other),
    }


class SyncLedgerService:
    """Upsert sync facts when jobs start/finish; reconcile against disk."""

    def __init__(
        self,
        repository: SyncLedgerRepository,
        *,
        data_path: Path | None = None,
        preferences_store: PreferencesStore | None = None,
        track_catalog: TrackCatalogRepository | None = None,
    ) -> None:
        self._repository = repository
        self._data_path = data_path
        self._preferences_store = preferences_store
        self._track_catalog = track_catalog
        self._gate = None
        self._job_executor = None
        self._subscription_folder_lookup = None
        self._external: ExternalLibraryService | None = None

    def bind_external_library(self, external: ExternalLibraryService) -> None:
        self._external = external

    def _cover_tier_kwargs(
        self,
        video_id: str | None,
        *,
        store: object | None = None,
        excellence_px: int | None = None,
    ) -> dict:
        excellence = excellence_px
        probe_days = 7
        download_days = 30
        if self._preferences_store is not None:
            prefs = self._preferences_store.effective()
            if excellence is None:
                excellence = int(
                    getattr(prefs, "cover_excellence_px", 0) or 0
                )
            probe_days = int(getattr(prefs, "cover_probe_fresh_days", 7) or 7)
            download_days = int(
                getattr(prefs, "cover_download_fresh_days", 30) or 30
            )
        if excellence is None:
            excellence = 0
        if not video_id or self._data_path is None:
            return {
                "cover_excellence_px": excellence,
                "cover_probe_fresh_days": probe_days,
                "cover_download_fresh_days": download_days,
            }
        if store is None:
            from yubal.services.scrape_state import ScrapeStateStore

            store = ScrapeStateStore(self._data_path)
        state = store.get(video_id)  # type: ignore[union-attr]
        return {
            "cover_compared_at": state.effective_compared_at(),
            "cover_check_kind": state.effective_check_kind(),
            "cover_width": state.cover_width,
            "cover_height": state.cover_height,
            "cover_excellence_px": excellence,
            "cover_probe_fresh_days": probe_days,
            "cover_download_fresh_days": download_days,
        }

    def bind_maintenance(
        self, gate: OperationGate, job_executor: JobExecutor
    ) -> None:
        """Wire OperationGate + JobExecutor for exclusive Direct folder moves."""
        self._gate = gate
        self._job_executor = job_executor

    def bind_subscription_folders(
        self, lookup: Callable[[UUID], str | None]
    ) -> None:
        """Resolve current subscription save_folder during reconcile."""
        self._subscription_folder_lookup = lookup

    def reconcile_interrupted_jobs(self) -> int:
        """Resolve ledger rows left ``running`` by a crash/restart. Startup-only."""
        return self._repository.mark_stale_running_interrupted()

    def get_catalog_lyrics(self, library_path: str) -> str | None:
        """Return lyrics stored in the track catalog for a library-relative path."""
        if self._track_catalog is None:
            return None
        return self._track_catalog.get_lyrics_by_library_path(library_path)

    def get_catalog_lyrics_source(self, library_path: str) -> str | None:
        """Return recorded lyrics provenance for a library-relative path."""
        if self._track_catalog is None:
            return None
        return self._track_catalog.get_lyrics_source_by_library_path(library_path)

    def set_catalog_lyrics(
        self, library_path: str, lyrics: str, *, source: str | None = "manual"
    ) -> bool:
        """Update catalog lyrics when a matching track exists."""
        if self._track_catalog is None:
            return False
        return self._track_catalog.set_lyrics_by_library_path(
            library_path, lyrics, source=source
        )

    def _direct_folder(self) -> str:
        if self._preferences_store is not None:
            return self._preferences_store.effective().direct_folder
        return DIRECT_FOLDER

    def list(self, *, reconcile: bool = True) -> list[SyncLedgerEntry]:
        items = self._repository.list()
        if reconcile and self._data_path is not None:
            items = [self._reconcile_entry(e) for e in items]
        return items

    def list_tracks(self, save_folder: str) -> tuple[str, list]:
        """List tracks for a relative save folder under the data root.

        Prefers the track catalog table (written on download). Falls back to
        on-disk scan when the catalog has no rows for this folder yet.
        """
        from yubal.utils.library import classify_audio_file

        from yubal_api.domain.track_quality import (
            compute_track_tier,
            lyrics_are_synced,
        )
        from yubal_api.schemas.sync_ledger import SyncTrackItem
        from yubal_api.services.library_hardlink import classify_catalog_file

        if self._data_path is None:
            return save_folder, []
        folder = (save_folder or "").strip().replace("\\", "/")
        if not folder or folder.startswith("/") or ".." in folder.split("/"):
            raise ValueError("invalid save_folder")
        try:
            root = resolve_under_data(self._data_path, folder)
        except ValueError as e:
            raise ValueError(str(e)) from e

        def _storage(path: Path) -> tuple[bool, str]:
            if not path.is_file():
                return False, "missing"
            return True, classify_audio_file(path)

        if self._track_catalog is not None:
            # List order is unified on the client (bucket → sort key → path).
            # Keep catalog order by relative_path for a stable API index.
            catalog_rows = self._track_catalog.list_for_save_folder(
                folder, order_by_recent=False
            )
            if catalog_rows:
                items: list[SyncTrackItem] = []
                scrape_store = None
                inode_cache: dict[str, list[tuple[str, tuple[int, int]]]] = {}
                if self._data_path is not None:
                    from yubal.services.scrape_state import ScrapeStateStore

                    scrape_store = ScrapeStateStore(self._data_path)
                excellence = 0
                if self._preferences_store is not None:
                    excellence = int(
                        getattr(
                            self._preferences_store.effective(),
                            "cover_excellence_px",
                            0,
                        )
                        or 0
                    )
                for loc, track in catalog_rows:
                    abs_path = (root / loc.relative_path).resolve()
                    try:
                        abs_path.relative_to(root.resolve())
                    except ValueError:
                        exists, storage = False, "missing"
                    else:
                        if not abs_path.is_file():
                            exists, storage = False, "missing"
                        else:
                            storage = classify_catalog_file(
                                abs_path,
                                video_id=loc.video_id,
                                save_folder=folder,
                                catalog=self._track_catalog,
                                download_root=self._data_path,
                                location_inode_cache=inode_cache,
                            )
                            exists = True
                    label = format_track_display(
                        track.artist, track.album_artist, track.title
                    )
                    has_lyrics = bool(
                        track.has_lyrics_embedded
                        or track.has_lyrics_sidecar
                        or (track.lyrics and track.lyrics.strip())
                    )
                    synced = lyrics_are_synced(track.lyrics)
                    if not synced and exists and abs_path.is_file():
                        # Fall back to the on-disk .lrc when catalog text is
                        # missing or plain (common for older imports).
                        try:
                            lrc = abs_path.with_suffix(".lrc")
                            if lrc.is_file():
                                synced = lyrics_are_synced(
                                    lrc.read_text(
                                        encoding="utf-8", errors="ignore"
                                    )
                                )
                        except OSError:
                            pass
                    cover_kw = self._cover_tier_kwargs(
                        loc.video_id,
                        store=scrape_store,
                        excellence_px=excellence,
                    )
                    tier = compute_track_tier(
                        title=track.title,
                        artist=track.artist,
                        has_embedded_cover=track.has_embedded_cover,
                        has_lyrics=has_lyrics,
                        cover_source=track.cover_source,
                        has_synced_lyrics=synced,
                        **cover_kw,
                    )
                    items.append(
                        SyncTrackItem(
                            index=len(items) + 1,
                            title=track.title,
                            artist=track.artist,
                            album_artist=track.album_artist,
                            display_label=label,
                            exists=exists,
                            storage=storage,
                            relative_path=loc.relative_path,
                            video_id=loc.video_id,
                            cover_url=track.cover_url,
                            album=track.album,
                            year=track.year,
                            track_number=track.track_number,
                            tier=tier,
                            has_embedded_cover=track.has_embedded_cover,
                            has_lyrics=has_lyrics,
                            has_synced_lyrics=synced,
                            cover_source=track.cover_source,
                            membership_status=(
                                str(loc.membership_status)
                                if getattr(loc, "membership_status", None)
                                else "active"
                            ),
                        )
                    )
                return folder, items

        tracks = list_folder_tracks(root)
        items = [
            SyncTrackItem(
                index=t.index,
                title=t.title,
                artist=t.artist,
                album_artist=None,
                display_label=(
                    f"{t.artist} - {t.title}" if t.artist else t.title
                ),
                exists=t.exists,
                storage=t.storage,
                relative_path=t.relative_path,
            )
            for t in tracks
        ]
        return folder, items

    def record_download_results(
        self,
        save_folder: str,
        results: list,
    ) -> None:
        """Persist track catalog rows from download results."""
        if self._track_catalog is None or self._data_path is None:
            return
        folder = (save_folder or "").strip().replace("\\", "/").rstrip("/")
        if not folder:
            return
        for result in results:
            status = getattr(result, "status", None)
            path = getattr(result, "output_path", None)
            track = getattr(result, "track", None)
            video_id = getattr(result, "video_id_used", None) or (
                track.video_id if track else None
            )
            if (
                status
                not in (
                    DownloadStatus.SUCCESS,
                    DownloadStatus.PRESELECTED,
                    DownloadStatus.HARDLINKED,
                    DownloadStatus.SKIPPED,
                )
                or path is None
                or track is None
                or not video_id
            ):
                continue
            if status == DownloadStatus.SKIPPED:
                skip = getattr(result, "skip_reason", None)
                if skip not in (None, SkipReason.FILE_EXISTS):
                    continue
                try:
                    rel_to_data = Path(path).resolve().relative_to(
                        self._data_path.resolve()
                    )
                except ValueError:
                    continue
                parts = rel_to_data.parts
                folder_parts = tuple(p for p in folder.split("/") if p)
                if parts[: len(folder_parts)] != folder_parts:
                    continue
                relative_path = (
                    str(Path(*parts[len(folder_parts) :]))
                    if len(parts) > len(folder_parts)
                    else Path(path).name
                )
                origin = getattr(result, "origin", None) or "download"
                try:
                    self._track_catalog.upsert_location(
                        video_id=str(video_id),
                        save_folder=folder,
                        relative_path=relative_path,
                        origin=origin,
                    )
                except Exception:
                    logger.exception(
                        "Failed to record skipped location for %s", video_id
                    )
                continue
            origin = getattr(result, "origin", None)
            if not origin:
                if status == DownloadStatus.PRESELECTED:
                    origin = "preselect_link"
                elif status == DownloadStatus.HARDLINKED:
                    origin = "dedupe_link"
                else:
                    origin = "download"
            try:
                self._track_catalog.record_from_download(
                    video_id=str(video_id),
                    title=track.title,
                    artist=track.artist,
                    album_artist=track.album_artist,
                    album=track.album,
                    track_number=track.track_number,
                    year=track.year,
                    cover_url=track.cover_url,
                    save_folder=folder,
                    absolute_path=Path(path),
                    data_root=self._data_path,
                    origin=origin,
                )
            except Exception:
                logger.exception(
                    "Failed to record track catalog for %s", video_id
                )

    def delete_for_subscription(self, subscription_id: UUID) -> None:
        self._repository.delete_by_subscription_id(subscription_id)

    def reconcile_direct(
        self, direct_folder: str | None = None
    ) -> SyncLedgerEntry | None:
        folder = direct_folder or self._direct_folder()
        entry = self._repository.get_by_key(DIRECT_LEDGER_KEY)
        if entry is None:
            if self._data_path is None:
                return None
            path = resolve_under_data(self._data_path, folder)
            counts = reconcile_folder_counts(path)
            entry = SyncLedgerEntry(
                key=DIRECT_LEDGER_KEY,
                kind=LedgerKind.DIRECT,
                save_folder=folder,
                title=folder.split("/")[-1] or folder,
                content_kind="playlist",
                total_count=counts.synced_count,
                synced_count=counts.synced_count,
                real_download_count=counts.real_download_count,
                hardlink_count=counts.hardlink_count,
            )
            return self._repository.upsert(entry)
        # Counts only — do not bump last_synced_at (reconcile ≠ download)
        return self._reconcile_entry(entry)

    def ensure_direct_entry(self) -> SyncLedgerEntry:
        """Return Direct ledger row after reconcile (creates empty row if needed)."""
        entry = self.reconcile_direct()
        if entry is not None:
            return entry
        folder = self._direct_folder()
        entry = SyncLedgerEntry(
            key=DIRECT_LEDGER_KEY,
            kind=LedgerKind.DIRECT,
            save_folder=folder,
            title=folder.split("/")[-1] or folder,
            content_kind="playlist",
            total_count=0,
            synced_count=0,
            real_download_count=0,
            hardlink_count=0,
        )
        return self._repository.upsert(entry)

    def update_direct_folder(
        self,
        new_folder: str | None = None,
        *,
        confirm_folder_move: bool = False,
        enabled: bool | None = None,
        max_items: int | None = None,
        sync_jitter_seconds: int | None = None,
        offline_marking_enabled: bool | None = None,
        offline_cleanup_enabled: bool | None = None,
        offline_cleanup_action: str | None = None,
        offline_cleanup_delay_hours: int | None = None,
    ) -> SyncLedgerEntry:
        if self._data_path is None or self._preferences_store is None:
            raise RuntimeError(
                "Direct folder update requires data_path and preferences"
            )

        prefs_updates: dict = {}
        if enabled is not None:
            prefs_updates["direct_auto_recover_enabled"] = bool(enabled)
        if max_items is not None:
            prefs_updates["direct_max_items"] = max(1, min(10000, int(max_items)))
        if sync_jitter_seconds is not None:
            prefs_updates["direct_sync_jitter_seconds"] = max(
                0, min(600, int(sync_jitter_seconds))
            )
        if offline_marking_enabled is not None:
            prefs_updates["direct_offline_marking_enabled"] = bool(
                offline_marking_enabled
            )
        if offline_cleanup_enabled is not None:
            prefs_updates["direct_offline_cleanup_enabled"] = bool(
                offline_cleanup_enabled
            )
        if offline_cleanup_action is not None:
            action = str(offline_cleanup_action).lower().strip()
            if action not in {"delete", "archive"}:
                raise ValueError(f"invalid offline_cleanup_action: {action}")
            prefs_updates["direct_offline_cleanup_action"] = action
        if offline_cleanup_delay_hours is not None:
            prefs_updates["direct_offline_cleanup_delay_hours"] = max(
                0, min(8760, int(offline_cleanup_delay_hours))
            )

        if new_folder is None or not str(new_folder).strip():
            if prefs_updates:
                self._preferences_store.update(**prefs_updates)
            return self.ensure_direct_entry()

        safe = sanitize_direct_folder(new_folder)
        old = self._direct_folder()
        if safe == old:
            if prefs_updates:
                self._preferences_store.update(**prefs_updates)
            return self._finalize_direct_folder(safe)

        entry = run_exclusive(
            gate=self._gate,
            job_executor=self._job_executor,
            reason=f"move Direct folder {old}→{safe}",
            fn=lambda: self._move_direct_folder(
                old, safe, confirm=confirm_folder_move
            ),
        )
        if prefs_updates:
            self._preferences_store.update(**prefs_updates)
            entry = self.ensure_direct_entry()
        return entry

    def _move_direct_folder(
        self, old: str, safe: str, *, confirm: bool
    ) -> SyncLedgerEntry:
        assert self._data_path is not None and self._preferences_store is not None
        old_path = resolve_under_data(self._data_path, old)
        new_path = resolve_under_data(self._data_path, safe)
        new_path.parent.mkdir(parents=True, exist_ok=True)
        if old_path.exists() and old_path.resolve() != new_path.resolve():
            if new_path.exists() and any(new_path.iterdir()):
                if not confirm:
                    raise FolderConflictError(
                        f"Target folder already has files: {safe}. "
                        "Confirm to merge into the existing folder.",
                        save_folder=safe,
                    )
                for item in list(old_path.iterdir()):
                    target = new_path / item.name
                    if target.exists():
                        continue
                    shutil.move(str(item), str(target))
                try:
                    old_path.rmdir()
                except OSError:
                    pass
            else:
                if new_path.exists():
                    try:
                        new_path.rmdir()
                    except OSError:
                        pass
                shutil.move(str(old_path), str(new_path))
        rewrite_track_index_prefix(self._data_path, old, safe)
        self._preferences_store.update(direct_folder=safe)
        return self._finalize_direct_folder(safe)

    def _finalize_direct_folder(self, safe: str) -> SyncLedgerEntry:
        entry = self._repository.get_by_key(DIRECT_LEDGER_KEY)
        if entry is None:
            entry = SyncLedgerEntry(
                key=DIRECT_LEDGER_KEY,
                kind=LedgerKind.DIRECT,
                save_folder=safe,
                title=safe.split("/")[-1] or safe,
                content_kind="playlist",
            )
        else:
            entry.save_folder = safe
            entry.title = safe.split("/")[-1] or safe
        self._repository.upsert(entry)
        return self.reconcile_direct(safe) or entry

    def delete_direct(
        self, *, confirm: bool = False, mode: str = "wipe_list"
    ) -> dict | None:
        """Delete Direct files or operate on offline / migrate.

        Modes:
        - ``keep_list``: delete audio only; catalog locations remain for recover.
        - ``wipe_list``: delete audio + catalog locations + ledger row.
        - ``clear_offline_delete``: hard-delete offline list rows + files.
        - ``clear_offline_to_raw_delete``: move offline files to Raw/Delete, drop ids.
        - ``migrate_to_external``: move active tracks to Organized/Default.
        """
        if mode in (
            "clear_offline_delete",
            "clear_offline_to_raw_delete",
            "migrate_to_external",
        ):
            if mode.startswith("clear_offline"):
                return self.clear_direct_offline(
                    to_raw_delete=mode == "clear_offline_to_raw_delete"
                )
            return self.migrate_direct_to_external()

        if not confirm:
            raise FolderConflictError(
                "Confirm deletion of all Direct-download files.",
                save_folder=self._direct_folder(),
            )
        folder = self._direct_folder()
        if self._data_path is not None:
            path = resolve_under_data(self._data_path, folder)
            delete_tree_audio(path)
            try:
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            except OSError:
                pass
        if mode == "wipe_list":
            if self._track_catalog is not None:
                self._track_catalog.delete_all_for_save_folder(folder)
            existing = self._repository.get_by_key(DIRECT_LEDGER_KEY)
            if existing is not None:
                self._repository.delete_by_key(DIRECT_LEDGER_KEY)
        else:
            # keep_list: recount zeros on disk but preserve ledger + catalog
            self.reconcile_direct(folder)
        return None

    def delete_direct_track(
        self, relative_path: str, *, mode: str = "keep_list"
    ) -> SyncLedgerEntry:
        """Delete or migrate one Direct audio file.

        ``keep_list``: file gone, catalog kept for auto-recover.
        ``wipe_list``: remove catalog location.
        ``block``: delete file + mark blocked (禁止回补).
        ``migrate_to_external``: move file+list to Organized/Default.
        """
        if self._data_path is None:
            raise ValueError("data path not configured")
        folder = self._direct_folder()
        if mode == "migrate_to_external":
            self._migrate_direct_track(relative_path)
        elif mode == "block":
            delete_track_file(
                data_path=self._data_path,
                save_folder=folder,
                relative_path=relative_path,
            )
            if self._track_catalog is not None:
                loc = self._track_catalog.get_location_by_relative_path(
                    folder, relative_path
                )
                if loc is not None:
                    self._track_catalog.set_membership_status(
                        folder,
                        loc.video_id,
                        LocationMembershipStatus.BLOCKED,
                    )
        else:
            delete_track_file(
                data_path=self._data_path,
                save_folder=folder,
                relative_path=relative_path,
            )
            if mode == "wipe_list" and self._track_catalog is not None:
                self._track_catalog.delete_location(folder, relative_path)
        entry = self.reconcile_direct(folder)
        if entry is None:
            entry = self.ensure_direct_entry()
        return entry

    def unblock_direct_track(self, video_id: str) -> SyncLedgerEntry:
        """Clear Direct block so auto-recover may restore the track."""
        if self._track_catalog is None:
            raise ValueError("track catalog not configured")
        folder = self._direct_folder()
        loc = self._track_catalog.get_location(video_id, folder)
        if loc is None:
            raise ValueError("track not in Direct list")
        self._track_catalog.set_membership_status(
            folder, video_id, LocationMembershipStatus.ACTIVE
        )
        entry = self.reconcile_direct(folder)
        if entry is None:
            entry = self.ensure_direct_entry()
        return entry

    def remove_direct_track_from_list(self, video_id: str) -> SyncLedgerEntry:
        """Drop a Direct list row (blocked/offline placeholder) permanently."""
        if self._track_catalog is None:
            raise ValueError("track catalog not configured")
        folder = self._direct_folder()
        loc = self._track_catalog.get_location(video_id, folder)
        if loc is None:
            raise ValueError("track not in Direct list")
        if self._data_path is not None:
            abs_path = resolve_under_data(
                self._data_path, f"{folder}/{loc.relative_path}"
            )
            if abs_path.is_file():
                try:
                    abs_path.unlink()
                except OSError as e:
                    logger.warning("Could not delete %s: %s", abs_path, e)
                lrc = abs_path.with_suffix(".lrc")
                if lrc.is_file():
                    lrc.unlink(missing_ok=True)
        self._track_catalog.delete_location(folder, loc.relative_path)
        entry = self.reconcile_direct(folder)
        if entry is None:
            entry = self.ensure_direct_entry()
        return entry

    def clear_direct_offline(self, *, to_raw_delete: bool = False) -> dict:
        """Remove offline Direct rows; optionally salvage files into Raw/Delete."""
        if self._track_catalog is None:
            return {"cleared": 0, "moved": 0, "errors": 0}
        folder = self._direct_folder()
        stop_at = (
            resolve_under_data(self._data_path, folder)
            if self._data_path is not None
            else None
        )
        rows = [
            (loc, rec)
            for loc, rec in self._track_catalog.list_for_save_folder(folder)
            if loc.membership_status == LocationMembershipStatus.OFFLINE
        ]
        cleared = moved = errors = 0
        for loc, rec in rows:
            abs_path = None
            if self._data_path is not None:
                abs_path = resolve_under_data(
                    self._data_path, f"{folder}/{loc.relative_path}"
                )
            if to_raw_delete:
                if self._external is None:
                    raise RuntimeError("external library not configured")
                try:
                    if abs_path is not None and abs_path.is_file():
                        dest = self._external.ingest_file_to_raw_delete(
                            abs_path,
                            title=rec.title,
                            artists=rec.artist,
                            album=rec.album or "",
                            album_artist=rec.album_artist or "",
                            year=rec.year,
                            track_number=rec.track_number,
                        )
                        if dest is not None:
                            moved += 1
                        if stop_at is not None:
                            cleanup_after_audio_removed(abs_path.parent, stop_at)
                    self._track_catalog.delete_location(folder, loc.relative_path)
                    cleared += 1
                except Exception:
                    errors += 1
                    logger.exception(
                        "Failed offline→Raw/Delete for %s", loc.relative_path
                    )
            else:
                if abs_path is not None and abs_path.is_file():
                    try:
                        abs_path.unlink()
                        lrc = abs_path.with_suffix(".lrc")
                        if lrc.is_file():
                            lrc.unlink(missing_ok=True)
                        if stop_at is not None:
                            cleanup_after_audio_removed(abs_path.parent, stop_at)
                    except OSError:
                        errors += 1
                        continue
                self._track_catalog.delete_location(folder, loc.relative_path)
                cleared += 1
        self.reconcile_direct(folder)
        return {"cleared": cleared, "moved": moved, "errors": errors}

    def migrate_direct_to_external(self) -> dict:
        """Move all active Direct tracks to Organized/Default."""
        if self._track_catalog is None or self._external is None:
            raise RuntimeError("external library not configured")
        folder = self._direct_folder()
        moved = errors = skipped = 0
        for loc, rec in list(self._track_catalog.list_for_save_folder(folder)):
            if loc.membership_status != LocationMembershipStatus.ACTIVE:
                skipped += 1
                continue
            try:
                self._migrate_direct_track(loc.relative_path)
                moved += 1
            except Exception:
                errors += 1
                logger.exception("Failed Direct→Default for %s", loc.relative_path)
        self.reconcile_direct(folder)
        return {"moved": moved, "errors": errors, "skipped": skipped}

    def _migrate_direct_track(self, relative_path: str) -> None:
        if self._track_catalog is None or self._external is None:
            raise RuntimeError("external library not configured")
        if self._data_path is None:
            raise ValueError("data path not configured")
        folder = self._direct_folder()
        loc = self._track_catalog.get_location_by_relative_path(
            folder, relative_path
        )
        if loc is None:
            raise ValueError(f"catalog location not found: {relative_path}")
        rec = self._track_catalog.get_track(loc.video_id)
        if rec is None:
            raise ValueError(f"track record not found: {loc.video_id}")
        abs_path = resolve_under_data(
            self._data_path, f"{folder}/{loc.relative_path}"
        )
        self._external.ingest_matched_to_default(
            abs_path,
            relative_path=loc.relative_path,
            video_id=loc.video_id,
            title=rec.title,
            artist=rec.artist,
            album_artist=rec.album_artist,
            album=rec.album or "",
            year=rec.year,
            track_number=rec.track_number,
            cover_url=rec.cover_url,
        )
        self._track_catalog.delete_location(folder, loc.relative_path)

    def direct_offline_count(self) -> int:
        if self._track_catalog is None:
            return 0
        folder = self._direct_folder()
        rows = self._track_catalog.list_for_save_folder(folder)
        return sum(
            1
            for loc, _ in rows
            if loc.membership_status == LocationMembershipStatus.OFFLINE
        )

    def direct_blocked_count(self) -> int:
        if self._track_catalog is None:
            return 0
        folder = self._direct_folder()
        rows = self._track_catalog.list_for_save_folder(folder)
        return sum(
            1
            for loc, _ in rows
            if loc.membership_status == LocationMembershipStatus.BLOCKED
        )

    def direct_policy(self) -> dict:
        if self._preferences_store is None:
            return {
                "enabled": False,
                "max_items": 100,
                "sync_jitter_seconds": 600,
                "offline_marking_enabled": True,
                "offline_cleanup_enabled": False,
                "offline_cleanup_action": "archive",
                "offline_cleanup_delay_hours": 72,
            }
        prefs = self._preferences_store.effective()
        return {
            "enabled": prefs.direct_auto_recover_enabled,
            "max_items": prefs.direct_max_items,
            "sync_jitter_seconds": prefs.direct_sync_jitter_seconds,
            "offline_marking_enabled": prefs.direct_offline_marking_enabled,
            "offline_cleanup_enabled": prefs.direct_offline_cleanup_enabled,
            "offline_cleanup_action": prefs.direct_offline_cleanup_action,
            "offline_cleanup_delay_hours": prefs.direct_offline_cleanup_delay_hours,
        }

    def run_id_invalid_cleanup(self, *, now: datetime | None = None) -> int:
        """Dispose due Direct ID-invalid locations (archive → Raw/Delete)."""
        if self._track_catalog is None or self._preferences_store is None:
            return 0
        prefs = self._preferences_store.effective()
        if not prefs.direct_offline_cleanup_enabled:
            return 0
        now = now or datetime.now(UTC)
        delay = max(0, int(prefs.direct_offline_cleanup_delay_hours))
        cutoff = now - timedelta(hours=delay)
        to_raw = (prefs.direct_offline_cleanup_action or "archive") != "delete"
        folder = self._direct_folder()
        stop_at = (
            resolve_under_data(self._data_path, folder)
            if self._data_path is not None
            else None
        )
        cleared = 0
        for loc, rec in list(self._track_catalog.list_for_save_folder(folder)):
            if loc.membership_status != LocationMembershipStatus.OFFLINE:
                continue
            if loc.missing_since is None or loc.missing_since > cutoff:
                continue
            abs_path = None
            if self._data_path is not None:
                abs_path = resolve_under_data(
                    self._data_path, f"{folder}/{loc.relative_path}"
                )
            try:
                if to_raw:
                    if self._external is None:
                        raise RuntimeError("external library not configured")
                    if abs_path is not None and abs_path.is_file():
                        self._external.ingest_file_to_raw_delete(
                            abs_path,
                            title=rec.title,
                            artists=rec.artist,
                            album=rec.album or "",
                            album_artist=rec.album_artist or "",
                            year=rec.year,
                            track_number=rec.track_number,
                        )
                        if stop_at is not None:
                            cleanup_after_audio_removed(abs_path.parent, stop_at)
                else:
                    if abs_path is not None and abs_path.is_file():
                        abs_path.unlink()
                        lrc = abs_path.with_suffix(".lrc")
                        if lrc.is_file():
                            lrc.unlink(missing_ok=True)
                        if stop_at is not None:
                            cleanup_after_audio_removed(abs_path.parent, stop_at)
                self._track_catalog.delete_location(folder, loc.relative_path)
                cleared += 1
            except Exception:
                logger.exception(
                    "Failed Direct ID-invalid cleanup for %s", loc.relative_path
                )
        if cleared:
            self.reconcile_direct(folder)
        return cleared

    def relocate_subscription_folder(
        self, subscription_id: UUID, new_folder: str
    ) -> SyncLedgerEntry | None:
        """Point ledger at the new save_folder and re-count files on disk."""
        key = subscription_ledger_key(subscription_id)
        entry = self._repository.get_by_key(key)
        if entry is None:
            return None
        entry.save_folder = new_folder
        return self._reconcile_entry(entry)

    def _reconcile_entry(self, entry: SyncLedgerEntry) -> SyncLedgerEntry:
        if self._data_path is None:
            return entry
        folder = entry.save_folder
        if entry.kind == LedgerKind.DIRECT:
            folder = self._direct_folder()
            entry.save_folder = folder
            entry.title = folder.split("/")[-1] or folder
        elif (
            entry.kind == LedgerKind.SUBSCRIPTION
            and entry.subscription_id is not None
            and self._subscription_folder_lookup is not None
        ):
            current = self._subscription_folder_lookup(entry.subscription_id)
            if current:
                folder = current
                entry.save_folder = current
        if not folder:
            return entry
        try:
            path = resolve_under_data(self._data_path, folder)
        except ValueError:
            return entry
        if self._track_catalog is not None:
            from yubal_api.services.library_hardlink import count_hardlinks_for_folder

            local, hard = count_hardlinks_for_folder(
                self._track_catalog,
                folder,
                download_root=self._data_path,
            )
            # Prefer catalog-local count; fall back to disk scan for orphans.
            disk = reconcile_folder_counts(path)
            synced = max(local, disk.synced_count)
            hardlink_count = hard
            real_download_count = max(0, synced - hardlink_count)
            counts_synced = synced
            counts_real = real_download_count
            counts_hard = hardlink_count
        else:
            counts = reconcile_folder_counts(path)
            counts_synced = counts.synced_count
            counts_real = counts.real_download_count
            counts_hard = counts.hardlink_count
        # Preserve total_count as max(playlist size, on-disk) when we had a larger total
        entry.synced_count = counts_synced
        entry.real_download_count = counts_real
        entry.hardlink_count = counts_hard
        if entry.total_count < counts_synced:
            entry.total_count = counts_synced
        # Direct: cloud = active catalog list (recoverable), local = on-disk.
        if entry.kind == LedgerKind.DIRECT:
            if self._track_catalog is not None:
                rows = self._track_catalog.list_for_save_folder(folder)
                active = sum(
                    1
                    for loc, _ in rows
                    if loc.membership_status != LocationMembershipStatus.OFFLINE
                )
                entry.total_count = max(active, counts_synced)
            else:
                entry.total_count = counts_synced
        return self._repository.upsert(entry)

    def mark_job_running(self, job: Job, *, save_folder: str | None = None) -> None:
        if job.subscription_id is not None:
            key = subscription_ledger_key(job.subscription_id)
            self._repository.mark_running(
                key,
                job_id=job.id,
                subscription_id=job.subscription_id,
                kind=LedgerKind.SUBSCRIPTION,
                save_folder=save_folder,
                url=job.url,
            )
        else:
            folder = save_folder or self._direct_folder()
            self._repository.mark_running(
                DIRECT_LEDGER_KEY,
                job_id=job.id,
                kind=LedgerKind.DIRECT,
                save_folder=folder,
                title=folder.split("/")[-1] or folder,
                url=job.url,
            )

    def record_job_finished(
        self,
        job: Job,
        *,
        success: bool,
        save_folder: str | None = None,
        content_info: ContentInfo | None = None,
        download_stats: PhaseStats | None = None,
        cloud_track_count: int | None = None,
    ) -> SyncLedgerEntry:
        info = content_info or job.content_info
        stats = download_stats if download_stats is not None else job.download_stats
        counts = compute_ledger_counts(stats)

        if job.subscription_id is not None:
            key = subscription_ledger_key(job.subscription_id)
            kind = LedgerKind.SUBSCRIPTION
            folder = save_folder or (info.title if info else "Unknown")
            title = info.title if info else folder
            existing = self._repository.get_by_key(key)
            # 云端 = last trusted remote playlist size, not download-batch size.
            if success and cloud_track_count is not None:
                counts["total_count"] = max(0, int(cloud_track_count))
            elif existing is not None:
                counts["total_count"] = existing.total_count
            else:
                counts["total_count"] = 0
        else:
            key = DIRECT_LEDGER_KEY
            kind = LedgerKind.DIRECT
            folder = save_folder or self._direct_folder()
            title = folder.split("/")[-1] or folder
            existing = self._repository.get_by_key(key)
            if existing is not None and success:
                counts = {
                    "total_count": existing.total_count + counts["total_count"],
                    "synced_count": existing.synced_count + counts["synced_count"],
                    "real_download_count": existing.real_download_count
                    + counts["real_download_count"],
                    "hardlink_count": existing.hardlink_count
                    + counts["hardlink_count"],
                    "failed_count": existing.failed_count + counts["failed_count"],
                    "skipped_ugc": existing.skipped_ugc + counts["skipped_ugc"],
                    "skipped_region": existing.skipped_region
                    + counts["skipped_region"],
                    "skipped_other": existing.skipped_other + counts["skipped_other"],
                }

        entry = SyncLedgerEntry(
            key=key,
            kind=kind,
            subscription_id=job.subscription_id,
            save_folder=folder,
            title=title,
            thumbnail_url=info.thumbnail_url if info else None,
            content_kind=info.kind.value if info else "playlist",
            url=job.url,
            last_job_id=job.id,
            last_job_status="completed" if success else "failed",
            last_synced_at=datetime.now(UTC) if success else None,
            **counts,
        )
        if not success:
            existing = self._repository.get_by_key(key)
            if existing and existing.last_synced_at:
                entry.last_synced_at = existing.last_synced_at
            # A failed job must not wipe a previously good cover.
            if existing and existing.thumbnail_url and not entry.thumbnail_url:
                entry.thumbnail_url = existing.thumbnail_url
            if existing and not success:
                if stats is None:
                    entry.total_count = existing.total_count
                    entry.synced_count = existing.synced_count
                    entry.real_download_count = existing.real_download_count
                    entry.hardlink_count = existing.hardlink_count
                    entry.failed_count = existing.failed_count
                    entry.skipped_ugc = existing.skipped_ugc
                    entry.skipped_region = existing.skipped_region
                    entry.skipped_other = existing.skipped_other
                else:
                    # Keep recorded cloud size even when a failed run has stats.
                    entry.total_count = existing.total_count

        saved = self._repository.upsert(entry)
        # Reconcile against disk so external deletes don't leave inflated counts
        return self._reconcile_entry(saved)

    def record_cloud_track_count(
        self,
        subscription_id: UUID,
        cloud_track_count: int,
        *,
        save_folder: str | None = None,
        title: str | None = None,
        url: str | None = None,
        thumbnail_url: str | None = None,
    ) -> SyncLedgerEntry | None:
        """Write remote playlist size from a trusted sync (or external seed)."""
        key = subscription_ledger_key(subscription_id)
        existing = self._repository.get_by_key(key)
        folder = save_folder or (existing.save_folder if existing else None) or ""
        entry = SyncLedgerEntry(
            key=key,
            kind=LedgerKind.SUBSCRIPTION,
            subscription_id=subscription_id,
            save_folder=folder,
            title=title or (existing.title if existing else folder) or "Unknown",
            thumbnail_url=thumbnail_url
            or (existing.thumbnail_url if existing else None),
            content_kind=existing.content_kind if existing else "playlist",
            url=url or (existing.url if existing else None),
            last_job_id=existing.last_job_id if existing else None,
            last_job_status=existing.last_job_status if existing else None,
            last_synced_at=existing.last_synced_at if existing else None,
            total_count=max(0, int(cloud_track_count)),
            synced_count=existing.synced_count if existing else 0,
            real_download_count=existing.real_download_count if existing else 0,
            hardlink_count=existing.hardlink_count if existing else 0,
            failed_count=existing.failed_count if existing else 0,
            skipped_ugc=existing.skipped_ugc if existing else 0,
            skipped_region=existing.skipped_region if existing else 0,
            skipped_other=existing.skipped_other if existing else 0,
        )
        saved = self._repository.upsert(entry)
        return self._reconcile_entry(saved)
