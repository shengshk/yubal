"""Library mount health: same-FS gate, sentinels, empty-scan guard."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from yubal.utils.library import (
    DOWNLOAD_MOUNT_SENTINEL,
    DOWNLOAD_ROOT,
    EXTERNAL_MOUNT_SENTINEL,
    EXTERNAL_ROOT,
    STORAGE_DOWNLOAD,
    STORAGE_EXTERNAL,
    ensure_external_layout,
    same_filesystem,
)

logger = logging.getLogger(__name__)

STATUS_HEALTHY = "healthy"
STATUS_FS_MISMATCH = "fs_mismatch"
STATUS_MOUNT_SUSPECT = "mount_suspect"

EMPTY_GUARD_MIN_INDEXED = 20
EMPTY_GUARD_RATIO = 0.5


@dataclass(frozen=True)
class LibraryHealth:
    status: str
    reason: str | None
    same_filesystem: bool
    download_sentinel_ok: bool
    external_sentinel_ok: bool
    last_good_raw_count: int
    last_check_at: datetime | None

    @property
    def ok(self) -> bool:
        return self.status == STATUS_HEALTHY


class LibraryHealthService:
    """Process-wide library health used to freeze jobs when mounts are unsafe."""

    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path
        self._lock = threading.RLock()
        self._last_good_raw_count = 0
        self._last_check_at: datetime | None = None
        self._cached: LibraryHealth | None = None
        self._require_external_fn: Callable[[], bool] | None = None
        self._load_persisted()

    def bind_require_external(self, fn: Callable[[], bool]) -> None:
        """When False, only Download mount is required (external library off)."""
        self._require_external_fn = fn

    def _require_external(self) -> bool:
        if self._require_external_fn is None:
            return True
        try:
            return bool(self._require_external_fn())
        except Exception:
            return True

    def _load_persisted(self) -> None:
        if not self._state_path.is_file():
            return
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._last_good_raw_count = int(raw.get("last_good_raw_count") or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as e:
            logger.warning("Failed to read library health state: %s", e)

    def _persist(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_good_raw_count": self._last_good_raw_count,
            "last_check_at": (
                self._last_check_at.isoformat() if self._last_check_at else None
            ),
        }
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self._state_path)

    def record_good_raw_scan(self, count: int) -> None:
        with self._lock:
            self._last_good_raw_count = max(0, int(count))
            self._persist()

    @property
    def last_good_raw_count(self) -> int:
        with self._lock:
            return self._last_good_raw_count

    def allow_index_deletes(self, walked_count: int) -> bool:
        """False when a sudden drop suggests an empty/failed mount."""
        with self._lock:
            n = int(walked_count)
            N = self._last_good_raw_count
            if N < EMPTY_GUARD_MIN_INDEXED:
                return True
            if n == 0:
                return False
            if n < N * EMPTY_GUARD_RATIO:
                return False
            return True

    def check(self, *, force: bool = True) -> LibraryHealth:
        """Probe mounts and update cached health."""
        _ = force
        require_external = self._require_external()
        if require_external:
            ensure_external_layout()
        now = datetime.now(UTC)

        dl_ok = DOWNLOAD_ROOT.is_dir()
        ext_ok = EXTERNAL_ROOT.is_dir()
        dl_sent_ok = (
            DOWNLOAD_MOUNT_SENTINEL.is_file()
            and os_access_readable(DOWNLOAD_MOUNT_SENTINEL)
        )
        ext_sent_ok = (
            EXTERNAL_MOUNT_SENTINEL.is_file()
            and os_access_readable(EXTERNAL_MOUNT_SENTINEL)
        )
        same_fs = False
        if dl_ok and ext_ok:
            same_fs = same_filesystem(DOWNLOAD_ROOT, EXTERNAL_ROOT)

        status = STATUS_HEALTHY
        reason: str | None = None
        if not dl_ok:
            status = STATUS_MOUNT_SUSPECT
            reason = (
                "Download mount is missing. "
                f"Expected {DOWNLOAD_ROOT} under /data "
                "(override with YUBAL_LIBRARY_ROOT if needed)."
            )
        elif not dl_sent_ok:
            status = STATUS_MOUNT_SUSPECT
            reason = (
                f"Download mount sentinel missing or unreadable "
                f"({DOWNLOAD_ROOT}/.yubal-mount). "
                "Refusing tasks to avoid wiping the index on an empty mount."
            )
        elif require_external and not ext_ok:
            status = STATUS_MOUNT_SUSPECT
            reason = (
                "External mount is missing. "
                f"Expected {EXTERNAL_ROOT}, "
                "or disable External library in settings."
            )
        elif require_external and not ext_sent_ok:
            status = STATUS_MOUNT_SUSPECT
            reason = (
                "Mount sentinel missing or unreadable "
                f"({MOUNT_SENTINEL_HINT}). "
                "Refusing tasks to avoid wiping the index on an empty mount."
            )
        elif require_external and not same_fs:
            status = STATUS_FS_MISMATCH
            reason = (
                "Download and External cannot hardlink to each other "
                "(often caused by separate Docker bind mounts). "
                "Use one library root: ./data:/data. "
                "All library tasks are stopped."
            )

        health = LibraryHealth(
            status=status,
            reason=reason,
            same_filesystem=same_fs if require_external else True,
            download_sentinel_ok=dl_sent_ok,
            external_sentinel_ok=ext_sent_ok if require_external else True,
            last_good_raw_count=self._last_good_raw_count,
            last_check_at=now,
        )
        with self._lock:
            prev = self._cached
            self._last_check_at = now
            self._cached = health
            self._persist()
        if not health.ok:
            # Log once per distinct unhealthy state — scheduled checks must
            # not spam the UI log ring every tick.
            changed = (
                prev is None
                or prev.status != health.status
                or prev.reason != health.reason
            )
            if changed:
                logger.error(
                    "Library unhealthy (%s): %s", health.status, health.reason
                )
        elif prev is not None and not prev.ok:
            logger.info("Library health restored (%s)", health.status)
        return health

    def current(self) -> LibraryHealth:
        with self._lock:
            if self._cached is not None:
                return self._cached
        return self.check()

    def ensure_healthy(self) -> None:
        from yubal_api.api.exceptions import LibraryUnhealthyError

        health = self.check()
        if not health.ok:
            raise LibraryUnhealthyError(
                health.reason or "Library mounts are unhealthy",
                status=health.status,
            )


MOUNT_SENTINEL_HINT = (
    f"{DOWNLOAD_ROOT}/.yubal-mount and {EXTERNAL_ROOT}/.yubal-mount"
)


def os_access_readable(path: Path) -> bool:
    try:
        path.read_bytes()
        return True
    except OSError:
        return False


# Re-export storage keys for callers
__all__ = [
    "EMPTY_GUARD_MIN_INDEXED",
    "EMPTY_GUARD_RATIO",
    "LibraryHealth",
    "LibraryHealthService",
    "STATUS_FS_MISMATCH",
    "STATUS_HEALTHY",
    "STATUS_MOUNT_SUSPECT",
    "STORAGE_DOWNLOAD",
    "STORAGE_EXTERNAL",
]
