"""Settings / preferences API schemas."""

from typing import Literal

from pydantic import BaseModel, Field

AudioFormat = Literal["opus", "mp3", "m4a"]
PreselectPlaceMode = Literal["link", "copy"]
PreselectMatchMode = Literal["loose", "standard", "strict"]
MatchStrictness = Literal["strict", "relaxed"]
TrackSortKey = Literal["title", "artist", "album"]


class SettingsResponse(BaseModel):
    """Runtime preferences plus live disk status for the data mount."""

    min_free_gb: float = Field(
        description=(
            "Refuse new jobs when free space is below this many GiB. 0 disables."
        )
    )
    direct_download_limit: int = Field(
        ge=1,
        le=100,
        description="Maximum track count allowed for a direct download.",
    )
    index_threshold: int = Field(
        ge=1,
        le=10000,
        description=(
            "Show A–Z section index when a playlist has at least this many tracks."
        ),
    )
    track_sort_key: TrackSortKey = Field(
        description="Default sort field for indexed track lists.",
    )
    search_result_ttl_hours: int = Field(
        ge=1,
        le=720,
        description="Hours to retain the latest online search result.",
    )
    audio_format: AudioFormat
    audio_quality: int = Field(ge=0, le=10)
    fetch_lyrics: bool
    ytmusic_lyrics_fallback: bool
    qq_lyrics_fallback: bool
    scrape_cooldown_hours: int = Field(
        ge=0,
        description=(
            "Hours before re-querying a missed lyrics source. "
            "0 disables. Covers use shelf-life days, not this."
        ),
    )
    download_ugc: bool
    replaygain: bool
    scheduler_enabled: bool
    scheduler_cron: str
    job_timeout_seconds: int = Field(ge=60, description="Per-job timeout in seconds")
    external_library_enabled: bool = False
    preselect_enabled: bool = False
    wash_enabled: bool = False
    preselect_root: str = ""
    preselect_place_mode: PreselectPlaceMode = "link"
    preselect_match_mode: PreselectMatchMode = "standard"
    preselect_hardlink_ok: bool | None = None
    preselect_indexed: int = 0
    preselect_placed: int = 0
    download_cache_enabled: bool = False
    cache_path: str = "/data/cache"
    cache_min_free_gb: float = 2.0
    cache_free_bytes: int = 0
    cache_free_gb: float = 0
    cache_available: bool = False
    match_backoff_cap_days: int = Field(
        default=7,
        ge=1,
        le=30,
        description=(
            "External match: linear +24h per fail; reject (junk) when "
            "cumulative wait would exceed this many days."
        ),
    )
    match_strictness: MatchStrictness = Field(
        default="strict",
        description=(
            "External-library YTM match: strict rejects Live/DJ↔studio; "
            "relaxed allows base-title matches across versions."
        ),
    )
    cover_excellence_px: int = Field(
        default=0,
        ge=0,
        le=10000,
        description=(
            "Min cover edge in pixels for permanent premium (e.g. 2000). "
            "0 disables — premium uses probe/download shelf life only."
        ),
    )
    cover_probe_fresh_days: int = Field(
        default=7,
        ge=1,
        le=365,
        description="Days a probe-only cover comparison stays fresh (default 7).",
    )
    cover_download_fresh_days: int = Field(
        default=30,
        ge=1,
        le=365,
        description=(
            "Days a full-download cover comparison stays fresh (default 30)."
        ),
    )
    library_health_status: str | None = None
    library_health_reason: str | None = None
    data_path: str
    free_bytes: int
    free_gb: float
    enough_space: bool
    maintenance_locked: bool = False
    telegram_bot_token: str = ""
    telegram_admin_ids: str = ""
    telegram_user_ids: str = ""
    telegram_daily_limit: int = Field(default=5, ge=1, le=1000)
    telegram_api_url: str = ""
    telegram_bot_running: bool = False
    wanted_enabled: bool = True
    wanted_auto_match_enabled: bool = True
    wanted_max_items: int = Field(default=50, ge=1, le=10000)
    wanted_sync_jitter_seconds: int = Field(default=600, ge=0, le=600)
    wanted_source_musicbrainz: bool = True
    wanted_source_qq: bool = True
    wanted_source_discogs: bool = False
    wanted_source_lastfm: bool = False
    lastfm_api_key: str = ""


class SettingsUpdate(BaseModel):
    """Partial update for runtime preferences."""

    min_free_gb: float | None = Field(default=None, ge=0, le=10000)
    direct_download_limit: int | None = Field(default=None, ge=1, le=100)
    index_threshold: int | None = Field(default=None, ge=1, le=10000)
    track_sort_key: TrackSortKey | None = None
    search_result_ttl_hours: int | None = Field(default=None, ge=1, le=720)
    audio_format: AudioFormat | None = None
    audio_quality: int | None = Field(default=None, ge=0, le=10)
    fetch_lyrics: bool | None = None
    ytmusic_lyrics_fallback: bool | None = None
    qq_lyrics_fallback: bool | None = None
    scrape_cooldown_hours: int | None = Field(default=None, ge=0, le=8760)
    download_ugc: bool | None = None
    replaygain: bool | None = None
    scheduler_enabled: bool | None = None
    scheduler_cron: str | None = None
    job_timeout_seconds: int | None = Field(default=None, ge=60, le=86400)
    external_library_enabled: bool | None = None
    preselect_enabled: bool | None = None
    wash_enabled: bool | None = None
    preselect_place_mode: PreselectPlaceMode | None = None
    preselect_match_mode: PreselectMatchMode | None = None
    download_cache_enabled: bool | None = None
    cache_min_free_gb: float | None = Field(default=None, ge=0, le=10000)
    match_backoff_cap_days: int | None = Field(default=None, ge=1, le=30)
    match_strictness: MatchStrictness | None = None
    cover_excellence_px: int | None = Field(default=None, ge=0, le=10000)
    cover_probe_fresh_days: int | None = Field(default=None, ge=1, le=365)
    cover_download_fresh_days: int | None = Field(default=None, ge=1, le=365)
    telegram_bot_token: str | None = None
    telegram_admin_ids: str | None = None
    telegram_user_ids: str | None = None
    telegram_daily_limit: int | None = Field(default=None, ge=1, le=1000)
    wanted_enabled: bool | None = None
    wanted_auto_match_enabled: bool | None = None
    wanted_max_items: int | None = Field(default=None, ge=1, le=10000)
    wanted_sync_jitter_seconds: int | None = Field(default=None, ge=0, le=600)
    wanted_source_musicbrainz: bool | None = None
    wanted_source_qq: bool | None = None
    wanted_source_discogs: bool | None = None
    wanted_source_lastfm: bool | None = None
    lastfm_api_key: str | None = None
