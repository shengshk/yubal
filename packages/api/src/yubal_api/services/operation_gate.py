"""Global gate that freezes jobs/scheduler during library migration."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from yubal_api.services.library_health_service import LibraryHealthService


@dataclass(frozen=True)
class GateState:
    locked: bool
    reason: str | None = None


class OperationGate:
    """Process-wide lock for exclusive maintenance (layout migration)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._locked = False
        self._reason: str | None = None
        self._library_health: LibraryHealthService | None = None

    def bind_health(self, library_health: LibraryHealthService) -> None:
        """Attach the library health service so ``ensure_allowed`` also gates on it."""
        self._library_health = library_health

    @property
    def is_locked(self) -> bool:
        with self._lock:
            return self._locked

    def state(self) -> GateState:
        with self._lock:
            return GateState(locked=self._locked, reason=self._reason)

    def acquire(self, reason: str) -> bool:
        """Try to acquire exclusive lock. Returns False if already locked."""
        with self._lock:
            if self._locked:
                return False
            self._locked = True
            self._reason = reason
            return True

    def release(self) -> None:
        with self._lock:
            self._locked = False
            self._reason = None

    def ensure_allowed(self) -> None:
        """Raise if maintenance is in progress or the library is unhealthy."""
        from yubal_api.api.exceptions import MigrationInProgressError

        with self._lock:
            if self._locked:
                raise MigrationInProgressError(self._reason or "maintenance")
        if self._library_health is not None:
            self._library_health.ensure_healthy()
