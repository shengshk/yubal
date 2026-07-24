"""Configuration for yubal."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class AudioCodec(StrEnum):
    """Supported audio output codecs."""

    OPUS = "opus"
    MP3 = "mp3"
    M4A = "m4a"


@dataclass(frozen=True)
class APIConfig:
    """YouTube Music API configuration.

    Attributes:
        search_limit: Maximum number of search results to return.
        ignore_spelling: Whether to ignore spelling in search queries.
    """

    search_limit: int = 1
    ignore_spelling: bool = True


@dataclass(frozen=True)
class DownloadConfig:
    """Download service configuration.

    Attributes:
        base_path: Base directory for downloaded files.
        codec: Audio codec for output files.
        quality: Audio quality (0 = best, 10 = worst). Only applies to lossy codecs.
        quiet: Suppress yt-dlp output.
        fetch_lyrics: Whether to fetch lyrics from lrclib.net.
        ytmusic_lyrics_fallback: When fetch_lyrics is enabled, fall back to
            YouTube Music's lyrics if lrclib.net has no match.
        qq_lyrics_fallback: When fetch_lyrics is enabled, fall back to QQ Music
            lyrics after lrclib / YouTube Music (high-confidence matches only).
        scrape_cooldown_hours: Hours to wait before re-querying a scrape source
            that previously missed (0 disables). Lyrics source misses use this.
            Default 24. Cover re-checks use premium shelf life (7d probe / 30d
            download) instead.
        cover_excellence_px: When >0, covers whose min edge reaches this many
            pixels permanently satisfy the premium cover requirement (no expiry).
            0 disables the seal (default).
        ascii_filenames: Transliterate unicode to ASCII in filenames.
        download_ugc: Whether to download UGC tracks to Unofficial/.
        library_folder: Top-level folder under base_path for matched tracks
            (playlist save folder or ``Direct``). Resolved by the playlist
            pipeline when unset.
    """

    base_path: Path
    codec: AudioCodec = AudioCodec.MP3
    quality: int = 0
    quiet: bool = True
    fetch_lyrics: bool = True
    ytmusic_lyrics_fallback: bool = True
    qq_lyrics_fallback: bool = True
    scrape_cooldown_hours: int = 24
    # Min edge (px) for permanent premium seal; 0 disables (shelf-life only).
    cover_excellence_px: int = 0
    # Premium cover shelf life (days) after probe vs full download comparison.
    cover_probe_fresh_days: int = 7
    cover_download_fresh_days: int = 30
    ascii_filenames: bool = False
    download_ugc: bool = False
    library_folder: str | None = None
    download_cache_path: Path | None = None
    cache_min_free_gb: float = 2.0
    data_min_free_gb: float = 2.0


@dataclass(frozen=True)
class PlaylistDownloadConfig:
    """Playlist download service configuration.

    Combines download settings with playlist-specific options.

    Attributes:
        download: Download configuration for tracks.
        generate_m3u: Whether to generate M3U playlist file.
        save_cover: Whether to save playlist cover image.
        skip_album_m3u: Skip M3U generation for album playlists.
        max_items: Per-round cap on not-yet-local tracks (see extractor).
            Already-local tracks are still processed for hardlink/skip.
            None means no cap.
        apply_replaygain: Whether to apply ReplayGain tags using rsgain.
        cache_path: Directory for extraction cache. None disables caching.
    """

    download: DownloadConfig
    generate_m3u: bool = False
    save_cover: bool = False
    skip_album_m3u: bool = True
    max_items: int | None = None
    apply_replaygain: bool = True
    cache_path: Path | None = None
    # Video IDs to skip during download (e.g. user sync blacklist).
    excluded_video_ids: frozenset[str] | None = None
