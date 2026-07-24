"""Admin report text builders for the Telegram bot."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func
from sqlmodel import Session, col, select

from yubal.utils.library import DIRECT_FOLDER, sanitize_direct_folder

from yubal_api.db.external_library import (
    MATCH_PENDING,
    MATCH_REJECTED,
    MATCH_UNMATCHED,
    ExternalRawTrack,
)
from yubal_api.db.subscription_membership import (
    MembershipStatus,
    SnapshotStatus,
    SubscriptionSyncSnapshot,
    SubscriptionTrack,
)
from yubal_api.db.track_catalog import LocationMembershipStatus, TrackRecord
from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.services.library_hardlink import location_abs_path
from yubal_api.services.subscription_service import SubscriptionService


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _bold(text: str) -> str:
    return f"<b>{_esc(text)}</b>"


def format_uptime(started_at: datetime, *, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    started = started_at if started_at.tzinfo else started_at.replace(tzinfo=UTC)
    seconds = max(0, int((now - started).total_seconds()))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}天 {hours}小时 {minutes}分"
    if hours:
        return f"{hours}小时 {minutes}分"
    if minutes:
        return f"{minutes}分 {secs}秒"
    return f"{secs}秒"


@dataclass(frozen=True)
class LibraryStats:
    subscriptions: int
    library_present: int
    missing: int
    unmatched: int


def collect_library_stats(
    *,
    engine: object,
    subscriptions: SubscriptionService,
    catalog: TrackCatalogRepository,
    data_path: Path,
    direct_folder: str = DIRECT_FOLDER,
) -> LibraryStats:
    """Compute admin /stats counters."""
    sub_count = subscriptions.count()
    present_ids = _present_video_ids(catalog, data_path)
    missing = _count_missing(
        engine=engine,
        subscriptions=subscriptions,
        catalog=catalog,
        data_path=data_path,
        direct_folder=sanitize_direct_folder(direct_folder),
    )
    unmatched = _count_unmatched(engine)
    return LibraryStats(
        subscriptions=sub_count,
        library_present=len(present_ids),
        missing=missing,
        unmatched=unmatched,
    )


def _present_video_ids(
    catalog: TrackCatalogRepository, data_path: Path
) -> set[str]:
    present: set[str] = set()
    for video_id, locs in catalog.list_all_by_video_id().items():
        for loc, _rec in locs:
            try:
                if location_abs_path(loc, download_root=data_path).is_file():
                    present.add(video_id)
                    break
            except OSError:
                continue
    return present


def _folder_present_ids(
    catalog: TrackCatalogRepository, data_path: Path, folder: str
) -> set[str]:
    present: set[str] = set()
    for loc, _rec in catalog.list_for_save_folder(folder):
        try:
            if location_abs_path(loc, download_root=data_path).is_file():
                present.add(loc.video_id)
        except OSError:
            continue
    return present


def _count_missing(
    *,
    engine: object,
    subscriptions: SubscriptionService,
    catalog: TrackCatalogRepository,
    data_path: Path,
    direct_folder: str,
) -> int:
    missing = 0
    with Session(engine) as session:  # type: ignore[arg-type]
        for sub in subscriptions.list():
            folder = (sub.save_folder or sub.name or "").strip()
            folder_present = (
                _folder_present_ids(catalog, data_path, folder)
                if folder
                else set()
            )
            stmt = select(SubscriptionTrack).where(
                SubscriptionTrack.subscription_id == sub.id,
                SubscriptionTrack.membership_status == MembershipStatus.ACTIVE,
            )
            for row in session.exec(stmt).all():
                vid = row.catalog_video_id or row.video_id
                if vid and vid not in folder_present:
                    missing += 1

    for loc, _rec in catalog.list_for_save_folder(direct_folder):
        if loc.membership_status in {
            LocationMembershipStatus.OFFLINE,
            LocationMembershipStatus.BLOCKED,
        }:
            continue
        try:
            if not location_abs_path(loc, download_root=data_path).is_file():
                missing += 1
        except OSError:
            missing += 1
    return missing


def _count_unmatched(engine: object) -> int:
    with Session(engine) as session:  # type: ignore[arg-type]
        stmt = (
            select(func.count())
            .select_from(ExternalRawTrack)
            .where(
                col(ExternalRawTrack.match_status).in_(
                    [MATCH_UNMATCHED, MATCH_PENDING, MATCH_REJECTED]
                )
            )
        )
        return int(session.exec(stmt).one() or 0)


@dataclass(frozen=True)
class RuntimeStatus:
    uptime: str
    scheduler_enabled: bool
    jobs_active: int
    sync_fail_24h: int
    enrich_fail_24h: int
    match_fail_backlog: int
    scrape_fail_backlog: int
    library_ok: bool | None


def collect_runtime_status(
    *,
    engine: object,
    started_at: datetime,
    scheduler_enabled: bool,
    jobs_active: int,
    library_healthy: bool | None = None,
) -> RuntimeStatus:
    since = datetime.now(UTC) - timedelta(hours=24)
    return RuntimeStatus(
        uptime=format_uptime(started_at),
        scheduler_enabled=scheduler_enabled,
        jobs_active=jobs_active,
        sync_fail_24h=_count_sync_fail_24h(engine, since),
        enrich_fail_24h=_count_enrich_fail_24h(engine, since),
        match_fail_backlog=_count_match_fail_backlog(engine),
        scrape_fail_backlog=_count_scrape_fail_backlog(engine),
        library_ok=library_healthy,
    )


def _count_sync_fail_24h(engine: object, since: datetime) -> int:
    with Session(engine) as session:  # type: ignore[arg-type]
        stmt = (
            select(func.count())
            .select_from(SubscriptionSyncSnapshot)
            .where(
                SubscriptionSyncSnapshot.status == SnapshotStatus.FAILED,
                col(SubscriptionSyncSnapshot.finished_at) >= since,
            )
        )
        return int(session.exec(stmt).one() or 0)


def _count_enrich_fail_24h(engine: object, since: datetime) -> int:
    with Session(engine) as session:  # type: ignore[arg-type]
        stmt = (
            select(func.count())
            .select_from(TrackRecord)
            .where(
                col(TrackRecord.last_enrich_error).is_not(None),
                col(TrackRecord.last_enrich_error) != "",
                col(TrackRecord.last_enriched_at) >= since,
            )
        )
        return int(session.exec(stmt).one() or 0)


def _count_match_fail_backlog(engine: object) -> int:
    with Session(engine) as session:  # type: ignore[arg-type]
        stmt = (
            select(func.count())
            .select_from(ExternalRawTrack)
            .where(col(ExternalRawTrack.match_fail_count) > 0)
        )
        return int(session.exec(stmt).one() or 0)


def _count_scrape_fail_backlog(engine: object) -> int:
    with Session(engine) as session:  # type: ignore[arg-type]
        stmt = (
            select(func.count())
            .select_from(ExternalRawTrack)
            .where(col(ExternalRawTrack.scrape_fail_count) > 0)
        )
        return int(session.exec(stmt).one() or 0)


def render_start_guide() -> str:
    return "\n".join(
        [
            f"🎵 {_bold('yubal')}",
            "",
            "发送单曲链接、歌单链接，或直接发送歌名。",
            "",
            f"📋 {_bold('管理命令')}",
            "/stats — 数据库统计",
            "/sync — 立即同步",
            "/status — 运行概况",
            "/factory — 恢复出厂（占位）",
        ]
    )


def render_stats(stats: LibraryStats) -> str:
    return "\n".join(
        [
            f"📊 {_bold('数据库统计')}",
            "",
            f"订阅歌单：{stats.subscriptions}",
            f"媒体库：{stats.library_present}",
            f"缺失：{stats.missing}",
            f"本地未匹配：{stats.unmatched}",
            "",
            "<i>媒体库 = 已有实体文件的曲目（不含未匹配 / 缺失）</i>",
        ]
    )


def render_sync_ack(job_count: int) -> str:
    lines = [
        f"🔄 {_bold('已触发立即同步')}",
        "",
        f"已排队订阅任务：{job_count}",
        "流程与网页「立即同步」相同。",
    ]
    if job_count == 0:
        lines.append("")
        lines.append("当前没有可排队的订阅任务（可能均未启用）。")
    return "\n".join(lines)


def render_status(status: RuntimeStatus) -> str:
    sched = "开启" if status.scheduler_enabled else "关闭"
    health = (
        "正常"
        if status.library_ok is True
        else "异常"
        if status.library_ok is False
        else "未知"
    )
    return "\n".join(
        [
            f"🖥 {_bold('系统运行概况')}",
            "",
            f"运行时间：{status.uptime}",
            f"定时同步：{sched}",
            f"队列任务：{status.jobs_active}",
            f"曲库健康：{health}",
            "",
            f"⏱ {_bold('近 24 小时')}",
            f"同步失败：{status.sync_fail_24h}",
            f"刮削失败：{status.enrich_fail_24h}",
            "",
            f"📦 {_bold('当前积压')}",
            f"匹配失败：{status.match_fail_backlog}",
            f"刮削退避：{status.scrape_fail_backlog}",
            "",
            "<i>匹配/刮削积压为累计未恢复条目，非严格 24h 窗口。</i>",
        ]
    )


def render_factory_placeholder() -> str:
    return "\n".join(
        [
            f"⚠️ {_bold('恢复出厂设置')}",
            "",
            "功能占位，尚未开放。",
            "不会执行任何清除或重置操作。",
        ]
    )
