"""Persisted runtime preferences (user-editable via Settings UI).

UI values in preferences.json override environment / Settings defaults.
Keys absent from the file fall back to env-backed defaults.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from croniter import croniter
from yubal import AudioCodec
from yubal.utils.library import DIRECT_FOLDER, sanitize_direct_folder

logger = logging.getLogger(__name__)

DEFAULT_MIN_FREE_GB = 2.0
DOWNLOAD_CACHE_ROOT = Path("/data/cache")
BYTES_PER_GIB = 1024**3

AUDIO_FORMATS = frozenset({"opus", "mp3", "m4a"})
PRESELECT_PLACE_MODES = frozenset({"link", "copy"})
PRESELECT_MATCH_MODES = frozenset({"loose", "standard", "strict"})
MATCH_STRICTNESS_MODES = frozenset({"strict", "relaxed"})
TRACK_SORT_KEYS = frozenset({"title", "artist", "album"})
DEFAULT_INDEX_THRESHOLD = 50


@dataclass(frozen=True)
class DiskStatus:
    """Free space snapshot for the music library mount."""

    path: Path
    free_bytes: int
    min_free_gb: float

    @property
    def free_gb(self) -> float:
        return self.free_bytes / BYTES_PER_GIB

    @property
    def min_free_bytes(self) -> int:
        return int(self.min_free_gb * BYTES_PER_GIB)

    @property
    def enough_space(self) -> bool:
        if self.min_free_gb <= 0:
            return True
        return self.free_bytes >= self.min_free_bytes


@dataclass(frozen=True)
class Preferences:
    """Effective preferences (env defaults + JSON overrides)."""

    min_free_gb: float = DEFAULT_MIN_FREE_GB
    direct_folder: str = DIRECT_FOLDER
    direct_download_limit: int = 50
    # Direct recover policy (subscription-like; separate from one-shot download limit).
    direct_auto_recover_enabled: bool = False
    direct_max_items: int = 100
    direct_sync_jitter_seconds: int = 600
    direct_offline_marking_enabled: bool = True
    direct_offline_cleanup_enabled: bool = False
    # archive = move to Raw/Delete; delete = unlink files.
    direct_offline_cleanup_action: str = "archive"
    direct_offline_cleanup_delay_hours: int = 72
    index_threshold: int = DEFAULT_INDEX_THRESHOLD
    track_sort_key: str = "title"
    search_result_ttl_hours: int = 24
    audio_format: str = "mp3"
    audio_quality: int = 0
    fetch_lyrics: bool = True
    ytmusic_lyrics_fallback: bool = True
    qq_lyrics_fallback: bool = True
    scrape_cooldown_hours: int = 24
    download_ugc: bool = False
    replaygain: bool = True
    scheduler_enabled: bool = True
    scheduler_cron: str = "0 * * * *"
    job_timeout_seconds: int = 1800
    external_library_enabled: bool = False
    preselect_enabled: bool = False
    wash_enabled: bool = False
    preselect_root: str = ""
    preselect_place_mode: str = "link"
    preselect_match_mode: str = "standard"
    download_cache_enabled: bool = False
    cache_min_free_gb: float = DEFAULT_MIN_FREE_GB
    # External library: cap for the exponential match-retry backoff (days).
    match_backoff_cap_days: int = 7
    # External library YTM match: strict rejects Live/DJ↔studio; relaxed allows.
    match_strictness: str = "strict"
    # Min cover edge (px) for permanent premium; 0 = shelf-life only.
    cover_excellence_px: int = 0
    # Cover comparison shelf life (days): probe rounds vs full downloads.
    cover_probe_fresh_days: int = 7
    cover_download_fresh_days: int = 30
    # Telegram bot (B2). Empty token = disabled.
    telegram_bot_token: str = ""
    telegram_admin_ids: str = ""
    telegram_user_ids: str = ""
    telegram_daily_limit: int = 5
    # Wishlist / wanted playlist
    wanted_enabled: bool = True
    wanted_auto_match_enabled: bool = True
    wanted_max_items: int = 50
    wanted_sync_jitter_seconds: int = 600
    wanted_source_musicbrainz: bool = True
    wanted_source_qq: bool = True
    wanted_source_discogs: bool = False
    wanted_source_lastfm: bool = False
    lastfm_api_key: str = ""


def preferences_from_settings(settings: Any) -> Preferences:
    """Build env-backed defaults from the process Settings object."""
    quality_raw = settings.audio_quality
    try:
        quality = int(quality_raw)
    except (TypeError, ValueError):
        quality = 0
    fmt = settings.audio_format
    audio_format = fmt.value if isinstance(fmt, AudioCodec) else str(fmt)
    try:
        job_timeout = int(settings.job_timeout_seconds)
    except (TypeError, ValueError):
        job_timeout = 1800
    try:
        scrape_cooldown = int(getattr(settings, "scrape_cooldown_hours", 24))
    except (TypeError, ValueError):
        scrape_cooldown = 24
    return Preferences(
        min_free_gb=DEFAULT_MIN_FREE_GB,
        direct_folder=DIRECT_FOLDER,
        direct_download_limit=50,
        direct_auto_recover_enabled=False,
        direct_max_items=100,
        direct_sync_jitter_seconds=600,
        direct_offline_marking_enabled=True,
        direct_offline_cleanup_enabled=False,
        direct_offline_cleanup_action="archive",
        direct_offline_cleanup_delay_hours=72,
        index_threshold=DEFAULT_INDEX_THRESHOLD,
        track_sort_key="title",
        search_result_ttl_hours=24,
        audio_format=audio_format,
        audio_quality=max(0, min(10, quality)),
        fetch_lyrics=bool(settings.fetch_lyrics),
        ytmusic_lyrics_fallback=bool(settings.ytmusic_lyrics_fallback),
        qq_lyrics_fallback=bool(getattr(settings, "qq_lyrics_fallback", True)),
        scrape_cooldown_hours=max(0, scrape_cooldown),
        download_ugc=bool(settings.download_ugc),
        replaygain=bool(settings.replaygain),
        scheduler_enabled=bool(settings.scheduler_enabled),
        scheduler_cron=str(settings.scheduler_cron),
        job_timeout_seconds=max(60, job_timeout),
        external_library_enabled=False,
        preselect_enabled=False,
        wash_enabled=False,
        preselect_root="",
        preselect_place_mode="link",
        preselect_match_mode="standard",
        download_cache_enabled=False,
        cache_min_free_gb=DEFAULT_MIN_FREE_GB,
        match_backoff_cap_days=7,
        match_strictness="strict",
        cover_excellence_px=0,
        cover_probe_fresh_days=7,
        cover_download_fresh_days=30,
        telegram_bot_token="",
        telegram_admin_ids="",
        telegram_user_ids="",
        telegram_daily_limit=5,
        wanted_enabled=True,
        wanted_auto_match_enabled=True,
        wanted_max_items=50,
        wanted_sync_jitter_seconds=600,
        wanted_source_musicbrainz=True,
        wanted_source_qq=True,
        wanted_source_discogs=False,
        wanted_source_lastfm=False,
        lastfm_api_key="",
    )


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _parse_overrides(raw: dict[str, Any], defaults: Preferences) -> dict[str, Any]:
    """Validate and collect known override keys from JSON."""
    overrides: dict[str, Any] = {}

    if "min_free_gb" in raw:
        try:
            value = float(raw["min_free_gb"])
            if value >= 0:
                overrides["min_free_gb"] = value
        except (TypeError, ValueError):
            logger.warning("Invalid min_free_gb in preferences: %r", raw["min_free_gb"])

    if "direct_folder" in raw:
        try:
            overrides["direct_folder"] = sanitize_direct_folder(
                str(raw["direct_folder"])
            )
        except Exception:
            logger.warning(
                "Invalid direct_folder in preferences: %r", raw["direct_folder"]
            )

    if "direct_download_limit" in raw:
        try:
            limit = int(raw["direct_download_limit"])
            if 1 <= limit <= 100:
                overrides["direct_download_limit"] = limit
            else:
                logger.warning(
                    "Invalid direct_download_limit in preferences: %r",
                    raw["direct_download_limit"],
                )
        except (TypeError, ValueError):
            logger.warning(
                "Invalid direct_download_limit in preferences: %r",
                raw["direct_download_limit"],
            )

    if "direct_auto_recover_enabled" in raw:
        overrides["direct_auto_recover_enabled"] = _coerce_bool(
            raw["direct_auto_recover_enabled"],
            defaults.direct_auto_recover_enabled,
        )

    if "direct_max_items" in raw:
        try:
            value = int(raw["direct_max_items"])
            if 1 <= value <= 10000:
                overrides["direct_max_items"] = value
            else:
                logger.warning(
                    "Invalid direct_max_items in preferences: %r",
                    raw["direct_max_items"],
                )
        except (TypeError, ValueError):
            logger.warning(
                "Invalid direct_max_items in preferences: %r",
                raw["direct_max_items"],
            )

    if "direct_sync_jitter_seconds" in raw:
        try:
            value = int(raw["direct_sync_jitter_seconds"])
            if 0 <= value <= 600:
                overrides["direct_sync_jitter_seconds"] = value
            else:
                logger.warning(
                    "Invalid direct_sync_jitter_seconds in preferences: %r",
                    raw["direct_sync_jitter_seconds"],
                )
        except (TypeError, ValueError):
            logger.warning(
                "Invalid direct_sync_jitter_seconds in preferences: %r",
                raw["direct_sync_jitter_seconds"],
            )

    if "direct_offline_marking_enabled" in raw:
        overrides["direct_offline_marking_enabled"] = _coerce_bool(
            raw["direct_offline_marking_enabled"],
            defaults.direct_offline_marking_enabled,
        )

    if "direct_offline_cleanup_enabled" in raw:
        overrides["direct_offline_cleanup_enabled"] = _coerce_bool(
            raw["direct_offline_cleanup_enabled"],
            defaults.direct_offline_cleanup_enabled,
        )

    if "direct_offline_cleanup_action" in raw:
        action = str(raw["direct_offline_cleanup_action"]).lower().strip()
        if action in {"delete", "archive", "to_wanted"}:
            overrides["direct_offline_cleanup_action"] = action
        else:
            logger.warning(
                "Invalid direct_offline_cleanup_action in preferences: %r",
                raw["direct_offline_cleanup_action"],
            )

    if "direct_offline_cleanup_delay_hours" in raw:
        try:
            hours = int(raw["direct_offline_cleanup_delay_hours"])
            if 0 <= hours <= 8760:
                overrides["direct_offline_cleanup_delay_hours"] = hours
            else:
                logger.warning(
                    "Invalid direct_offline_cleanup_delay_hours in preferences: %r",
                    raw["direct_offline_cleanup_delay_hours"],
                )
        except (TypeError, ValueError):
            logger.warning(
                "Invalid direct_offline_cleanup_delay_hours in preferences: %r",
                raw["direct_offline_cleanup_delay_hours"],
            )

    if "index_threshold" in raw:
        try:
            threshold = int(raw["index_threshold"])
            if 1 <= threshold <= 10000:
                overrides["index_threshold"] = threshold
            else:
                logger.warning(
                    "Invalid index_threshold in preferences: %r",
                    raw["index_threshold"],
                )
        except (TypeError, ValueError):
            logger.warning(
                "Invalid index_threshold in preferences: %r",
                raw["index_threshold"],
            )

    if "track_sort_key" in raw:
        key = str(raw["track_sort_key"]).lower().strip()
        if key in TRACK_SORT_KEYS:
            overrides["track_sort_key"] = key
        else:
            logger.warning(
                "Invalid track_sort_key in preferences: %r",
                raw["track_sort_key"],
            )

    if "search_result_ttl_hours" in raw:
        try:
            hours = int(raw["search_result_ttl_hours"])
            if 1 <= hours <= 720:
                overrides["search_result_ttl_hours"] = hours
            else:
                logger.warning(
                    "Invalid search_result_ttl_hours in preferences: %r",
                    raw["search_result_ttl_hours"],
                )
        except (TypeError, ValueError):
            logger.warning(
                "Invalid search_result_ttl_hours in preferences: %r",
                raw["search_result_ttl_hours"],
            )

    if "audio_format" in raw:
        fmt = str(raw["audio_format"]).lower()
        if fmt in AUDIO_FORMATS:
            overrides["audio_format"] = fmt
        else:
            logger.warning(
                "Invalid audio_format in preferences: %r", raw["audio_format"]
            )

    if "audio_quality" in raw:
        try:
            quality = int(raw["audio_quality"])
            if 0 <= quality <= 10:
                overrides["audio_quality"] = quality
        except (TypeError, ValueError):
            logger.warning(
                "Invalid audio_quality in preferences: %r", raw["audio_quality"]
            )

    for key in (
        "fetch_lyrics",
        "ytmusic_lyrics_fallback",
        "qq_lyrics_fallback",
        "download_ugc",
        "replaygain",
        "scheduler_enabled",
        "external_library_enabled",
        "preselect_enabled",
        "wash_enabled",
        "download_cache_enabled",
        "wanted_enabled",
        "wanted_auto_match_enabled",
        "wanted_source_musicbrainz",
        "wanted_source_qq",
        "wanted_source_discogs",
        "wanted_source_lastfm",
    ):
        if key in raw:
            overrides[key] = _coerce_bool(raw[key], getattr(defaults, key))

    if "wanted_max_items" in raw:
        try:
            value = int(raw["wanted_max_items"])
            if 1 <= value <= 10000:
                overrides["wanted_max_items"] = value
        except (TypeError, ValueError):
            logger.warning(
                "Invalid wanted_max_items in preferences: %r", raw["wanted_max_items"]
            )

    if "wanted_sync_jitter_seconds" in raw:
        try:
            value = int(raw["wanted_sync_jitter_seconds"])
            if 0 <= value <= 600:
                overrides["wanted_sync_jitter_seconds"] = value
        except (TypeError, ValueError):
            logger.warning(
                "Invalid wanted_sync_jitter_seconds in preferences: %r",
                raw["wanted_sync_jitter_seconds"],
            )

    if "lastfm_api_key" in raw:
        overrides["lastfm_api_key"] = str(raw["lastfm_api_key"] or "").strip()[:128]

    if "preselect_root" in raw:
        root = str(raw["preselect_root"] or "").strip()
        overrides["preselect_root"] = root

    if "cache_min_free_gb" in raw:
        try:
            value = float(raw["cache_min_free_gb"])
            if value >= 0:
                overrides["cache_min_free_gb"] = value
        except (TypeError, ValueError):
            logger.warning(
                "Invalid cache_min_free_gb in preferences: %r",
                raw["cache_min_free_gb"],
            )

    if "preselect_place_mode" in raw:
        mode = str(raw["preselect_place_mode"]).lower().strip()
        if mode in PRESELECT_PLACE_MODES:
            overrides["preselect_place_mode"] = mode
        else:
            logger.warning(
                "Invalid preselect_place_mode in preferences: %r",
                raw["preselect_place_mode"],
            )

    if "preselect_match_mode" in raw:
        mode = str(raw["preselect_match_mode"]).lower().strip()
        if mode in PRESELECT_MATCH_MODES:
            overrides["preselect_match_mode"] = mode
        else:
            logger.warning(
                "Invalid preselect_match_mode in preferences: %r",
                raw["preselect_match_mode"],
            )

    if "scheduler_cron" in raw:
        cron = str(raw["scheduler_cron"]).strip()
        if croniter.is_valid(cron):
            overrides["scheduler_cron"] = cron
        else:
            logger.warning("Invalid scheduler_cron in preferences: %r", cron)

    if "job_timeout_seconds" in raw:
        try:
            timeout = int(raw["job_timeout_seconds"])
            if timeout >= 60:
                overrides["job_timeout_seconds"] = timeout
            else:
                logger.warning(
                    "Invalid job_timeout_seconds in preferences: %r",
                    raw["job_timeout_seconds"],
                )
        except (TypeError, ValueError):
            logger.warning(
                "Invalid job_timeout_seconds in preferences: %r",
                raw["job_timeout_seconds"],
            )

    if "match_backoff_cap_days" in raw:
        try:
            days = int(raw["match_backoff_cap_days"])
            if 1 <= days <= 30:
                overrides["match_backoff_cap_days"] = days
            else:
                logger.warning(
                    "Invalid match_backoff_cap_days in preferences: %r",
                    raw["match_backoff_cap_days"],
                )
        except (TypeError, ValueError):
            logger.warning(
                "Invalid match_backoff_cap_days in preferences: %r",
                raw["match_backoff_cap_days"],
            )

    if "match_strictness" in raw:
        mode = str(raw["match_strictness"]).lower().strip()
        if mode in MATCH_STRICTNESS_MODES:
            overrides["match_strictness"] = mode
        else:
            logger.warning(
                "Invalid match_strictness in preferences: %r",
                raw["match_strictness"],
            )

    if "cover_excellence_px" in raw:
        try:
            px = int(raw["cover_excellence_px"])
            if 0 <= px <= 10000:
                overrides["cover_excellence_px"] = px
            else:
                logger.warning(
                    "Invalid cover_excellence_px in preferences: %r",
                    raw["cover_excellence_px"],
                )
        except (TypeError, ValueError):
            logger.warning(
                "Invalid cover_excellence_px in preferences: %r",
                raw["cover_excellence_px"],
            )

    if "cover_probe_fresh_days" in raw:
        try:
            days = int(raw["cover_probe_fresh_days"])
            if 1 <= days <= 365:
                overrides["cover_probe_fresh_days"] = days
            else:
                logger.warning(
                    "Invalid cover_probe_fresh_days in preferences: %r",
                    raw["cover_probe_fresh_days"],
                )
        except (TypeError, ValueError):
            logger.warning(
                "Invalid cover_probe_fresh_days in preferences: %r",
                raw["cover_probe_fresh_days"],
            )

    if "cover_download_fresh_days" in raw:
        try:
            days = int(raw["cover_download_fresh_days"])
            if 1 <= days <= 365:
                overrides["cover_download_fresh_days"] = days
            else:
                logger.warning(
                    "Invalid cover_download_fresh_days in preferences: %r",
                    raw["cover_download_fresh_days"],
                )
        except (TypeError, ValueError):
            logger.warning(
                "Invalid cover_download_fresh_days in preferences: %r",
                raw["cover_download_fresh_days"],
            )

    if "scrape_cooldown_hours" in raw:
        try:
            hours = int(raw["scrape_cooldown_hours"])
            if hours >= 0:
                overrides["scrape_cooldown_hours"] = hours
            else:
                logger.warning(
                    "Invalid scrape_cooldown_hours in preferences: %r",
                    raw["scrape_cooldown_hours"],
                )
        except (TypeError, ValueError):
            logger.warning(
                "Invalid scrape_cooldown_hours in preferences: %r",
                raw["scrape_cooldown_hours"],
            )

    if "telegram_bot_token" in raw:
        overrides["telegram_bot_token"] = str(raw["telegram_bot_token"] or "").strip()

    if "telegram_admin_ids" in raw:
        overrides["telegram_admin_ids"] = str(raw["telegram_admin_ids"] or "").strip()

    if "telegram_user_ids" in raw:
        overrides["telegram_user_ids"] = str(raw["telegram_user_ids"] or "").strip()

    if "telegram_daily_limit" in raw:
        try:
            limit = int(raw["telegram_daily_limit"])
            if 1 <= limit <= 1000:
                overrides["telegram_daily_limit"] = limit
            else:
                logger.warning(
                    "Invalid telegram_daily_limit in preferences: %r",
                    raw["telegram_daily_limit"],
                )
        except (TypeError, ValueError):
            logger.warning(
                "Invalid telegram_daily_limit in preferences: %r",
                raw["telegram_daily_limit"],
            )

    return overrides


class PreferencesStore:
    """Load/save preferences.json under the config directory.

    JSON overrides win over env defaults. ``reset()`` clears overrides so
    effective values fall back to env again.
    """

    def __init__(
        self,
        path: Path,
        data_path: Path,
        defaults: Preferences | None = None,
    ) -> None:
        self._path = path
        self._data_path = data_path
        self._lock = threading.Lock()
        self._defaults = defaults or Preferences()
        self._overrides: dict[str, Any] = {}
        self._load()

    def set_defaults(self, defaults: Preferences) -> None:
        """Replace env-backed defaults (e.g. after process settings change)."""
        with self._lock:
            self._defaults = defaults

    def effective(self) -> Preferences:
        with self._lock:
            return replace(self._defaults, **self._overrides)

    def snapshot(self) -> Preferences:
        """Alias for effective() — used by job / scheduler hot-read paths."""
        return self.effective()

    @property
    def min_free_gb(self) -> float:
        return self.effective().min_free_gb

    def update(self, **changes: Any) -> Preferences:
        """Merge partial updates into overrides and persist."""
        with self._lock:
            validated = _parse_overrides(changes, self._defaults)
            # Keep unset keys out of validated when caller passes None explicitly
            for key, value in changes.items():
                if value is None and key in validated:
                    del validated[key]
            self._overrides.update(validated)
            # Drop unknown / invalidated
            self._overrides = _parse_overrides(self._overrides, self._defaults)
            self._save_unlocked()
            return replace(self._defaults, **self._overrides)

    def reset(self) -> Preferences:
        """Clear preferences.json overrides; effective values become env defaults."""
        with self._lock:
            self._overrides = {}
            if self._path.exists():
                try:
                    self._path.unlink()
                except OSError as e:
                    logger.warning("Failed to remove preferences file: %s", e)
            return self._defaults

    def disk_status(self) -> DiskStatus:
        path = self._data_path
        try:
            path.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(path).free
        except OSError as e:
            logger.warning("Could not read free space for %s: %s", path, e)
            free = 0
        return DiskStatus(
            path=path,
            free_bytes=free,
            min_free_gb=self.min_free_gb,
        )

    def cache_disk_status(self) -> DiskStatus:
        prefs = self.effective()
        path = DOWNLOAD_CACHE_ROOT
        try:
            # Do not create the cache root here: a missing /data/cache
            # (or its optional SSD bind) must remain visible to settings checks.
            free = shutil.disk_usage(path).free if path.is_dir() else 0
        except OSError as e:
            logger.warning("Could not read cache free space for %s: %s", path, e)
            free = 0
        return DiskStatus(
            path=path,
            free_bytes=free,
            min_free_gb=prefs.cache_min_free_gb,
        )

    def ensure_cache_enough_space(self) -> DiskStatus:
        """Validate the fixed SSD staging mount when download caching is enabled."""
        from yubal_api.api.exceptions import InsufficientDiskSpaceError

        status = self.cache_disk_status()
        if not status.path.is_dir():
            raise RuntimeError(
                f"Download cache is enabled but {status.path} is missing"
            )
        if not os.access(status.path, os.W_OK):
            raise RuntimeError(
                f"Download cache is enabled but {status.path} is not writable"
            )
        if not status.enough_space:
            raise InsufficientDiskSpaceError(
                free_gb=status.free_gb,
                required_gb=status.min_free_gb,
                path=str(status.path),
            )
        return status

    def ensure_enough_space(self) -> DiskStatus:
        """Return disk status, raising if below the configured threshold."""
        from yubal_api.api.exceptions import InsufficientDiskSpaceError

        status = self.disk_status()
        if not status.enough_space:
            raise InsufficientDiskSpaceError(
                free_gb=status.free_gb,
                required_gb=status.min_free_gb,
                path=str(status.path),
            )
        return status

    def set_min_free_gb(self, value: float) -> float:
        """Back-compat helper used by older callers."""
        prefs = self.update(min_free_gb=value)
        return prefs.min_free_gb

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load preferences from %s: %s", self._path, e)
            return
        if not isinstance(raw, dict):
            return
        self._overrides = _parse_overrides(raw, self._defaults)

    def _save_unlocked(self) -> None:
        payload = dict(self._overrides)
        # Always persist a complete effective snapshot for readability, while
        # keeping only override keys so reset/env fallback stays correct.
        # Store overrides only (keys user has set via UI / previous saves).
        if not payload and not self._path.exists():
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except OSError as e:
            logger.error("Failed to save preferences to %s: %s", self._path, e)
            raise


def preferences_as_dict(prefs: Preferences) -> dict[str, Any]:
    """Serialize preferences for API responses."""
    data = asdict(prefs)
    # Keep field names stable for the frontend
    return data
