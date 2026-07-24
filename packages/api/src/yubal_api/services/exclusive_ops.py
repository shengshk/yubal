"""Freeze jobs/scheduler while doing exclusive on-disk folder moves."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from yubal_api.api.exceptions import MigrationFailedError

if TYPE_CHECKING:
    from yubal_api.services.job_executor import JobExecutor
    from yubal_api.services.operation_gate import OperationGate

logger = logging.getLogger(__name__)

T = TypeVar("T")


def run_exclusive(
    *,
    gate: OperationGate | None,
    job_executor: JobExecutor | None,
    reason: str,
    fn: Callable[[], T],
) -> T:
    """Cancel active jobs, hold the gate, run ``fn``, then unlock."""
    if gate is None:
        return fn()

    if not gate.acquire(reason):
        raise MigrationFailedError(
            "Another maintenance operation is already running"
        )

    try:
        if job_executor is not None:
            cancelled = job_executor.cancel_all_jobs()
            logger.info("%s: cancelled %s job token(s)", reason, cancelled)
        return fn()
    finally:
        gate.release()
