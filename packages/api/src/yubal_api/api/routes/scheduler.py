"""Scheduler status endpoint."""

from fastapi import APIRouter

from yubal_api.api.deps import SchedulerDep, SettingsDep, SubscriptionServiceDep
from yubal_api.schemas.scheduler import SchedulerStatus, SubscriptionCounts

router = APIRouter(prefix="/scheduler", tags=["scheduler"])


@router.get("", response_model=SchedulerStatus)
def get_scheduler_status(
    service: SubscriptionServiceDep,
    scheduler: SchedulerDep,
    settings: SettingsDep,
) -> SchedulerStatus:
    """Get scheduler status (read-only)."""
    scheduler.refresh_next_run()
    next_id = scheduler.next_run_subscription_id
    return SchedulerStatus(
        running=scheduler.is_running,
        enabled=scheduler.enabled,
        cron_expression=scheduler.cron_expression,
        timezone=settings.tz,
        next_run_at=scheduler.next_run_at,
        next_run_subscription_id=str(next_id) if next_id is not None else None,
        next_run_subscription_name=scheduler.next_run_subscription_name,
        next_run_target_kind=scheduler.next_run_target_kind,
        next_run_target_id=scheduler.next_run_target_id,
        next_run_target_name=scheduler.next_run_target_name,
        subscription_counts=SubscriptionCounts(
            total=service.count(),
            enabled=service.count(enabled=True),
        ),
    )
