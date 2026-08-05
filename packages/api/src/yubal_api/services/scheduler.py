"""Background scheduler for periodic subscription syncing.

Each enabled subscription fires near the global cron tick with a fresh random
offset every cycle:

  fire_at = next_cron_tick + random(-N, +N)

``sync_jitter_seconds`` (N) is only the max absolute offset (default 600).
Applied |offset| is also capped so it never exceeds the cron interval.
The chosen offset for a cycle is kept until that fire runs (stable countdown),
then re-rolled. Manual sync bypasses jitter.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from croniter import croniter

from yubal_api.api.exceptions import (
    InsufficientDiskSpaceError,
    SubscriptionNotFoundError,
)
from yubal_api.db.external_library import EXTERNAL_ACCESS_PENDING
from yubal_api.db.subscription import Subscription
from yubal_api.domain.enums import JobSource
from yubal_api.services.external_library_service import (
    ExternalLibraryService,
    ScanResult,
)
from yubal_api.services.job_executor import JobExecutor
from yubal_api.services.library_dedup_service import LibraryDedupService
from yubal_api.services.library_enrichment_service import LibraryEnrichmentService
from yubal_api.services.library_health_service import LibraryHealthService
from yubal_api.services.operation_gate import OperationGate
from yubal_api.services.preferences import PreferencesStore
from yubal_api.services.subscription_membership_service import (
    SubscriptionMembershipService,
)
from yubal_api.services.subscription_service import SubscriptionService
from yubal_api.services.sync_ledger_service import SyncLedgerService
from yubal_api.services.sync_pipeline_service import SyncPipelineService
from yubal_api.services.wanted_service import WantedService
from yubal_api.settings import Settings

logger = logging.getLogger(__name__)


class Scheduler:
    """Background scheduler that syncs enabled subscriptions periodically."""

    def __init__(
        self,
        subscription_service: SubscriptionService,
        job_executor: JobExecutor,
        settings: Settings,
        preferences_store: PreferencesStore | None = None,
        operation_gate: OperationGate | None = None,
        sync_ledger_service: SyncLedgerService | None = None,
        library_health: LibraryHealthService | None = None,
        external_library_service: ExternalLibraryService | None = None,
        membership_service: SubscriptionMembershipService | None = None,
        library_enrichment_service: LibraryEnrichmentService | None = None,
        library_dedup_service: LibraryDedupService | None = None,
        wanted_service: WantedService | None = None,
        sync_pipeline_service: SyncPipelineService | None = None,
    ) -> None:
        """Initialize scheduler."""
        self._subscription_service = subscription_service
        self._job_executor = job_executor
        self._settings = settings
        self._preferences_store = preferences_store
        self._operation_gate = operation_gate
        self._sync_ledger_service = sync_ledger_service
        self._library_health = library_health
        self._external_library_service = external_library_service
        self._membership_service = membership_service
        self._library_enrichment_service = library_enrichment_service
        self._library_dedup_service = library_dedup_service
        self._wanted_service = wanted_service
        self._pipeline = sync_pipeline_service or SyncPipelineService(
            library_health=library_health,
            external_library_service=external_library_service,
            library_enrichment_service=library_enrichment_service,
            library_dedup_service=library_dedup_service,
            preferences_store=preferences_store,
            sync_ledger_service=sync_ledger_service,
            wanted_service=wanted_service,
        )
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        # Per-sub fire times for the current cycle only (re-rolled after each fire)
        self._planned: dict[UUID, datetime] = {}
        self._external_planned: dict[str, datetime] = {}
        self._direct_planned: datetime | None = None
        self._wanted_planned: datetime | None = None
        self._external_inventory_planned: datetime | None = None
        self._inventory_state_path = (
            self._settings.config / "state" / "external_inventory_maintenance.json"
        )
        self._last_inventory_success_at = self._load_inventory_success()
        self._next_run_at: datetime | None = None
        self._next_run_subscription_id: UUID | None = None
        self._next_run_subscription_name: str | None = None
        self._next_run_target_kind: (
            Literal[
                "subscription", "external", "direct", "wanted", "external_inventory"
            ]
            | None
        ) = None
        self._next_run_target_id: str | None = None
        self._next_run_target_name: str | None = None
        self._tracked_cron: str | None = None
        self._last_offline_cleanup_at: datetime | None = None
        self._last_manual_sync_steps: list[dict[str, str | int | None]] = []
        # Browser/API initiated library passes must never make the request wait
        # for scanning, matching and asset enrichment.  Keep their tasks here so
        # they survive beyond the response and repeated presses are coalesced.
        self._manual_sync_task: asyncio.Task[None] | None = None
        self._external_sync_tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._task is not None and not self._task.done()

    @property
    def enabled(self) -> bool:
        """Check if scheduler is enabled (prefs override env; gate forces off)."""
        if self._operation_gate is not None and self._operation_gate.is_locked:
            return False
        if self._preferences_store is not None:
            return self._preferences_store.snapshot().scheduler_enabled
        return self._settings.scheduler_enabled

    @property
    def cron_expression(self) -> str:
        """Get cron expression (prefs override env)."""
        if self._preferences_store is not None:
            return self._preferences_store.snapshot().scheduler_cron
        return self._settings.scheduler_cron

    @property
    def next_run_at(self) -> datetime | None:
        """Soonest planned fire among enabled subscriptions."""
        return self._next_run_at

    @property
    def next_run_subscription_id(self) -> UUID | None:
        """Subscription id for the soonest planned fire."""
        return self._next_run_subscription_id

    @property
    def next_run_subscription_name(self) -> str | None:
        """Subscription name for the soonest planned fire."""
        return self._next_run_subscription_name

    @property
    def next_run_target_kind(
        self,
    ) -> (
        Literal["subscription", "external", "direct", "wanted", "external_inventory"]
        | None
    ):
        """Kind of the earliest independent scheduled task."""
        return self._next_run_target_kind

    @property
    def next_run_target_id(self) -> str | None:
        """Stable id of the earliest independent scheduled task."""
        return self._next_run_target_id

    @property
    def next_run_target_name(self) -> str | None:
        """Display name of the earliest independent scheduled task."""
        return self._next_run_target_name

    @property
    def last_manual_sync_steps(self) -> list[dict[str, str | int | None]]:
        """Snapshot of the most recently triggered manual Sync All pipeline."""
        return [dict(step) for step in self._last_manual_sync_steps]

    def invalidate_plan(self, subscription_id: UUID | None = None) -> None:
        """Drop a cycle plan so the next loop re-rolls jitter.

        Pass ``None`` to clear all plans (e.g. after cron changes).
        """
        if subscription_id is None:
            self._planned.clear()
            self._external_planned.clear()
            self._direct_planned = None
            self._wanted_planned = None
            self._external_inventory_planned = None
            return
        self._planned.pop(subscription_id, None)

    def invalidate_external_plan(self, dir_name: str | None = None) -> None:
        """Drop one External playlist fire after its scheduling policy changes."""
        if dir_name is None:
            self._external_planned.clear()
            return
        self._external_planned.pop(dir_name, None)

    def invalidate_direct_plan(self) -> None:
        """Drop the Direct recover fire so the next loop re-rolls jitter."""
        self._direct_planned = None

    def invalidate_wanted_plan(self) -> None:
        """Drop the Wanted fire after its scheduling policy changes."""
        self._wanted_planned = None

    def refresh_next_run(self) -> None:
        """Recompute soonest fire for status API without waiting for the loop."""
        if not self.enabled:
            self._refresh_status([])
            return
        self._refresh_status(self._subscription_service.list(enabled=True))

    def _get_next_cron_after(self, after: datetime) -> datetime:
        """Next cron tick strictly after ``after``, returned in UTC."""
        tz = self._settings.timezone
        local_after = after.astimezone(tz)
        cron = croniter(self.cron_expression, local_after)
        next_time = cron.get_next(datetime)
        if next_time.tzinfo is None:
            next_time = next_time.replace(tzinfo=tz)
        return next_time.astimezone(UTC)

    def _get_next_run_time(self) -> datetime:
        """Next global cron tick from now (UTC). Kept for tests/compat."""
        return self._get_next_cron_after(datetime.now(UTC))

    def _cron_interval_seconds(self) -> int:
        """Seconds between two consecutive cron ticks (best-effort)."""
        tz = self._settings.timezone
        base = datetime.now(tz)
        cron = croniter(self.cron_expression, base)
        t1 = cron.get_next(datetime)
        t2 = cron.get_next(datetime)
        if t1.tzinfo is None:
            t1 = t1.replace(tzinfo=tz)
        if t2.tzinfo is None:
            t2 = t2.replace(tzinfo=tz)
        return max(0, int((t2 - t1).total_seconds()))

    def _effective_jitter_max(self, configured: int) -> int:
        """Configured max |offset|, capped by cron interval and 0..600."""
        configured = max(0, min(600, int(configured)))
        interval = self._cron_interval_seconds()
        if interval <= 0:
            return configured
        # Must not exceed the cron gap (keep 1s headroom when possible)
        cap = interval if interval <= 1 else interval - 1
        return min(configured, cap)

    def _roll_fire_at(self, jitter_seconds: int, base: datetime) -> datetime:
        """Compute ``base + random(-N, N)`` with a fresh N each cycle."""
        n = self._effective_jitter_max(jitter_seconds)
        now = datetime.now(UTC)
        candidate_base = base
        for _ in range(8):
            offset = random.randint(-n, n) if n > 0 else 0
            fire_at = candidate_base + timedelta(seconds=offset)
            if fire_at > now:
                return fire_at
            candidate_base = self._get_next_cron_after(candidate_base)
        return now + timedelta(seconds=1)

    def _ensure_plan(self, subscription: Subscription) -> datetime:
        """Return this cycle's fire time, rolling a new offset if missing/past."""
        existing = self._planned.get(subscription.id)
        if existing is not None and existing > datetime.now(UTC):
            return existing
        base = self._get_next_cron_after(datetime.now(UTC))
        fire_at = self._roll_fire_at(subscription.sync_jitter_seconds or 0, base)
        self._planned[subscription.id] = fire_at
        return fire_at

    def _scheduled_external_playlists(self) -> list[object]:
        """External playlists that have their own periodic task."""
        service = self._external_library_service
        if service is None or not self._external_sync_enabled():
            return []
        return [
            playlist
            for playlist in service.list_playlists()
            if playlist.enabled and playlist.access_mode != EXTERNAL_ACCESS_PENDING
        ]

    def _external_sync_enabled(self) -> bool:
        if self._external_library_service is None:
            return False
        if self._preferences_store is None:
            return True
        return bool(self._preferences_store.effective().external_library_enabled)

    def _external_inventory_schedule_enabled(self) -> bool:
        if self._external_library_service is None:
            return False
        if self._preferences_store is None:
            return False
        prefs = self._preferences_store.effective()
        return bool(
            prefs.external_library_enabled and prefs.external_inventory_schedule_enabled
        )

    def _load_inventory_success(self) -> datetime | None:
        try:
            raw = json.loads(self._inventory_state_path.read_text(encoding="utf-8"))
            value = datetime.fromisoformat(str(raw.get("last_success_at", "")))
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return value.astimezone(UTC)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def record_external_inventory_success(self) -> None:
        """Persist completion so restarts do not repeat today's full inventory."""
        completed_at = datetime.now(UTC)
        self._last_inventory_success_at = completed_at
        try:
            self._inventory_state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._inventory_state_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(
                    {"last_success_at": completed_at.isoformat()},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            tmp.replace(self._inventory_state_path)
        except OSError:
            logger.exception("Failed to persist external inventory schedule state")

    def reconcile_external_inventory(
        self,
        *,
        dir_name: str | None = None,
    ) -> ScanResult | None:
        """Run one manual/daily lightweight inventory without overlapping sync."""
        if self._operation_gate is not None:
            self._operation_gate.ensure_allowed()
        result = self._pipeline.reconcile_external_inventory(dir_name=dir_name)
        if result is not None and result.errors == 0 and dir_name is None:
            self.record_external_inventory_success()
        return result

    def _inventory_completed_today(self) -> bool:
        if self._last_inventory_success_at is None:
            return False
        return (
            self._last_inventory_success_at.astimezone(self._settings.timezone).date()
            == datetime.now(self._settings.timezone).date()
        )

    def _ensure_external_inventory_plan(self) -> datetime | None:
        """Plan the next local-time daily lightweight inventory pass."""
        if not self._external_inventory_schedule_enabled():
            self._external_inventory_planned = None
            return None
        now = datetime.now(UTC)
        if self._external_inventory_planned is not None:
            if self._external_inventory_planned > now:
                return self._external_inventory_planned
        prefs = self._preferences_store.effective()
        hour_text, minute_text = prefs.external_inventory_schedule_time.split(":", 1)
        local_now = now.astimezone(self._settings.timezone)
        today_at = local_now.replace(
            hour=int(hour_text),
            minute=int(minute_text),
            second=0,
            microsecond=0,
        )
        completed_today = self._inventory_completed_today()
        if today_at <= local_now and not completed_today:
            # Service was offline at the configured time: queue a non-blocking
            # catch-up shortly after startup, then wait for active work to drain.
            candidate = local_now + timedelta(seconds=30)
        elif today_at > local_now:
            candidate = today_at
        else:
            candidate = today_at + timedelta(days=1)
        self._external_inventory_planned = candidate.astimezone(UTC)
        return self._external_inventory_planned

    def _library_work_active(self) -> bool:
        if self._manual_sync_task is not None and not self._manual_sync_task.done():
            return True
        if any(not task.done() for task in self._external_sync_tasks.values()):
            return True
        return self._job_executor.has_active_jobs()

    def _ensure_external_plan(self, playlist: object) -> datetime:
        dir_name = str(playlist.dir_name)
        existing = self._external_planned.get(dir_name)
        if existing is not None and existing > datetime.now(UTC):
            return existing
        fire_at = self._roll_fire_at(
            int(getattr(playlist, "sync_jitter_seconds", 0) or 0),
            self._get_next_cron_after(datetime.now(UTC)),
        )
        self._external_planned[dir_name] = fire_at
        return fire_at

    def _direct_auto_recover_enabled(self) -> bool:
        if self._preferences_store is None:
            return False
        return bool(self._preferences_store.effective().direct_auto_recover_enabled)

    def _wanted_auto_match_enabled(self) -> bool:
        if self._preferences_store is None:
            return False
        prefs = self._preferences_store.effective()
        return bool(prefs.wanted_enabled and prefs.wanted_auto_match_enabled)

    def _direct_jitter_max(self) -> int:
        if self._preferences_store is None:
            return 0
        configured = max(
            0,
            min(
                600,
                int(self._preferences_store.effective().direct_sync_jitter_seconds),
            ),
        )
        interval = self._cron_interval_seconds()
        if interval <= 0:
            return configured
        cap = interval if interval <= 1 else interval - 1
        return min(configured, cap)

    def _ensure_direct_plan(self) -> datetime | None:
        if not self.enabled or not self._direct_auto_recover_enabled():
            self._direct_planned = None
            return None
        now = datetime.now(UTC)
        if self._direct_planned is not None and self._direct_planned > now:
            return self._direct_planned
        base = self._get_next_cron_after(now)
        n = self._direct_jitter_max()
        candidate_base = base
        for _ in range(8):
            offset = random.randint(-n, n) if n > 0 else 0
            fire_at = candidate_base + timedelta(seconds=offset)
            if fire_at > now:
                self._direct_planned = fire_at
                return fire_at
            candidate_base = self._get_next_cron_after(candidate_base)
        self._direct_planned = now + timedelta(seconds=1)
        return self._direct_planned

    def _ensure_wanted_plan(self) -> datetime | None:
        if not self.enabled or not self._wanted_auto_match_enabled():
            self._wanted_planned = None
            return None
        now = datetime.now(UTC)
        if self._wanted_planned is not None and self._wanted_planned > now:
            return self._wanted_planned
        prefs = self._preferences_store.effective()
        self._wanted_planned = self._roll_fire_at(
            prefs.wanted_sync_jitter_seconds,
            self._get_next_cron_after(now),
        )
        return self._wanted_planned

    def _refresh_status(
        self,
        subscriptions: list[Subscription],
        external_playlists: list[object] | None = None,
    ) -> tuple[datetime | None, Subscription | None]:
        """Recompute soonest fire and publish status fields."""
        direct_at = self._ensure_direct_plan() if self.enabled else None
        wanted_at = self._ensure_wanted_plan() if self.enabled else None
        inventory_at = self._ensure_external_inventory_plan()
        external_playlists = (
            self._scheduled_external_playlists()
            if external_playlists is None
            else external_playlists
        )

        if (
            not subscriptions
            and not external_playlists
            and direct_at is None
            and wanted_at is None
            and inventory_at is None
        ):
            self._next_run_at = None
            self._next_run_subscription_id = None
            self._next_run_subscription_name = None
            self._next_run_target_kind = None
            self._next_run_target_id = None
            self._next_run_target_name = None
            alive = {s.id for s in subscriptions}
            for sid in list(self._planned):
                if sid not in alive:
                    self._planned.pop(sid, None)
            alive_external = {str(playlist.dir_name) for playlist in external_playlists}
            for dir_name in list(self._external_planned):
                if dir_name not in alive_external:
                    self._external_planned.pop(dir_name, None)
            if not self.enabled:
                self._direct_planned = None
                self._wanted_planned = None
            return None, None

        candidates: list[
            tuple[
                datetime,
                Literal[
                    "subscription",
                    "external",
                    "direct",
                    "wanted",
                    "external_inventory",
                ],
                str | None,
                str,
                Subscription | None,
            ]
        ] = []
        alive_ids: set[UUID] = set()
        for sub in subscriptions:
            alive_ids.add(sub.id)
            candidates.append(
                (self._ensure_plan(sub), "subscription", str(sub.id), sub.name, sub)
            )

        for sid in list(self._planned):
            if sid not in alive_ids:
                self._planned.pop(sid, None)

        alive_external: set[str] = set()
        for playlist in external_playlists:
            dir_name = str(playlist.dir_name)
            alive_external.add(dir_name)
            candidates.append(
                (
                    self._ensure_external_plan(playlist),
                    "external",
                    dir_name,
                    dir_name,
                    None,
                )
            )
        for dir_name in list(self._external_planned):
            if dir_name not in alive_external:
                self._external_planned.pop(dir_name, None)

        if direct_at is not None:
            candidates.append((direct_at, "direct", None, "下载中心", None))
        if wanted_at is not None:
            candidates.append((wanted_at, "wanted", None, "心爱歌单", None))
        if inventory_at is not None:
            candidates.append(
                (
                    inventory_at,
                    "external_inventory",
                    None,
                    "外部曲库盘点",
                    None,
                )
            )

        soonest_at, kind, target_id, target_name, soonest_sub = min(
            candidates, key=lambda candidate: candidate[0]
        )
        self._next_run_at = soonest_at
        self._next_run_target_kind = kind
        self._next_run_target_id = target_id
        self._next_run_target_name = target_name
        self._next_run_subscription_id = soonest_sub.id if soonest_sub else None
        self._next_run_subscription_name = soonest_sub.name if soonest_sub else None
        return soonest_at, soonest_sub

    def start(self) -> None:
        """Start the scheduler background task."""
        if self._task is not None and not self._task.done():
            return
        self._task = None
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Scheduler started")

    async def stop(self) -> None:
        """Stop the scheduler background task."""
        if self._task is None:
            return
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        self._planned.clear()
        self._external_planned.clear()
        self._direct_planned = None
        self._wanted_planned = None
        self._external_inventory_planned = None
        self._next_run_at = None
        self._next_run_subscription_id = None
        self._next_run_subscription_name = None
        self._next_run_target_kind = None
        self._next_run_target_id = None
        self._next_run_target_name = None
        self._tracked_cron = None
        background = [
            task
            for task in [self._manual_sync_task, *self._external_sync_tasks.values()]
            if task is not None and not task.done()
        ]
        for task in background:
            task.cancel()
        if background:
            await asyncio.gather(*background, return_exceptions=True)
        self._manual_sync_task = None
        self._external_sync_tasks.clear()
        logger.info("Scheduler stopped")

    async def _run_loop(self) -> None:
        """Main scheduler loop — wake for the soonest per-sub fire."""
        while not self._stop_event.is_set():
            try:
                await self._run_loop_once()
            except Exception:
                logger.exception("Scheduler loop crashed; restarting in 30s")
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=30.0)
                    break
                except TimeoutError:
                    continue
        self._task = None

    async def _run_loop_once(self) -> None:
        """One scheduler wake cycle."""
        while not self._stop_event.is_set():
            cron = self.cron_expression
            if self._tracked_cron is not None and cron != self._tracked_cron:
                self.invalidate_plan()
                logger.info("Scheduler cron changed; cleared planned fires")
            self._tracked_cron = cron

            enabled_subs = (
                self._subscription_service.list(enabled=True) if self.enabled else []
            )
            external_playlists = (
                self._scheduled_external_playlists() if self.enabled else []
            )
            soonest_at, _ = self._refresh_status(enabled_subs, external_playlists)

            if soonest_at is None:
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=5.0)
                    break
                except TimeoutError:
                    continue

            wait_seconds = (soonest_at - datetime.now(UTC)).total_seconds()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=max(0.0, wait_seconds),
                )
                break
            except TimeoutError:
                pass

            if not self.enabled:
                if not self._external_inventory_schedule_enabled():
                    continue

            now = datetime.now(UTC)
            due = [
                sub
                for sub in self._subscription_service.list(enabled=True)
                if self._planned.get(sub.id) is not None
                and self._planned[sub.id] <= now
            ]
            direct_due = (
                self._direct_planned is not None and self._direct_planned <= now
            )
            due_external = [
                playlist
                for playlist in external_playlists
                if self._external_planned.get(str(playlist.dir_name)) is not None
                and self._external_planned[str(playlist.dir_name)] <= now
            ]
            wanted_due = (
                self._wanted_planned is not None and self._wanted_planned <= now
            )
            inventory_due = (
                self._external_inventory_planned is not None
                and self._external_inventory_planned <= now
            )
            if inventory_due and self._library_work_active():
                self._external_inventory_planned = now + timedelta(minutes=5)
                inventory_due = False
                logger.info("External inventory deferred for active library work")
            if due or due_external or direct_due or wanted_due or inventory_due:
                await asyncio.to_thread(
                    self._run_due_scheduled_tasks,
                    subscriptions=due,
                    external_playlists=due_external,
                    run_direct=direct_due,
                    run_wanted=wanted_due,
                    run_inventory=inventory_due,
                )
                for sub in due:
                    # Drop plan so the next cycle rolls a new random offset
                    self._planned.pop(sub.id, None)
                for playlist in due_external:
                    self._external_planned.pop(str(playlist.dir_name), None)
                if direct_due:
                    self._direct_planned = None
                if wanted_due:
                    self._wanted_planned = None
                if inventory_due:
                    self._external_inventory_planned = (
                        None
                        if self._inventory_completed_today()
                        else datetime.now(UTC) + timedelta(minutes=5)
                    )
                self._maybe_offline_cleanup()

    def _run_due_scheduled_tasks(
        self,
        *,
        subscriptions: list[Subscription],
        external_playlists: list[object],
        run_direct: bool,
        run_wanted: bool,
        run_inventory: bool = False,
    ) -> None:
        """Run only the independently scheduled tasks that are due now."""
        if run_inventory:
            try:
                result = self.reconcile_external_inventory()
                if result is not None:
                    logger.info(
                        "External inventory reconciled: scanned=%d added=%d "
                        "updated=%d removed=%d errors=%d",
                        result.scanned,
                        result.added,
                        result.updated,
                        result.removed,
                        result.errors,
                    )
                # Merge a simultaneous inventory and routine external run:
                # every enabled playlist is processed once after discovery.
                external_playlists = self._scheduled_external_playlists()
            except Exception:
                logger.exception("Scheduled external inventory failed")
        if subscriptions:
            try:
                self._create_jobs_for_subscriptions(subscriptions)
            except InsufficientDiskSpaceError as exc:
                logger.warning("Skipping scheduled subscription sync: %s", exc)
        if run_direct:
            self._create_direct_recover_job(JobSource.SCHEDULER)
        for playlist in external_playlists:
            dir_name = str(playlist.dir_name)
            service = self._external_library_service
            if service is None:
                continue
            try:
                service.record_playlist_sync_status(dir_name, status="running")
                result = (
                    self._pipeline.drain_external_playlist(
                        dir_name,
                        trigger="daily-external-scan",
                    )
                    if run_inventory
                    else self._pipeline.sync_external_playlist(
                        dir_name,
                        trigger="scheduler",
                    )
                )
                if result.errors:
                    service.record_playlist_sync_status(dir_name, status="failed")
            except Exception:
                logger.exception("Scheduled external sync failed for %s", dir_name)
                service.record_playlist_sync_status(dir_name, status="failed")
        if run_wanted:
            self._pipeline.sync_wanted(trigger="scheduler")
        if run_inventory:
            try:
                self._pipeline.refresh_library_summary()
            except Exception:
                logger.exception("Scheduled library summary refresh failed")

    def _create_direct_recover_job(self, source: JobSource) -> str | None:
        """Enqueue a Direct recover job. Returns job id or None."""
        from yubal_api.services.direct_recover_service import DIRECT_RECOVER_URL

        max_items = 100
        if self._preferences_store is not None:
            max_items = self._preferences_store.effective().direct_max_items
        try:
            job = self._job_executor.create_and_start_job(
                DIRECT_RECOVER_URL,
                max_items,
                source,
                None,
            )
            if job is None:
                logger.warning(
                    "Could not create Download Center recovery job (queue full)"
                )
                return None
            logger.info("Created Download Center recovery job %s", job.id[:8])
            return job.id
        except InsufficientDiskSpaceError:
            raise
        except Exception:
            logger.exception("Failed to create Download Center recovery job")
            return None

    def _create_jobs_for_subscriptions(
        self, subscriptions: list[Subscription]
    ) -> list[str]:
        """Create sync jobs for given subscriptions."""
        job_ids: list[str] = []
        for subscription in subscriptions:
            try:
                job = self._job_executor.create_and_start_job(
                    subscription.url,
                    subscription.max_items,
                    JobSource.SCHEDULER,
                    subscription.id,
                )
                if job is None:
                    logger.warning(
                        "Could not create job for %s (queue full)",
                        subscription.name,
                    )
                    continue

                job_ids.append(job.id)
                self._subscription_service.update(
                    subscription.id,
                    {"last_synced_at": datetime.now(UTC)},
                )
                logger.info(
                    "Created sync job %s for %s",
                    job.id[:8],
                    subscription.name,
                )
            except InsufficientDiskSpaceError:
                raise
            except Exception:
                logger.exception(
                    "Failed to create job for %s",
                    subscription.name,
                )
        return job_ids

    async def _check_library_health(self) -> None:
        """Refresh the Download/External mount health probe."""
        await asyncio.to_thread(self._pipeline.check_health)

    async def _maybe_scan_and_match_external(self) -> None:
        """Scan External/Raw for new files and attempt YTM matches for a batch."""
        await asyncio.to_thread(self._pipeline.run_external_scan_and_match)

    async def _sync_subscriptions(self, subscriptions: list[Subscription]) -> list[str]:
        """Sync the given subscriptions (async wrapper)."""
        try:
            return self._create_jobs_for_subscriptions(subscriptions)
        except InsufficientDiskSpaceError as e:
            logger.warning("Skipping scheduled sync: %s", e)
            return []

    def sync_subscription(self, subscription_id: UUID) -> str | None:
        """Create sync job for a single subscription. Returns job_id or None."""
        if self._operation_gate is not None:
            self._operation_gate.ensure_allowed()
        try:
            subscription = self._subscription_service.get(subscription_id)
        except SubscriptionNotFoundError:
            return None

        job_ids = self._create_jobs_for_subscriptions([subscription])
        return job_ids[0] if job_ids else None

    def sync_direct(self) -> str | None:
        """Manually trigger Direct recover (bypasses jitter). Returns job_id."""
        if self._operation_gate is not None:
            self._operation_gate.ensure_allowed()
        return self._create_direct_recover_job(JobSource.MANUAL)

    def _mark_external_playlists_queued(self) -> int:
        """Discover External/Raw folders and make their queued state visible.

        This is intentionally only a top-level directory reconciliation.  It
        never walks audio files, reads tags or contacts a provider, so the UI
        can render newly discovered playlist cards before the heavy pass starts.
        """
        service = self._external_library_service
        if service is None:
            return 0
        prefs = (
            self._preferences_store.effective()
            if self._preferences_store is not None
            else None
        )
        if prefs is not None and not prefs.external_library_enabled:
            return 0
        service.sync_playlists_from_disk()
        queued = 0
        for playlist in service.list_playlists():
            if playlist.enabled and (
                getattr(playlist, "access_mode", "readonly") != EXTERNAL_ACCESS_PENDING
            ):
                service.record_playlist_sync_status(playlist.dir_name, status="queued")
                queued += 1
        return queued

    def _queued_manual_steps(
        self, external_count: int
    ) -> list[dict[str, str | int | None]]:
        """Return the immediate, truthful snapshot for an accepted Sync All."""
        return [
            {"key": "health", "status": "queued", "count": None},
            {"key": "subscriptions", "status": "queued", "count": None},
            {"key": "direct", "status": "queued", "count": None},
            {"key": "external", "status": "queued", "count": external_count},
            {"key": "wanted", "status": "queued", "count": None},
            {"key": "enrichment", "status": "queued", "count": None},
            {"key": "hardlinks", "status": "queued", "count": None},
        ]

    async def queue_manual_sync(
        self,
    ) -> tuple[list[str], list[dict[str, str | int | None]]]:
        """Accept Sync All immediately and run the shared core in background.

        The synchronous ``run_unified_sync`` remains the single business core
        used by scheduler and Telegram.  This API-facing wrapper only moves it
        off the request path and performs the cheap directory discovery first.
        """
        task = self._manual_sync_task
        if task is not None and not task.done():
            return [], self.last_manual_sync_steps

        if self._operation_gate is not None:
            self._operation_gate.ensure_allowed()
        external_count = await asyncio.to_thread(self._mark_external_playlists_queued)
        steps = self._queued_manual_steps(external_count)
        self._last_manual_sync_steps = steps

        async def run() -> None:
            try:
                await asyncio.to_thread(self.run_unified_sync, source="sync_all")
            except Exception:
                logger.exception("Background manual Sync All failed")

        task = asyncio.create_task(run(), name="manual-library-sync")
        self._manual_sync_task = task

        def clear(completed: asyncio.Task[None]) -> None:
            if self._manual_sync_task is completed:
                self._manual_sync_task = None

        task.add_done_callback(clear)
        return [], [dict(step) for step in steps]

    async def queue_external_playlist_sync(
        self,
        dir_name: str,
        *,
        enrich: bool,
        raw_match: bool,
        verify_meta: bool,
        junk_match: bool,
        scan_first: bool = False,
        drain: bool = False,
    ) -> bool:
        """Queue one external playlist using the same canonical pipeline.

        Returns ``False`` when that playlist already has an active pass.  The
        caller can still refresh and show its current queued/running state.
        """
        if self._operation_gate is not None:
            self._operation_gate.ensure_allowed()
        current = self._external_sync_tasks.get(dir_name)
        if current is not None and not current.done():
            return True
        global_task = self._manual_sync_task
        if global_task is not None and not global_task.done():
            return True

        service = self._external_library_service
        view = service.get_playlist_view(dir_name) if service is not None else None
        if view is None:
            raise ValueError(f"playlist not found: {dir_name}")
        if view.access_mode == EXTERNAL_ACCESS_PENDING:
            raise ValueError(
                "playlist access mode is pending; choose read-only or managed first"
            )
        service.record_playlist_sync_status(dir_name, status="queued")

        async def run() -> None:
            try:
                service.record_playlist_sync_status(dir_name, status="running")
                if scan_first:
                    await asyncio.to_thread(
                        self.reconcile_external_inventory,
                        dir_name=dir_name,
                    )
                if drain:
                    await asyncio.to_thread(
                        self._pipeline.drain_external_playlist,
                        dir_name,
                        trigger="external-scan",
                    )
                else:
                    await asyncio.to_thread(
                        self._pipeline.sync_external_playlist,
                        dir_name,
                        trigger="playlist",
                        enrich=enrich,
                        raw_match=raw_match,
                        verify_meta=verify_meta,
                        junk_match=junk_match,
                    )
            except Exception:
                logger.exception(
                    "Background external playlist sync failed for %s", dir_name
                )
                service.record_playlist_sync_status(dir_name, status="failed")

        task = asyncio.create_task(run(), name=f"external-sync-{dir_name}")
        self._external_sync_tasks[dir_name] = task

        def clear(completed: asyncio.Task[None]) -> None:
            if self._external_sync_tasks.get(dir_name) is completed:
                self._external_sync_tasks.pop(dir_name, None)

        task.add_done_callback(clear)
        return True

    def run_unified_sync(
        self,
        *,
        source: str,
        subscriptions: list[Subscription] | None = None,
        run_direct: bool | None = None,
    ) -> list[str]:
        """Shared Sync All / scheduled pipeline (identical steps).

        Order:
          1) health check
          2) sync subscriptions (all enabled vs due — caller chooses list)
          3) Direct recover (when enabled / due)
          4) external scan → verify → match → materialize → enrich
          5) Wanted local link → asset completion → YTM fulfill
          6) final whole-library enrichment / on-disk verification
          7) collapse_divergent_copies

        ``source`` is ``\"sync_all\"`` or ``\"scheduler\"``. Both use the same
        library rules; only subscription scope and trigger time differ. When
        ``subscriptions`` is None and source is sync_all, all
        enabled subscriptions are used. Scheduled passes the due list (may be
        empty when only Direct is due).
        """
        is_manual = source == "sync_all"
        job_source = JobSource.MANUAL if is_manual else JobSource.SCHEDULER

        if is_manual and self._operation_gate is not None:
            self._operation_gate.ensure_allowed()

        steps: list[dict[str, str | int | None]] = []
        health_ok = self._pipeline.check_health()
        if not health_ok and self._external_library_service is not None:
            for playlist in self._external_library_service.list_playlists():
                if playlist.last_sync_status in {"queued", "running"}:
                    self._external_library_service.record_playlist_sync_status(
                        playlist.dir_name,
                        status="failed",
                    )
        steps.append(
            {
                "key": "health",
                "status": "complete" if health_ok else "failed",
                "count": None,
            }
        )

        if subscriptions is None:
            if is_manual:
                subscriptions = self._subscription_service.list(enabled=True)
            else:
                subscriptions = []

        job_ids: list[str] = []
        if subscriptions:
            try:
                job_ids.extend(self._create_jobs_for_subscriptions(subscriptions))
            except InsufficientDiskSpaceError as e:
                logger.warning("Skipping subscription sync (%s): %s", source, e)
        steps.append(
            {
                "key": "subscriptions",
                "status": "queued" if job_ids else "skipped",
                "count": len(job_ids),
            }
        )

        do_direct = (
            self._direct_auto_recover_enabled()
            if run_direct is None
            else bool(run_direct)
        )
        if do_direct:
            direct_id = self._create_direct_recover_job(job_source)
            if direct_id:
                job_ids.append(direct_id)
            direct_status = "queued" if direct_id else "skipped"
        else:
            self._pipeline.reconcile_direct()
            direct_status = "complete"
        steps.append(
            {
                "key": "direct",
                "status": direct_status,
                "count": 1 if direct_status == "queued" else None,
            }
        )

        prefs = (
            self._preferences_store.effective()
            if self._preferences_store is not None
            else None
        )
        cycle = self._pipeline.run_library_cycle(
            trigger=source,
            enrichment_budget=500,
        )
        external_count = (
            cycle.external.matched
            + cycle.external.meta_verified
            + cycle.external.recovered
            + cycle.external.enriched
        )
        steps.append(
            {
                "key": "external",
                "status": (
                    (
                        "failed"
                        if cycle.external.errors or cycle.external.asset_errors
                        else "complete"
                    )
                    if self._external_library_service is not None
                    and (prefs is None or prefs.external_library_enabled)
                    else "skipped"
                ),
                "count": external_count,
            }
        )
        wanted_count = sum(
            int(cycle.wanted.get(key, 0))
            for key in (
                "linked",
                "matched",
                "covers_written",
                "lyrics_written",
            )
        )
        steps.append(
            {
                "key": "wanted",
                "status": (
                    (
                        "failed"
                        if cycle.wanted.get("asset_failed", 0)
                        or cycle.wanted.get("failed", 0)
                        else "complete"
                    )
                    if self._wanted_service is not None
                    and (prefs is None or prefs.wanted_enabled)
                    else "skipped"
                ),
                "count": wanted_count,
            }
        )
        steps.append(
            {
                "key": "enrichment",
                "status": (
                    "failed"
                    if cycle.enrichment.failed
                    else "complete"
                    if self._library_enrichment_service is not None
                    else "skipped"
                ),
                "count": cycle.enrichment.enriched,
            }
        )
        steps.append(
            {
                "key": "hardlinks",
                "status": (
                    "complete" if self._library_dedup_service is not None else "skipped"
                ),
                "count": None,
            }
        )
        if is_manual:
            self._last_manual_sync_steps = steps
        return job_ids

    def _run_wanted_pass(self) -> None:
        """Local hardlink match + YTM fulfill for wishlist (best-effort)."""
        if self._wanted_service is None:
            return
        prefs = (
            self._preferences_store.effective()
            if self._preferences_store is not None
            else None
        )
        if prefs is not None and not prefs.wanted_enabled:
            return
        try:
            result = self._wanted_service.run_sync_pass(force_ytm=False)
            if result.get("linked") or result.get("matched"):
                logger.info("Wanted sync pass %s", result)
        except Exception as exc:
            logger.warning("Wanted sync pass failed: %s", exc)

    def sync_all(self) -> list[str]:
        """Create sync jobs for all enabled subscriptions. Returns job_ids.

        Same pipeline as the scheduled cycle, except subscriptions are all
        enabled ones rather than only those due by cron. Does **not** ignore
        match backoff.
        """
        return self.run_unified_sync(source="sync_all")

    def _run_external_scan_and_match_sync(self) -> None:
        """Best-effort external scan/match (same gate as the scheduler path)."""
        self._pipeline.run_external_scan_and_match()

    def _collapse_divergent_copies(self) -> None:
        """Hardlink same-video_id copies left from earlier copy fallbacks."""
        self._pipeline.collapse_divergent_copies()

    def _reconcile_direct(self) -> None:
        self._pipeline.reconcile_direct()

    def _spawn_enrichment(
        self,
        *,
        budget: int | None,
        reason: str,
        save_folder: str | None = None,
    ) -> None:
        """Run a library enrichment pass in a daemon thread (never blocks)."""
        self._pipeline.spawn_enrichment(
            budget=budget, reason=reason, save_folder=save_folder
        )

    def _maybe_offline_cleanup(self) -> None:
        """Run due offline / ID-invalid cleanup at most once per hour."""
        now = datetime.now(UTC)
        if (
            self._last_offline_cleanup_at is not None
            and now - self._last_offline_cleanup_at < timedelta(hours=1)
        ):
            return
        processed = 0
        try:
            if self._membership_service is not None:
                processed += self._membership_service.run_offline_cleanup(now=now)
            if self._sync_ledger_service is not None:
                processed += self._sync_ledger_service.run_id_invalid_cleanup(now=now)
            if self._external_library_service is not None:
                processed += self._external_library_service.run_id_invalid_cleanup(
                    now=now
                )
            self._last_offline_cleanup_at = now
            if processed:
                logger.info(
                    "Offline / ID-invalid cleanup processed %d track(s)", processed
                )
        except Exception:
            logger.exception("Scheduled offline / ID-invalid cleanup failed")
