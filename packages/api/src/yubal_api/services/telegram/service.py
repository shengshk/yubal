"""Telegram B2 bot: polling, sessions, cleanup, and interaction flow."""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from yubal import ContentKind
from yubal.utils.url import (
    PLAYLIST_ID_PATTERN,
    is_supported_url,
    parse_video_id,
)

from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.services.factory_reset_service import (
    FactoryResetMode,
    FactoryResetPreview,
    FactoryResetService,
)
from yubal_api.services.job_executor import JobExecutor
from yubal_api.services.job_store import JobStore
from yubal_api.services.library_hardlink import location_abs_path
from yubal_api.services.library_lookup_service import LibraryLookupService
from yubal_api.services.playlist_info_service import PlaylistInfoService
from yubal_api.services.preferences import PreferencesStore
from yubal_api.services.search_service import SearchService
from yubal_api.services.subscription_service import SubscriptionService
from yubal_api.services.telegram.admin_reports import (
    collect_library_stats,
    collect_runtime_status,
    render_start_guide,
    render_stats,
    render_status,
    render_sync_ack,
)
from yubal_api.services.telegram.client import BotApiClient
from yubal_api.services.telegram.sender import AudioSender, extract_delivery
from yubal_api.services.telegram.stores import DailyQuota, FileIdStore
from yubal_api.services.wanted_service import WantedService

if TYPE_CHECKING:
    from yubal_api.services.library_health_service import LibraryHealthService
    from yubal_api.services.scheduler import Scheduler

logger = logging.getLogger(__name__)

_ID_SPLIT = re.compile(r"[,;\s]+")
_BOT_COMMANDS = [
    {"command": "start", "description": "开始使用"},
    {"command": "stats", "description": "数据库统计"},
    {"command": "sync", "description": "立即同步"},
    {"command": "status", "description": "系统运行概况"},
    {"command": "factory", "description": "恢复出厂设置"},
]
_ADMIN_CMDS = frozenset({"stats", "sync", "status", "factory"})

# Message self-destruct is NOT one global timeout.
_BURN_EPHEMERAL = 2.0  # busy / short tips
_BURN_GUIDE = 20.0  # /start help
_BURN_REPORT = 60.0  # /stats /status /factory
_BURN_SYNC_ACK = 25.0  # /sync acknowledgement
_BURN_FLOW = 1.5  # song-flow status bubbles


def _parse_ids(raw: str) -> set[int]:
    result: set[int] = set()
    for part in _ID_SPLIT.split((raw or "").strip()):
        if not part:
            continue
        try:
            result.add(int(part))
        except ValueError:
            continue
    return result


def _command_name(text: str) -> str | None:
    """Extract /cmd from '/cmd', '/cmd@bot', '/cmd args'."""
    if not text.startswith("/"):
        return None
    head = text.split(None, 1)[0][1:]
    return head.split("@", 1)[0].lower() or None


def _inline(rows: list[list[tuple[str, str]]]) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": data} for text, data in row]
            for row in rows
        ]
    }


def _remove_keyboard() -> dict[str, Any]:
    """Strip any leftover reply keyboard from older /panel clients."""
    return {"remove_keyboard": True}


@dataclass
class ChatSession:
    """Per-chat ephemeral state; tracks messages to delete at end."""

    control_ids: set[int] = field(default_factory=set)
    preview_msg_id: int | None = None
    query: str = ""
    online_ids: list[str] = field(default_factory=list)
    # Parallel to online result rows: "ytm" | "meta"
    online_kinds: list[str] = field(default_factory=list)
    # Meta wish payloads aligned by index when kind == meta
    online_meta: list[dict[str, Any]] = field(default_factory=list)
    pending_url: str = ""
    factory_mode: FactoryResetMode | None = None
    factory_token: str = ""
    factory_stage: int = 0
    factory_code: str = ""
    factory_expires_at: datetime | None = None
    busy: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _callback_toast(data: str) -> str:
    """Short toast shown the instant a callback is answered."""
    if data == "x" or data.startswith("ds:"):
        return "已取消"
    if data == "onl":
        return "搜索中…"
    if data.startswith(("pick:", "loc:")):
        return "发送中…"
    if data.startswith(("dl:", "jd:")):
        return "下载中…"
    if data.startswith("pv:"):
        return "试听准备中…"
    if data.startswith("sv:"):
        return "保存中…"
    if data == "sub:y":
        return "订阅中…"
    if data.startswith("os:"):
        return "已选择"
    if data.startswith("ow:"):
        return "已加入心愿"
    if data.startswith("fr"):
        return "已选择"
    return "处理中…"


@dataclass(frozen=True)
class LocalOption:
    video_id: str
    title: str
    artist: str
    path: Path
    playlist_count: int


