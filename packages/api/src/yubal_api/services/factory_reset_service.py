"""Unified, preview-first factory reset operations for Web and Telegram."""

from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Engine
from sqlalchemy import inspect as sa_inspect
from yubal.utils.library import (
    AUDIO_SUFFIXES,
    EXTERNAL_RAW_DIR,
    MOUNT_SENTINEL_NAME,
    STORAGE_DOWNLOAD,
    STORAGE_EXTERNAL,
    STORAGE_WANTED,
)

from yubal_api.db.external_library import META_VERIFIED
from yubal_api.db.external_library_repository import ExternalLibraryRepository
from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.db.wanted import WantedTrack
from yubal_api.db.wanted_repository import WantedRepository
from yubal_api.services.exclusive_ops import run_exclusive

if TYPE_CHECKING:
    from yubal_api.services.auth import AuthManager
    from yubal_api.services.job_executor import JobExecutor
    from yubal_api.services.operation_gate import OperationGate
    from yubal_api.services.preferences import PreferencesStore

logger = logging.getLogger(__name__)

_TICKET_TTL_SECONDS = 300
_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VERIFIED_WANTED_SOURCES = frozenset({"musicbrainz", "qq", "discogs", "lastfm"})


class FactoryResetMode(StrEnum):
    PREFERENCES = "preferences"
    INVALID = "invalid"
    FULL = "full"


@dataclass(frozen=True)
class FactoryResetPreview:
    mode: FactoryResetMode
    token: str
    expires_in_seconds: int
    list_entries: int
    files: int
    paths: int
    bytes: int
    backups: int
    clears_account: bool
    clears_external_originals: bool


@dataclass(frozen=True)
class FactoryResetResult:
    mode: FactoryResetMode
    list_entries: int
    files: int
    paths: int
    bytes: int
    backups: int
    requires_setup: bool


@dataclass(frozen=True)
class _FileTarget:
    path: Path
    inode: tuple[int, int]
    size: int


@dataclass(frozen=True)
class _ResetSnapshot:
    list_entries: int = 0
    files: int = 0
    paths: int = 0
    bytes: int = 0
    backups: int = 0


