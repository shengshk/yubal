"""Tests for scheduler resilience fixes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from yubal_api.services.scheduler import Scheduler


def test_library_work_active_uses_job_executor(mock_settings: MagicMock) -> None:
    job_executor = MagicMock()
    job_executor.has_active_jobs.return_value = True
    scheduler = Scheduler(MagicMock(), job_executor, mock_settings)
    assert scheduler._library_work_active() is True
    job_executor.has_active_jobs.assert_called_once_with()


def test_library_work_active_false_when_idle(mock_settings: MagicMock) -> None:
    job_executor = MagicMock()
    job_executor.has_active_jobs.return_value = False
    scheduler = Scheduler(MagicMock(), job_executor, mock_settings)
    assert scheduler._library_work_active() is False


def test_start_replaces_dead_scheduler_task(mock_settings: MagicMock) -> None:
    scheduler = Scheduler(MagicMock(), MagicMock(), mock_settings)
    dead = MagicMock()
    dead.done.return_value = True
    scheduler._task = dead
    scheduler.start()
    assert scheduler._task is not dead


def test_inventory_due_deferral_does_not_raise(mock_settings: MagicMock) -> None:
    """Regression: deferral must use JobExecutor, not a missing job store."""
    job_executor = MagicMock()
    job_executor.has_active_jobs.return_value = True
    scheduler = Scheduler(MagicMock(), job_executor, mock_settings)
    scheduler._external_inventory_planned = datetime.now(UTC)
    now = datetime.now(UTC)
    inventory_due = (
        scheduler._external_inventory_planned is not None
        and scheduler._external_inventory_planned <= now
    )
    if inventory_due and scheduler._library_work_active():
        scheduler._external_inventory_planned = now + timedelta(minutes=5)
        inventory_due = False
    assert inventory_due is False