class TelegramBotService:
    """Long-polling Telegram bot wired into yubal library services."""

    def __init__(
        self,
        *,
        preferences: PreferencesStore,
        catalog: TrackCatalogRepository,
        library_lookup: LibraryLookupService,
        search: SearchService,
        playlist_info: PlaylistInfoService,
        job_executor: JobExecutor,
        job_store: JobStore,
        subscriptions: SubscriptionService,
        data_path: Path,
        config_path: Path,
        tg_api_url: str = "",
        scheduler: Scheduler | None = None,
        library_health: LibraryHealthService | None = None,
        db_engine: object | None = None,
        wanted_service: WantedService | None = None,
        factory_reset: FactoryResetService | None = None,
    ) -> None:
        self._preferences = preferences
        self._catalog = catalog
        self._lookup = library_lookup
        self._search = search
        self._playlist_info = playlist_info
        self._jobs = job_executor
        self._job_store = job_store
        self._subscriptions = subscriptions
        self._scheduler = scheduler
        self._library_health = library_health
        self._wanted = wanted_service
        self._factory_reset = factory_reset
        self._db_engine = db_engine or getattr(catalog, "_engine", None)
        self._data_path = data_path
        self._tg_api_url = (tg_api_url or "").strip()
        self._file_ids = FileIdStore(config_path / "telegram_file_ids.json")
        self._quota = DailyQuota(config_path / "telegram_quota.json")
        self._started_at = datetime.now(UTC)
        self._client: BotApiClient | None = None
        self._sender: AudioSender | None = None
        self._task: asyncio.Task[None] | None = None
        self._bg_tasks: set[asyncio.Task[None]] = set()
        self._stop = asyncio.Event()
        self._sessions: dict[int, ChatSession] = {}
        self._offset: int | None = None
        self._token: str = ""

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def clear_factory_state(self) -> None:
        """Drop account-bound Telegram caches after a full reset."""
        self._file_ids.clear()
        self._quota.clear()
        self._sessions.clear()

    async def start(self) -> None:
        await self.reload()

    async def stop(self) -> None:
        self._stop.set()
        for task in list(self._bg_tasks):
            task.cancel()
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        self._bg_tasks.clear()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._sender = None

    async def reload(self) -> None:
        prefs = self._preferences.effective()
        token = (prefs.telegram_bot_token or "").strip()
        if token == self._token and self.is_running:
            return
        await self.stop()
        self._stop = asyncio.Event()
        self._token = token
        if not token:
            logger.info("Telegram bot disabled (empty token)")
            return
        if not _parse_ids(prefs.telegram_admin_ids):
            logger.warning("Telegram bot token set but no admin IDs; not starting")
            return
        self._client = BotApiClient(token, api_base=self._tg_api_url)
        self._sender = AudioSender(self._client, self._file_ids)
        try:
            await self._client.delete_my_commands()
            await self._client.set_my_commands(_BOT_COMMANDS)
        except Exception:
            logger.warning("Failed to refresh Telegram bot commands", exc_info=True)
        self._task = asyncio.create_task(self._poll_loop(), name="telegram-bot")
        logger.info(
            "Telegram bot started (api=%s)",
            self._tg_api_url or "official",
        )

    # -- auth ------------------------------------------------------------------

    def _role(self, user_id: int) -> str | None:
        prefs = self._preferences.effective()
        admins = _parse_ids(prefs.telegram_admin_ids)
        users = _parse_ids(prefs.telegram_user_ids)
        if user_id in admins:
            return "admin"
        if users:
            return "user" if user_id in users else None
        return None

    def _session(self, chat_id: int) -> ChatSession:
        session = self._sessions.get(chat_id)
        if session is None:
            session = ChatSession()
            self._sessions[chat_id] = session
        return session

    def _track(self, chat_id: int, message: dict[str, Any] | None) -> None:
        if not message:
            return
        mid = message.get("message_id")
        if isinstance(mid, int):
            self._session(chat_id).control_ids.add(mid)

    async def _forget(self, chat_id: int, *message_ids: int | None) -> None:
        """Delete messages and drop them from the control set."""
        assert self._client is not None
        session = self._session(chat_id)
        for mid in message_ids:
            if not isinstance(mid, int):
                continue
            await self._client.delete_message(chat_id, mid)
            session.control_ids.discard(mid)

    async def _say(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        track: bool = True,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        assert self._client is not None
        msg = await self._client.send_message(
            chat_id, text, reply_markup=reply_markup, parse_mode=parse_mode
        )
        if track:
            self._track(chat_id, msg)
        return msg

    async def _cleanup(self, chat_id: int, *, keep_preview: bool = False) -> None:
        """Delete every tracked non-file control message. Audio is never tracked."""
        assert self._client is not None
        session = self._session(chat_id)
        ids = set(session.control_ids)
        if keep_preview and session.preview_msg_id is not None:
            ids.discard(session.preview_msg_id)
        for mid in ids:
            await self._client.delete_message(chat_id, mid)
        session.control_ids.clear()
        if not keep_preview:
            session.preview_msg_id = None
        session.query = ""
        session.online_ids = []
        session.pending_url = ""
        session.factory_mode = None
        session.factory_token = ""
        session.factory_stage = 0
        session.factory_code = ""
        session.factory_expires_at = None
        session.busy = False

    async def _clear_controls(self, chat_id: int) -> None:
        """Remove control messages without discarding an active factory flow."""
        assert self._client is not None
        session = self._session(chat_id)
        for mid in list(session.control_ids):
            await self._client.delete_message(chat_id, mid)
        session.control_ids.clear()

    async def _flash(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        also_delete: list[int | None] | None = None,
        delay: float = _BURN_EPHEMERAL,
        parse_mode: str | None = None,
    ) -> None:
        """Show a tip, then delete it after ``delay`` (varies by message kind)."""
        msg = await self._say(
            chat_id,
            text,
            reply_markup=reply_markup,
            track=True,
            parse_mode=parse_mode,
        )
        self._spawn(
            self._burn_after(
                chat_id,
                [msg.get("message_id"), *(also_delete or [])],
                delay,
            )
        )

    async def _burn_after(
        self,
        chat_id: int,
        message_ids: list[int | None],
        delay: float,
    ) -> None:
        await asyncio.sleep(delay)
        await self._forget(chat_id, *message_ids)

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    # -- poll ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Never block the poll loop on uploads — dispatch each update as a task."""
        assert self._client is not None
        while not self._stop.is_set():
            try:
                updates = await self._client.get_updates(
                    offset=self._offset, timeout=25
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Telegram getUpdates error: %s", exc)
                await asyncio.sleep(3)
                continue
            for update in updates:
                uid = update.get("update_id")
                if isinstance(uid, int):
                    self._offset = uid + 1
                self._spawn(self._dispatch_safe(update))

    async def _dispatch_safe(self, update: dict[str, Any]) -> None:
        try:
            await self._dispatch(update)
        except Exception:
            logger.exception("Telegram update handler failed")

    async def _dispatch(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            await self._on_callback(update["callback_query"])
            return
        message = update.get("message")
        if isinstance(message, dict):
            await self._on_message(message)

    async def _on_message(self, message: dict[str, Any]) -> None:
        assert self._client is not None
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        user = message.get("from") or {}
        user_id = user.get("id")
        text = (message.get("text") or "").strip()
        mid = message.get("message_id")
        if not isinstance(chat_id, int) or not isinstance(user_id, int):
            return

        role = self._role(user_id)
        if role is None:
            if isinstance(mid, int):
                await self._client.delete_message(chat_id, mid)
            return

        session = self._session(chat_id)
        if isinstance(mid, int):
            session.control_ids.add(mid)

        if text in {"/start", "/help"} or _command_name(text) in {
            "start",
            "help",
        }:
            await self._flash(
                chat_id,
                render_start_guide(),
                reply_markup=_remove_keyboard(),
                also_delete=[mid],
                delay=_BURN_GUIDE,
                parse_mode="HTML",
            )
            return

        cmd = _command_name(text)
        if cmd in _ADMIN_CMDS:
            if role != "admin":
                await self._flash(
                    chat_id,
                    "仅管理员可用此命令。",
                    also_delete=[mid],
                    delay=_BURN_EPHEMERAL,
                )
                return
            await self._handle_admin_command(chat_id, cmd, user_mid=mid)
            return

        # Legacy reply-keyboard button labels → same as commands, then strip KB.
        legacy = {
            "数据库统计": "stats",
            "数据库同步": "sync",
            "系统运行概况": "status",
            "恢复出厂设置": "factory",
        }
        if text in legacy:
            if role != "admin":
                await self._flash(
                    chat_id,
                    "仅管理员可用此命令。",
                    reply_markup=_remove_keyboard(),
                    also_delete=[mid],
                    delay=_BURN_EPHEMERAL,
                )
                return
            await self._handle_admin_command(chat_id, legacy[text], user_mid=mid)
            return

        if (
            role == "admin"
            and session.factory_mode is FactoryResetMode.FULL
            and session.factory_stage == 2
        ):
            await self._handle_factory_code(chat_id, text)
            return

        if session.busy or session.lock.locked():
            tip = await self._say(chat_id, "请稍候，当前操作尚未结束。")
            self._spawn(
                self._burn_after(
                    chat_id, [tip.get("message_id"), mid], _BURN_EPHEMERAL
                )
            )
            return

        await self._handle_input(chat_id, user_id, role, text)

    async def _handle_admin_command(
        self,
        chat_id: int,
        cmd: str,
        *,
        user_mid: int | None,
    ) -> None:
        try:
            if cmd == "stats":
                body = await self._report_stats()
                delay = _BURN_REPORT
            elif cmd == "sync":
                body = await self._report_sync()
                delay = _BURN_SYNC_ACK
            elif cmd == "status":
                body = await self._report_status()
                delay = _BURN_REPORT
            elif cmd == "factory":
                await self._start_factory_menu(chat_id)
                return
            else:
                body = "未知命令。"
                delay = _BURN_EPHEMERAL
        except Exception:
            logger.exception("Telegram admin command failed: %s", cmd)
            body = "命令执行失败，请稍后再试。"
            delay = _BURN_EPHEMERAL

        await self._flash(
            chat_id,
            body,
            reply_markup=_remove_keyboard(),
            also_delete=[user_mid],
            delay=delay,
            parse_mode="HTML",
        )

    async def _report_stats(self) -> str:
        engine = self._db_engine
        if engine is None:
            return "统计不可用：数据库未就绪。"
        prefs = self._preferences.effective()
        stats = await asyncio.to_thread(
            collect_library_stats,
            engine=engine,
            subscriptions=self._subscriptions,
            catalog=self._catalog,
            data_path=self._data_path,
            direct_folder=prefs.direct_folder,
        )
        return render_stats(stats)

    async def _report_sync(self) -> str:
        if self._scheduler is None:
            return "同步不可用：调度器未就绪。"
        try:
            job_ids = await asyncio.to_thread(self._scheduler.sync_all)
        except Exception as exc:
            logger.exception("Telegram /sync failed")
            return f"同步启动失败：{type(exc).__name__}"
        return render_sync_ack(len(job_ids))

    async def _report_status(self) -> str:
        engine = self._db_engine
        if engine is None:
            return "概况不可用：数据库未就绪。"
        prefs = self._preferences.effective()
        jobs_active = sum(
            1 for job in self._job_store.get_all() if not job.status.is_finished
        )
        healthy: bool | None = None
        if self._library_health is not None:
            try:
                healthy = bool(self._library_health.check().ok)
            except Exception:
                healthy = None
        status = await asyncio.to_thread(
            collect_runtime_status,
            engine=engine,
            started_at=self._started_at,
            scheduler_enabled=bool(prefs.scheduler_enabled),
            jobs_active=jobs_active,
            library_healthy=healthy,
        )
        return render_status(status)

    async def _start_factory_menu(self, chat_id: int) -> None:
        await self._cleanup(chat_id)
        await self._say(
            chat_id,
            "\n".join(
                [
                    "恢复与清理",
                    "",
                    "1. 恢复默认偏好",
                    "保留歌单、文件、账号、Cookie 与 TG 配置。",
                    "",
                    "2. 清理无效数据",
                    "清理 Yubal 管理范围内无 ID 且未验证的数据；"
                    "外部原始曲库保留。",
                    "",
                    "3. 完全恢复出厂",
                    "永久清除全部音乐、列表、备份、Cookie、账号与 TG 配置。",
                ]
            ),
            reply_markup=_inline(
                [
                    [("1 · 恢复偏好", "fr:1")],
                    [("2 · 清理无效数据", "fr:2")],
                    [("3 · 完全恢复出厂", "fr:3")],
                    [("取消", "x")],
                ]
            ),
        )

    @staticmethod
    def _factory_counts(preview: FactoryResetPreview) -> str:
        size_mib = preview.bytes / (1024 * 1024)
        return (
            f"列表记录 {preview.list_entries} · "
            f"真实文件 {preview.files} · "
            f"路径 {preview.paths} · "
            f"{size_mib:.1f} MiB"
        )

    async def _factory_preview(
        self,
        chat_id: int,
        mode: FactoryResetMode,
    ) -> FactoryResetPreview | None:
        if self._factory_reset is None:
            await self._say(chat_id, "恢复服务暂不可用。")
            await self._cleanup(chat_id)
            return None
        preview = await asyncio.to_thread(self._factory_reset.preview, mode)
        session = self._session(chat_id)
        session.factory_mode = mode
        session.factory_token = preview.token
        session.factory_stage = 1
        session.factory_expires_at = datetime.now(UTC) + timedelta(
            seconds=preview.expires_in_seconds
        )
        return preview

    async def _handle_factory_callback(
        self,
        chat_id: int,
        role: str,
        data: str,
    ) -> None:
        if role != "admin":
            await self._say(chat_id, "无权限。")
            await self._cleanup(chat_id)
            return
        session = self._session(chat_id)

        if data in {"fr:1", "fr:2", "fr:3"}:
            await self._clear_controls(chat_id)
            mode = {
                "fr:1": FactoryResetMode.PREFERENCES,
                "fr:2": FactoryResetMode.INVALID,
                "fr:3": FactoryResetMode.FULL,
            }[data]
            preview = await self._factory_preview(chat_id, mode)
            if preview is None:
                return
            if mode is FactoryResetMode.PREFERENCES:
                await self._say(
                    chat_id,
                    "确认恢复默认偏好？\n不会删除歌单、文件或账号。",
                    reply_markup=_inline(
                        [[("确认恢复", "fr1:go"), ("取消", "x")]]
                    ),
                )
            elif mode is FactoryResetMode.INVALID:
                await self._say(
                    chat_id,
                    "\n".join(
                        [
                            "第一次确认：清理无效数据",
                            self._factory_counts(preview),
                            "",
                            "只处理无 YTM ID 且未通过标签验证的数据；"
                            "外部原始文件保留。",
                        ]
                    ),
                    reply_markup=_inline(
                        [[("继续", "fr2:next"), ("取消", "x")]]
                    ),
                )
            else:
                await self._say(
                    chat_id,
                    "\n".join(
                        [
                            "第一次确认：完全恢复出厂",
                            self._factory_counts(preview),
                            f"数据库备份 {preview.backups}",
                            "",
                            "所有音乐、外部原始文件、列表、备份、Cookie、"
                            "Web 账号与 TG 配置都将永久删除。",
                        ]
                    ),
                    reply_markup=_inline(
                        [[("我已了解，继续", "fr3:next"), ("取消", "x")]]
                    ),
                )
            return

        if not session.factory_token or session.factory_mode is None:
            await self._say(chat_id, "确认已过期，请重新输入 /factory。")
            await self._cleanup(chat_id)
            return
        if (
            session.factory_expires_at is None
            or session.factory_expires_at <= datetime.now(UTC)
        ):
            await self._say(chat_id, "确认已过期，请重新输入 /factory。")
            await self._cleanup(chat_id)
            return

        if data == "fr1:go" and session.factory_mode is FactoryResetMode.PREFERENCES:
            await self._execute_factory_from_telegram(chat_id)
            return
        if data == "fr2:next" and session.factory_mode is FactoryResetMode.INVALID:
            session.factory_stage = 2
            await self._clear_controls(chat_id)
            await self._say(
                chat_id,
                "第二次确认：删除后无法在 Yubal 中恢复这些无效数据。",
                reply_markup=_inline(
                    [[("确认清理", "fr2:go"), ("取消", "x")]]
                ),
            )
            return
        if data == "fr2:go" and session.factory_mode is FactoryResetMode.INVALID:
            await self._execute_factory_from_telegram(chat_id)
            return
        if data == "fr3:next" and session.factory_mode is FactoryResetMode.FULL:
            session.factory_stage = 2
            session.factory_code = f"{secrets.randbelow(1_000_000):06d}"
            await self._clear_controls(chat_id)
            await self._say(
                chat_id,
                "\n".join(
                    [
                        "第二次确认：此操作不可恢复。",
                        f"请在 5 分钟内发送验证码：{session.factory_code}",
                        "发送其他内容不会执行删除。",
                    ]
                ),
            )
            return

        await self._say(chat_id, "确认状态无效，请重新输入 /factory。")
        await self._cleanup(chat_id)

    async def _handle_factory_code(self, chat_id: int, text: str) -> None:
        session = self._session(chat_id)
        if (
            session.factory_mode is not FactoryResetMode.FULL
            or session.factory_stage != 2
            or not session.factory_code
        ):
            return
        if (
            session.factory_expires_at is None
            or session.factory_expires_at <= datetime.now(UTC)
        ):
            await self._say(chat_id, "验证码已过期，请重新输入 /factory。")
            await self._cleanup(chat_id)
            return
        if not secrets.compare_digest(text, session.factory_code):
            await self._flash(
                chat_id,
                "验证码不正确，未执行删除。",
                delay=_BURN_EPHEMERAL,
            )
            return
        await self._execute_factory_from_telegram(chat_id, authorized=True)

    async def _execute_factory_from_telegram(
        self,
        chat_id: int,
        *,
        authorized: bool = False,
    ) -> None:
        session = self._session(chat_id)
        mode = session.factory_mode
        token = session.factory_token
        if self._factory_reset is None or mode is None or not token:
            await self._say(chat_id, "确认已过期，请重新输入 /factory。")
            await self._cleanup(chat_id)
            return
        await self._clear_controls(chat_id)
        await self._say(chat_id, "正在执行，请勿重复操作。", track=False)
        try:
            await asyncio.to_thread(
                self._factory_reset.execute,
                mode,
                token,
                authorized=authorized,
            )
        except (PermissionError, ValueError):
            await self._say(chat_id, "确认已失效，未执行删除。", track=False)
            await self._cleanup(chat_id)
            return

        await self._say(
            chat_id,
            (
                "完全恢复出厂已完成，Web 端需要重新注册。"
                if mode is FactoryResetMode.FULL
                else "操作已完成。"
            ),
            track=False,
        )
        if mode is FactoryResetMode.FULL:
            self.clear_factory_state()
            asyncio.create_task(self._reload_after_factory())
        else:
            await self._cleanup(chat_id)

    async def _reload_after_factory(self) -> None:
        await asyncio.sleep(1)
        await self.reload()

    # -- input routing ---------------------------------------------------------

    async def _handle_input(
        self, chat_id: int, user_id: int, role: str, text: str
    ) -> None:
        session = self._session(chat_id)
        async with session.lock:
            session.busy = True
            # Wipe leftover control bubbles from earlier flows; keep this user msg.
            if session.preview_msg_id is not None and self._client is not None:
                await self._client.delete_message(chat_id, session.preview_msg_id)
                session.preview_msg_id = None
            prior = list(session.control_ids)
            session.control_ids.clear()
            if prior and self._client is not None:
                latest = prior[-1]
                for mid in prior[:-1]:
                    await self._client.delete_message(chat_id, mid)
                session.control_ids.add(latest)
            session.query = ""
            session.online_ids = []
            session.pending_url = ""
            try:
                if not text:
                    await self._say(chat_id, "请发送链接或歌名。")
                    await self._cleanup(chat_id)
                    return

                if is_supported_url(text):
                    await self._handle_url(chat_id, user_id, role, text)
                    return

                # Reject obvious non-YouTube URLs / control chars like the web UI.
                if re.match(r"^[a-z][a-z\d+.-]*://", text, re.I) or re.match(
                    r"^(?:www\.)?[\w-]+(?:\.[\w-]+)+(?:[/:?#]|$)", text, re.I
                ):
                    await self._say(chat_id, "不支持的链接。")
                    await self._cleanup(chat_id)
                    return
                if len(text) > 200 or any(ord(c) < 32 for c in text):
                    await self._say(chat_id, "无效输入。")
                    await self._cleanup(chat_id)
                    return

                await self._handle_text(chat_id, user_id, role, text)
            except Exception:
                logger.exception("Telegram input handling failed")
                try:
                    await self._say(chat_id, "处理失败，已取消。")
                except Exception:
                    pass
                await self._cleanup(chat_id)
            finally:
                session.busy = False

    async def _handle_url(
        self, chat_id: int, user_id: int, role: str, url: str
    ) -> None:
        info = await asyncio.to_thread(self._playlist_info.get_content_info, url)
        track_count = max(1, info.track_count or 1)
        is_single = info.kind == ContentKind.TRACK or track_count == 1

        if is_single:
            video_id = info.playlist_id or parse_video_id(url) or ""
            if video_id:
                presence = await asyncio.to_thread(
                    self._lookup.lookup_track, video_id
                )
                options = self._local_options_for_video(
                    video_id,
                    title=presence.title or info.title or video_id,
                    artist=presence.artist or "Unknown",
                )
                if options or presence.in_direct or presence.locations:
                    await self._offer_local(
                        chat_id, user_id, role, url, video_id, options
                    )
                    return
            if role == "admin":
                await self._direct_download_and_send(chat_id, url, video_id)
            else:
                await self._say(chat_id, "本地无此曲，普通用户不能入库下载。")
                await self._cleanup(chat_id)
            return

        # Multi-track: presence only, never push files.
        playlist = await asyncio.to_thread(self._lookup.lookup_playlist, url)
        if playlist.subscription:
            name = playlist.subscription.title
            note = (
                f"已订阅「{name}」（已关闭）"
                if playlist.subscription.enabled is False
                else f"已订阅「{name}」"
            )
            await self._say(chat_id, note)
            await self._cleanup(chat_id)
            return
        if playlist.in_direct_url:
            await self._say(chat_id, "该链接已在下载中心。")
            await self._cleanup(chat_id)
            return
        if role != "admin":
            await self._say(chat_id, "未订阅。普通用户不能添加订阅。")
            await self._cleanup(chat_id)
            return
        session = self._session(chat_id)
        session.pending_url = url
        await self._say(
            chat_id,
            f"未订阅（约 {track_count} 首）。是否加入订阅？",
            reply_markup=_inline(
                [[("加入订阅", "sub:y"), ("取消", "x")]]
            ),
        )

    async def _handle_text(
        self, chat_id: int, user_id: int, role: str, query: str
    ) -> None:
        local = await asyncio.to_thread(self._lookup.lookup_text, query, limit=8)
        if local.matches:
            rows: list[list[tuple[str, str]]] = []
            for match in local.matches[:5]:
                options = self._local_options_for_video(
                    match.video_id,
                    title=match.title,
                    artist=match.artist,
                )
                opt = options[0] if options else None
                label = f"{match.artist} - {match.title}"
                if opt and opt.playlist_count > 1:
                    label = f"{label} · {opt.playlist_count}X"
                if len(label) > 60:
                    label = label[:57] + "…"
                rows.append([(label, f"pick:{match.video_id}")])
            rows.append([("在线搜索", "onl"), ("取消", "x")])
            self._session(chat_id).query = query
            await self._say(
                chat_id,
                "本地命中，请选择：",
                reply_markup=_inline(rows),
            )
            return
        await self._start_online(chat_id, user_id, role, query)

    # -- local options ---------------------------------------------------------

    def _local_options_for_video(
        self, video_id: str, *, title: str, artist: str
    ) -> list[LocalOption]:
        """Deduplicate hardlinked copies by inode; one button per physical file."""
        locations = self._catalog.list_locations_for_video(video_id)
        by_inode: dict[tuple[int, int], LocalOption] = {}
        folder_names: dict[tuple[int, int], set[str]] = {}

        canonical = self._catalog.resolve_canonical_path(video_id)
        for loc in locations:
            abs_path = location_abs_path(loc, download_root=self._data_path)
            if not abs_path.is_file():
                continue
            try:
                st = abs_path.stat()
                key = (st.st_dev, st.st_ino)
            except OSError:
                continue
            folder_names.setdefault(key, set()).add(loc.save_folder)
            if key not in by_inode:
                by_inode[key] = LocalOption(
                    video_id=video_id,
                    title=title,
                    artist=artist,
                    path=abs_path,
                    playlist_count=1,
                )

        if not by_inode and canonical and canonical.is_file():
            try:
                st = canonical.stat()
                key = (st.st_dev, st.st_ino)
            except OSError:
                key = (0, 0)
            by_inode[key] = LocalOption(
                video_id=video_id,
                title=title,
                artist=artist,
                path=canonical,
                playlist_count=1,
            )

        out: list[LocalOption] = []
        for key, opt in by_inode.items():
            count = len(folder_names.get(key, {opt.path.parent.name}))
            out.append(
                LocalOption(
                    video_id=opt.video_id,
                    title=opt.title,
                    artist=opt.artist,
                    path=opt.path,
                    playlist_count=max(1, count),
                )
            )
        return out

    async def _offer_local(
        self,
        chat_id: int,
        user_id: int,
        role: str,
        url: str,
        video_id: str,
        options: list[LocalOption],
    ) -> None:
        rows: list[list[tuple[str, str]]] = []
        for opt in options[:5]:
            label = f"{opt.artist} - {opt.title}"
            if opt.playlist_count > 1:
                label = f"{label} · {opt.playlist_count}X"
            if len(label) > 60:
                label = label[:57] + "…"
            rows.append([(label, f"loc:{video_id}")])
        if role == "admin":
            rows.append([("加入下载中心", f"jd:{video_id}")])
        rows.append([("在线搜索", "onl"), ("取消", "x")])
        self._session(chat_id).query = url
        self._session(chat_id).pending_url = url
        await self._say(
            chat_id,
            "本地已有，请选择：",
            reply_markup=_inline(rows),
        )

    # -- online / preview ------------------------------------------------------

    async def _start_online(
        self, chat_id: int, user_id: int, role: str, query: str
    ) -> None:
        prefs = self._preferences.effective()
        limit = prefs.telegram_daily_limit
        if role != "admin" and not self._quota.consume(user_id, limit):
            await self._say(
                chat_id,
                f"今日在线搜索/试听已达上限（{limit}）。",
            )
            await self._cleanup(chat_id)
            return

        snapshot = await asyncio.to_thread(self._search.search, query)
        if snapshot is None or not snapshot.tracks:
            await self._say(chat_id, "在线无结果。")
            await self._cleanup(chat_id)
            return

        ytm = [t for t in snapshot.tracks if (t.result_kind or "ytm") == "ytm" and t.video_id][
            :3
        ]
        meta = [t for t in snapshot.tracks if t.wishable or (t.result_kind or "") == "meta"][
            :3
        ]
        tracks = ytm + meta
        if not tracks:
            await self._say(chat_id, "在线无结果。")
            await self._cleanup(chat_id)
            return

        session = self._session(chat_id)
        session.query = query
        session.online_ids = []
        session.online_kinds = []
        session.online_meta = []
        rows: list[list[tuple[str, str]]] = []
        for idx, track in enumerate(tracks):
            kind = "meta" if (track.wishable or track.result_kind == "meta") else "ytm"
            label = f"{track.artist} - {track.title}"
            if kind == "meta":
                src = (track.source or "meta").upper()
                label = f"♡ [{src}] {label}"
            if len(label) > 60:
                label = label[:57] + "…"
            session.online_kinds.append(kind)
            if kind == "ytm":
                session.online_ids.append(track.video_id)
                session.online_meta.append({})
                rows.append([(label, f"os:{idx}")])
            else:
                session.online_ids.append("")
                session.online_meta.append(
                    {
                        "title": track.title,
                        "artists": track.artist,
                        "album": track.album or "",
                        "source": track.source or "manual",
                        "source_id": track.source_id or "",
                        "source_url": track.source_url,
                        "thumbnail_url": track.thumbnail_url,
                        "duration_seconds": track.duration_seconds,
                    }
                )
                rows.append([(label, f"ow:{idx}")])
        rows.append([("取消", "x")])
        await self._say(
            chat_id,
            "在线结果（前3 YTM / 后3 心愿）：",
            reply_markup=_inline(rows),
        )

    # -- callbacks -------------------------------------------------------------

    async def _on_callback(self, callback: dict[str, Any]) -> None:
        assert self._client is not None
        data = (callback.get("data") or "").strip()
        cb_id = callback.get("id") or ""
        message = callback.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        user = callback.get("from") or {}
        user_id = user.get("id")
        mid = message.get("message_id")
        if not isinstance(chat_id, int) or not isinstance(user_id, int):
            return

        role = self._role(user_id)
        if role is None:
            await self._client.answer_callback(cb_id, text="无权限")
            return

        if isinstance(mid, int):
            self._session(chat_id).control_ids.add(mid)

        session = self._session(chat_id)
        cancelling = data == "x" or data.startswith("ds:")
        # Atomic under asyncio: no await between busy check and set.
        if not cancelling and session.busy:
            await self._client.answer_callback(cb_id, text="请稍候…")
            return
        if not cancelling:
            session.busy = True
        await self._client.answer_callback(cb_id, text=_callback_toast(data))

        async with session.lock:
            try:
                await self._handle_callback(chat_id, user_id, role, data)
            except Exception:
                delivery = data.startswith(("pick:", "loc:"))
                if delivery:
                    logger.exception("Telegram audio delivery failed: %s", data)
                else:
                    logger.exception("Telegram callback failed: %s", data)
                try:
                    await self._say(
                        chat_id,
                        "音频发送失败，请稍后重试。" if delivery else "操作失败。",
                    )
                except Exception:
                    pass
                await self._cleanup(chat_id)
            finally:
                session.busy = False

    async def _handle_callback(
        self, chat_id: int, user_id: int, role: str, data: str
    ) -> None:
        session = self._session(chat_id)

        if data.startswith("fr"):
            await self._handle_factory_callback(chat_id, role, data)
            return

        if data == "x":
            if session.preview_msg_id is not None and self._client:
                await self._client.delete_message(chat_id, session.preview_msg_id)
            await self._cleanup(chat_id)
            return

        if data == "onl":
            query = session.query or session.pending_url
            if not query:
                await self._cleanup(chat_id)
                return
            tip = await self._say(chat_id, "正在搜索…")
            if is_supported_url(query):
                vid = parse_video_id(query)
                query = vid or query
            try:
                await self._start_online(chat_id, user_id, role, query)
            finally:
                await self._forget(chat_id, tip.get("message_id"))
            return

        if data.startswith("jd:"):
            if role != "admin":
                await self._say(chat_id, "无权限。")
                await self._cleanup(chat_id)
                return
            video_id = data[3:]
            url = session.pending_url or f"https://music.youtube.com/watch?v={video_id}"
            await self._direct_download_and_send(chat_id, url, video_id)
            return

        if data.startswith("os:"):
            try:
                idx = int(data[3:])
            except ValueError:
                await self._cleanup(chat_id)
                return
            if idx < 0 or idx >= len(session.online_ids):
                await self._cleanup(chat_id)
                return
            if idx < len(session.online_kinds) and session.online_kinds[idx] != "ytm":
                await self._cleanup(chat_id)
                return
            video_id = session.online_ids[idx]
            if not video_id:
                await self._cleanup(chat_id)
                return
            rows = [[("试听", f"pv:{video_id}")]]
            if role == "admin":
                rows[0].append(("立即下载", f"dl:{video_id}"))
            rows.append([("取消", "x")])
            await self._say(
                chat_id,
                "请选择操作：",
                reply_markup=_inline(rows),
            )
            return

        if data.startswith("ow:"):
            try:
                idx = int(data[3:])
            except ValueError:
                await self._cleanup(chat_id)
                return
            if idx < 0 or idx >= len(session.online_meta):
                await self._cleanup(chat_id)
                return
            payload = session.online_meta[idx] or {}
            if self._wanted is None:
                await self._say(chat_id, "心愿歌单不可用。")
                await self._cleanup(chat_id)
                return
            from yubal_api.schemas.wanted import WantedAddRequest

            try:
                await asyncio.to_thread(
                    self._wanted.add,
                    WantedAddRequest(
                        title=str(payload.get("title") or ""),
                        artists=str(payload.get("artists") or ""),
                        album=str(payload.get("album") or ""),
                        source=str(payload.get("source") or "manual"),
                        source_id=str(payload.get("source_id") or ""),
                        source_url=payload.get("source_url"),
                        thumbnail_url=payload.get("thumbnail_url"),
                        duration_seconds=payload.get("duration_seconds"),
                    ),
                )
                await self._say(chat_id, "已加入心愿歌单。")
            except ValueError as exc:
                await self._say(chat_id, f"加入失败：{exc}")
            except Exception:
                logger.exception("Telegram add wanted failed")
                await self._say(chat_id, "加入心愿失败。")
            await self._cleanup(chat_id)
            return

        if data.startswith("pv:"):
            await self._preview(chat_id, user_id, role, data[3:])
            return

        if data.startswith("dl:"):
            if role != "admin":
                await self._say(chat_id, "无权限。")
                await self._cleanup(chat_id)
                return
            video_id = data[3:]
            url = f"https://music.youtube.com/watch?v={video_id}"
            await self._direct_download_and_send(chat_id, url, video_id)
            return

        if data.startswith("sv:"):
            if role != "admin":
                await self._say(chat_id, "无权限。")
                await self._cleanup(chat_id)
                return
            video_id = data[3:]
            await asyncio.to_thread(self._search.promote_preview, video_id)
            session.preview_msg_id = None
            tip = await self._say(chat_id, "已保存。")
            self._spawn(self._burn_after(chat_id, [tip.get("message_id")], _BURN_FLOW))
            await self._cleanup(chat_id)
            return

        if data.startswith("ds:"):
            if session.preview_msg_id is not None and self._client:
                await self._client.delete_message(chat_id, session.preview_msg_id)
                session.preview_msg_id = None
            await self._cleanup(chat_id)
            return

        if data == "sub:y":
            if role != "admin":
                await self._say(chat_id, "无权限。")
                await self._cleanup(chat_id)
                return
            url = session.pending_url
            if not url or not PLAYLIST_ID_PATTERN.search(url):
                await self._say(chat_id, "无效订阅链接。")
                await self._cleanup(chat_id)
                return
            await asyncio.to_thread(self._subscriptions.create, url, None)
            tip = await self._say(chat_id, "已加入订阅。")
            self._spawn(self._burn_after(chat_id, [tip.get("message_id")], _BURN_FLOW))
            await self._cleanup(chat_id)
            return

        if data.startswith("pick:"):
            video_id = data[5:]
            tip = await self._say(chat_id, "正在发送…")
            url = f"https://music.youtube.com/watch?v={video_id}"
            presence = await asyncio.to_thread(self._lookup.lookup_track, video_id)
            options = self._local_options_for_video(
                video_id,
                title=presence.title or video_id,
                artist=presence.artist or "Unknown",
            )
            if options:
                try:
                    await self._send_local_file(chat_id, options[0])
                finally:
                    await self._forget(chat_id, tip.get("message_id"))
                await self._cleanup(chat_id)
                return
            await self._forget(chat_id, tip.get("message_id"))
            if role == "admin":
                await self._direct_download_and_send(chat_id, url, video_id)
            else:
                await self._say(chat_id, "本地文件缺失。")
                await self._cleanup(chat_id)
            return

        if data.startswith("loc:"):
            video_id = data[4:]
            tip = await self._say(chat_id, "正在发送…")
            presence = await asyncio.to_thread(self._lookup.lookup_track, video_id)
            options = self._local_options_for_video(
                video_id,
                title=presence.title or video_id,
                artist=presence.artist or "Unknown",
            )
            if not options:
                await self._forget(chat_id, tip.get("message_id"))
                await self._say(chat_id, "文件不存在。")
                await self._cleanup(chat_id)
                return
            try:
                await self._send_local_file(chat_id, options[0])
            finally:
                await self._forget(chat_id, tip.get("message_id"))
            await self._cleanup(chat_id)
            return

    # -- send helpers ----------------------------------------------------------

    async def _send_local_file(self, chat_id: int, opt: LocalOption) -> None:
        assert self._sender is not None
        await self._sender.send(
            chat_id,
            video_id=opt.video_id,
            path=opt.path,
            title=opt.title,
            performer=opt.artist,
            remember=True,
        )

    async def _preview(
        self, chat_id: int, user_id: int, role: str, video_id: str
    ) -> None:
        assert self._sender is not None and self._client is not None
        prefs = self._preferences.effective()
        if role != "admin":
            if not self._quota.consume(user_id, prefs.telegram_daily_limit):
                await self._say(chat_id, "今日试听次数已用完。")
                await self._cleanup(chat_id)
                return

        tip = await self._say(chat_id, "正在准备试听…")
        result: dict[str, Any] = {}
        try:
            path = await asyncio.to_thread(self._search.prepare_preview, video_id)
            snapshot = await asyncio.to_thread(self._search.current)
            track = None
            if snapshot:
                track = next(
                    (t for t in snapshot.tracks if t.video_id == video_id), None
                )
            title = track.title if track else video_id
            artist = track.artist if track else None

            result = await self._sender.send(
                chat_id,
                video_id=f"preview:{video_id}",
                path=path,
                title=title,
                performer=artist,
                remember=True,
            )
        finally:
            await self._forget(chat_id, tip.get("message_id"))

        mid = result.get("message_id")
        session = self._session(chat_id)
        if isinstance(mid, int):
            session.preview_msg_id = mid
            file_id, kind = extract_delivery(result)
            if file_id and kind in {"audio", "document"}:
                self._file_ids.put(video_id, file_id, kind=kind)

        rows: list[list[tuple[str, str]]] = []
        if role == "admin":
            rows.append([("保存", f"sv:{video_id}"), ("丢弃", f"ds:{video_id}")])
        else:
            rows.append([("丢弃", f"ds:{video_id}")])
        await self._say(
            chat_id,
            "试听已发送。",
            reply_markup=_inline(rows),
        )

    async def _direct_download_and_send(
        self, chat_id: int, url: str, video_id: str
    ) -> None:
        assert self._sender is not None
        wait = await self._say(chat_id, "正在下载…")
        job = self._jobs.create_and_start_job(url, None)
        if job is None:
            await self._say(chat_id, "任务队列已满。")
            await self._cleanup(chat_id)
            return

        for _ in range(600):
            current = self._job_store.get(job.id)
            if current is None or current.status.is_finished:
                break
            await asyncio.sleep(1)

        from yubal_api.domain.enums import JobStatus

        current = self._job_store.get(job.id)
        if current is None or current.status != JobStatus.COMPLETED:
            await self._say(chat_id, "下载失败。")
            await self._cleanup(chat_id)
            return

        vid = video_id or parse_video_id(url) or ""
        path = self._catalog.resolve_canonical_path(vid) if vid else None
        if path is None and vid:
            options = self._local_options_for_video(
                vid, title=vid, artist="Unknown"
            )
            path = options[0].path if options else None
        if path is None:
            await self._say(chat_id, "下载完成但找不到文件。")
            await self._cleanup(chat_id)
            return

        presence = await asyncio.to_thread(self._lookup.lookup_track, vid)
        send_tip = await self._say(chat_id, "正在发送…")
        try:
            await self._sender.send(
                chat_id,
                video_id=vid,
                path=path,
                title=presence.title,
                performer=presence.artist,
                remember=True,
            )
        finally:
            await self._forget(
                chat_id, wait.get("message_id"), send_tip.get("message_id")
            )
        await self._cleanup(chat_id)