class FactoryResetService:
    """One reset core shared by all product entry points."""

    def __init__(
        self,
        *,
        engine: Engine,
        preferences: PreferencesStore,
        auth: AuthManager,
        download_root: Path,
        external_root: Path,
        wanted_root: Path,
        cache_root: Path,
        config_root: Path,
        db_path: Path,
        cookies_path: Path,
        operation_gate: OperationGate | None = None,
        job_executor: JobExecutor | None = None,
    ) -> None:
        self._engine = engine
        self._preferences = preferences
        self._auth = auth
        self._download_root = download_root
        self._external_root = external_root
        self._wanted_root = wanted_root
        self._cache_root = cache_root
        self._config_root = config_root
        self._db_path = db_path
        self._cookies_path = cookies_path
        self._operation_gate = operation_gate
        self._job_executor = job_executor
        self._catalog = TrackCatalogRepository(engine)
        self._external = ExternalLibraryRepository(engine)
        self._wanted = WantedRepository(engine)
        self._tickets: dict[str, tuple[FactoryResetMode, float]] = {}
        self._ticket_lock = threading.Lock()

    def bind_job_executor(self, job_executor: JobExecutor) -> None:
        """Attach the executor after service graph construction."""
        self._job_executor = job_executor

    def preview(self, mode: FactoryResetMode) -> FactoryResetPreview:
        snapshot = self._snapshot(mode)
        token = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._ticket_lock:
            self._tickets = {
                key: value for key, value in self._tickets.items() if value[1] > now
            }
            self._tickets[token] = (mode, now + _TICKET_TTL_SECONDS)
        return FactoryResetPreview(
            mode=mode,
            token=token,
            expires_in_seconds=_TICKET_TTL_SECONDS,
            list_entries=snapshot.list_entries,
            files=snapshot.files,
            paths=snapshot.paths,
            bytes=snapshot.bytes,
            backups=snapshot.backups,
            clears_account=mode is FactoryResetMode.FULL,
            clears_external_originals=mode is FactoryResetMode.FULL,
        )

    def execute(
        self,
        mode: FactoryResetMode,
        token: str,
        *,
        password: str = "",
        authorized: bool = False,
    ) -> FactoryResetResult:
        self._validate_ticket(mode, token)
        if (
            mode is FactoryResetMode.FULL
            and not authorized
            and not self._auth.verify_password(password)
        ):
            raise PermissionError("invalid password")
        self._consume_ticket(mode, token)
        snapshot = self._snapshot(mode)

        def action() -> None:
            if mode is FactoryResetMode.PREFERENCES:
                self._preferences.reset_preferences()
            elif mode is FactoryResetMode.INVALID:
                self._clean_invalid_managed_data()
                self._preferences.reset_preferences()
            else:
                self._full_reset()

        run_exclusive(
            gate=self._operation_gate,
            job_executor=self._job_executor,
            reason=f"factory-reset:{mode.value}",
            fn=action,
        )
        logger.warning(
            "Factory reset completed: mode=%s entries=%d files=%d paths=%d bytes=%d",
            mode.value,
            snapshot.list_entries,
            snapshot.files,
            snapshot.paths,
            snapshot.bytes,
        )
        return FactoryResetResult(
            mode=mode,
            list_entries=snapshot.list_entries,
            files=snapshot.files,
            paths=snapshot.paths,
            bytes=snapshot.bytes,
            backups=snapshot.backups,
            requires_setup=mode is FactoryResetMode.FULL and self._auth.enabled,
        )

    def _validate_ticket(self, mode: FactoryResetMode, token: str) -> None:
        with self._ticket_lock:
            ticket = self._tickets.get(token)
        if ticket is None or ticket[0] is not mode or ticket[1] <= time.monotonic():
            raise ValueError("confirmation expired")

    def _consume_ticket(self, mode: FactoryResetMode, token: str) -> None:
        with self._ticket_lock:
            ticket = self._tickets.pop(token, None)
        if ticket is None or ticket[0] is not mode or ticket[1] <= time.monotonic():
            raise ValueError("confirmation expired")

    def _snapshot(self, mode: FactoryResetMode) -> _ResetSnapshot:
        if mode is FactoryResetMode.PREFERENCES:
            return _ResetSnapshot()
        if mode is FactoryResetMode.INVALID:
            targets, invalid_wanted = self._invalid_targets()
            return self._file_snapshot(
                targets,
                list_entries=len(invalid_wanted),
            )

        targets = self._full_targets()
        backups = self._backup_count()
        return self._file_snapshot(
            targets,
            list_entries=self._database_entry_count(),
            backups=backups,
        )

    @staticmethod
    def _file_snapshot(
        targets: list[_FileTarget],
        *,
        list_entries: int = 0,
        backups: int = 0,
    ) -> _ResetSnapshot:
        unique: dict[tuple[int, int], int] = {}
        for target in targets:
            unique.setdefault(target.inode, target.size)
        return _ResetSnapshot(
            list_entries=list_entries,
            files=len(unique),
            paths=len(targets),
            bytes=sum(unique.values()),
            backups=backups,
        )

    @staticmethod
    def _target(path: Path) -> _FileTarget | None:
        try:
            if not path.is_file():
                return None
            stat = path.stat()
            return _FileTarget(
                path=path,
                inode=(stat.st_dev, stat.st_ino),
                size=stat.st_size,
            )
        except OSError:
            return None

    @staticmethod
    def _iter_files(root: Path) -> list[Path]:
        if not root.is_dir():
            return []
        files: list[Path] = []
        for current, dirs, names in os.walk(root, followlinks=False):
            current_path = Path(current)
            dirs[:] = [name for name in dirs if not (current_path / name).is_symlink()]
            for name in names:
                path = current_path / name
                if path.is_symlink() or path.is_file():
                    files.append(path)
        return files

    def _full_targets(self) -> list[_FileTarget]:
        targets: list[_FileTarget] = []
        for root in (
            self._download_root,
            self._external_root,
            self._wanted_root,
            self._cache_root,
            self._config_root / "state",
        ):
            for path in self._iter_files(root):
                if path.name == MOUNT_SENTINEL_NAME:
                    continue
                target = self._target(path)
                if target is not None:
                    targets.append(target)
        return targets

    def _trusted_inodes(self) -> set[tuple[int, int]]:
        roots = {
            STORAGE_DOWNLOAD: self._download_root,
            STORAGE_EXTERNAL: self._external_root,
            STORAGE_WANTED: self._wanted_root,
        }
        trusted: set[tuple[int, int]] = set()
        for rows in self._catalog.list_all_by_video_id().values():
            for location, _record in rows:
                root = roots.get(location.storage_root)
                if root is None:
                    continue
                target = self._target(
                    root / location.save_folder / location.relative_path
                )
                if target is not None:
                    trusted.add(target.inode)

        raw_root = self._external_root / EXTERNAL_RAW_DIR
        for playlist in self._external.list_playlists():
            for row in self._external.list_for_dir(playlist.dir_name):
                if not row.video_id and row.meta_status != META_VERIFIED:
                    continue
                target = self._target(raw_root / row.rel_path)
                if target is not None:
                    trusted.add(target.inode)

        for row in self._wanted.list_all():
            if not row.relative_path or not self._wanted_is_trusted(row):
                continue
            target = self._target(self._wanted_root / row.relative_path)
            if target is not None:
                trusted.add(target.inode)
        return trusted

    @staticmethod
    def _wanted_is_trusted(row: WantedTrack) -> bool:
        return bool(
            row.video_id
            or (
                row.source_id and row.source.strip().lower() in _VERIFIED_WANTED_SOURCES
            )
        )

    def _invalid_targets(
        self,
    ) -> tuple[list[_FileTarget], list[WantedTrack]]:
        trusted = self._trusted_inodes()
        targets: list[_FileTarget] = []
        for root in (self._download_root, self._wanted_root):
            for path in self._iter_files(root):
                if path.suffix.lower() not in AUDIO_SUFFIXES:
                    continue
                target = self._target(path)
                if target is not None and target.inode not in trusted:
                    targets.append(target)
        invalid_wanted = [
            row for row in self._wanted.list_all() if not self._wanted_is_trusted(row)
        ]
        return targets, invalid_wanted

    def _clean_invalid_managed_data(self) -> None:
        targets, invalid_wanted = self._invalid_targets()
        for target in targets:
            self._unlink(target.path)
            self._unlink(target.path.with_suffix(".lrc"))
        for row in invalid_wanted:
            self._wanted.delete(row.id)
        self._remove_empty_dirs((self._download_root, self._wanted_root))

    def _full_reset(self) -> None:
        self._clear_database()
        for root in (
            self._download_root,
            self._external_root,
            self._wanted_root,
            self._cache_root,
        ):
            self._clear_root(root)
        self._clear_known_config_state()
        self._preferences.reset()
        self._auth.reset_for_setup()

    def _clear_database(self) -> None:
        tables = [
            table
            for table in sa_inspect(self._engine).get_table_names()
            if table != "alembic_version" and _TABLE_NAME.fullmatch(table)
        ]
        raw = self._engine.raw_connection()
        try:
            cursor = raw.cursor()
            cursor.execute("PRAGMA foreign_keys=OFF")
            for table in tables:
                cursor.execute(f'DELETE FROM "{table}"')
            if "sqlite_sequence" in tables:
                cursor.execute("DELETE FROM sqlite_sequence")
            raw.commit()
            cursor.execute("PRAGMA foreign_keys=ON")
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()

    def _database_entry_count(self) -> int:
        total = 0
        table_names = [
            table
            for table in sa_inspect(self._engine).get_table_names()
            if table != "alembic_version" and _TABLE_NAME.fullmatch(table)
        ]
        with self._engine.connect() as connection:
            for table in table_names:
                if table == "sqlite_sequence":
                    continue
                total += int(
                    connection.exec_driver_sql(
                        f'SELECT COUNT(*) FROM "{table}"'
                    ).scalar_one()
                )
        return total

    def _backup_count(self) -> int:
        backup_dir = self._db_path.parent / "backups"
        if not backup_dir.is_dir():
            return 0
        return sum(1 for path in backup_dir.iterdir() if path.is_file())

    def _clear_known_config_state(self) -> None:
        known = (
            self._cookies_path,
            self._config_root / "search_results.json",
            self._config_root / "library_health.json",
            self._config_root / "telegram_file_ids.json",
            self._config_root / "telegram_quota.json",
            self._db_path.parent / "extraction_cache.db",
        )
        for path in known:
            self._unlink(path)
        state_dir = self._config_root / "state"
        if state_dir.is_dir():
            shutil.rmtree(state_dir)
        backup_dir = self._db_path.parent / "backups"
        if backup_dir.is_dir():
            shutil.rmtree(backup_dir)

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Factory reset could not remove %s: %s", path, exc)
            raise

    def _clear_root(self, root: Path) -> None:
        resolved = root.resolve()
        if resolved == Path("/") or len(resolved.parts) < 3:
            raise ValueError(f"unsafe factory reset root: {root}")
        if not root.is_dir():
            return
        for child in root.iterdir():
            if child.name == MOUNT_SENTINEL_NAME:
                continue
            if child.is_symlink() or child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                shutil.rmtree(child)

    @staticmethod
    def _remove_empty_dirs(roots: tuple[Path, ...]) -> None:
        for root in roots:
            if not root.is_dir():
                continue
            directories = [
                path
                for path in root.rglob("*")
                if path.is_dir() and not path.is_symlink()
            ]
            for directory in sorted(
                directories,
                key=lambda item: len(item.parts),
                reverse=True,
            ):
                try:
                    directory.rmdir()
                except OSError:
                    continue
