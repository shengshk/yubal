"""Application settings using pydantic-settings."""

import tempfile
from datetime import tzinfo
from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from croniter import croniter
from pydantic import BeforeValidator, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from yubal import AudioCodec

LogLevel = Annotated[
    Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    BeforeValidator(lambda v: v.upper() if isinstance(v, str) else v),
]


def _validate_timezone(v: str) -> str:
    """Validate timezone string by attempting to create ZoneInfo."""
    if isinstance(v, str):
        try:
            ZoneInfo(v)
        except KeyError as e:
            raise ValueError(f"Invalid timezone: {v}") from e
    return v


Timezone = Annotated[str, BeforeValidator(_validate_timezone)]


def _validate_cron_expression(v: str) -> str:
    """Validate cron expression using croniter."""
    if isinstance(v, str):
        if not croniter.is_valid(v):
            raise ValueError(f"Invalid cron expression: {v}")
    return v


CronExpression = Annotated[str, BeforeValidator(_validate_cron_expression)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="YUBAL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Project root (app install / web dist). Optional; defaults to /app.
    root: Path = Field(default=Path("/app"), description="Project root directory")

    # Path settings — Docker defaults:
    #   /data/... media library, /config settings+DB.
    # Override with YUBAL_DATA / YUBAL_CONFIG only when needed (tests, custom layouts).
    data: Path = Field(
        default=Path("/data/download"),
        description="Music download library (download/)",
    )
    config: Path = Field(
        default=Path("/config"),
        description="Config directory (settings, DB, cookies)",
    )

    # Reverse proxy
    base_path: str = Field(
        default="",
        description="URL base path for reverse proxy subfolder deployment",
    )

    # Server settings
    host: str = Field(default="127.0.0.1", description="Server host")
    port: int = Field(default=8000, description="Server port")
    reload: bool = Field(default=False, description="Enable auto-reload")
    debug: bool = Field(default=False, description="Enable debug mode")
    log_level: LogLevel = Field(default="INFO", description="Log level")

    # Audio settings
    audio_format: AudioCodec = Field(
        default=AudioCodec.MP3, description="Audio format"
    )
    audio_quality: str = Field(default="0", description="Audio quality (0 = best)")

    # Lyrics settings
    fetch_lyrics: bool = Field(default=True, description="Fetch lyrics from lrclib.net")
    ytmusic_lyrics_fallback: bool = Field(
        default=True,
        description="Fall back to YouTube Music lyrics when lrclib.net has no match",
    )
    qq_lyrics_fallback: bool = Field(
        default=True,
        description="Fall back to QQ Music lyrics when lrclib/YTM have no high-confidence match",
    )
    scrape_cooldown_hours: int = Field(
        default=24,
        ge=0,
        description=(
            "Hours before re-querying a missed Apple cover or lyrics source. "
            "0 disables cooldown."
        ),
    )

    # Filename settings
    ascii_filenames: bool = Field(
        default=False, description="Transliterate unicode to ASCII in filenames"
    )

    # UGC settings
    download_ugc: bool = Field(
        default=False,
        description="Download user-generated content tracks to unofficial/",
    )

    # ReplayGain settings
    replaygain: bool = Field(
        default=True,
        description="Apply ReplayGain tags using rsgain",
    )

    # Temp directory
    temp: Path = Field(
        default_factory=lambda: Path(tempfile.gettempdir()) / "yubal",
        description="Temp directory for downloads",
    )

    # CORS settings
    cors_origins: list[str] = Field(default=["*"], description="Allowed CORS origins")

    # Scheduler settings
    scheduler_enabled: bool = Field(
        default=True, description="Enable automatic scheduled sync"
    )
    scheduler_cron: CronExpression = Field(
        default="0 * * * *",
        description="Cron expression for scheduled sync",
    )

    # Job execution
    job_timeout_seconds: int = Field(
        default=1800,
        ge=60,
        description="Job execution timeout in seconds",
    )

    # Built-in login (set false to use external Traefik/Authelia auth)
    auth_login: bool = Field(
        default=True,
        description="Enable built-in login page and session auth",
    )

    # Telegram local Bot API. Set = use local tgapi; unset = official API.
    tg_api_url: str = Field(
        default="",
        description="Local Telegram Bot API base URL (e.g. http://tgapi:8081)",
    )

    # Timezone
    tz: Timezone = Field(default="UTC", description="Timezone for timestamps")

    @field_validator("base_path")
    @classmethod
    def normalize_base_path(cls, v: str) -> str:
        """Normalize: leading /, no trailing /, bare / becomes empty."""
        v = v.strip().rstrip("/")
        if not v or v == "/":
            return ""
        if not v.startswith("/"):
            v = f"/{v}"
        return v

    @model_validator(mode="before")
    @classmethod
    def set_path_defaults(cls, data: Any) -> Any:
        """Fill missing path fields with Docker layout defaults."""
        if not isinstance(data, dict):
            return data
        if not data.get("root"):
            data["root"] = Path("/app")
        if not data.get("data"):
            data["data"] = Path("/data/download")
        if not data.get("config"):
            data["config"] = Path("/config")
        return data

    @property
    def timezone(self) -> tzinfo:
        return ZoneInfo(self.tz)

    @property
    def ytdlp_dir(self) -> Path:
        return self.config / "ytdlp"

    @property
    def cookies_file(self) -> Path:
        return self.ytdlp_dir / "cookies.txt"

    @property
    def db_path(self) -> Path:
        return self.config / "yubal" / "yubal.db"

    @property
    def cache_path(self) -> Path:
        """Directory for extraction cache (same as db_path parent)."""
        return self.db_path.parent

    @property
    def auth_file(self) -> Path:
        """Built-in login credentials file under config/."""
        return self.config / "auth.json"

    @property
    def preferences_file(self) -> Path:
        """Runtime preferences edited from the Settings UI."""
        return self.config / "preferences.json"


@cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
