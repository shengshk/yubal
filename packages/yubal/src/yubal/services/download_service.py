"""Download service for YouTube Music tracks using yt-dlp."""

import logging
import os
import shutil
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yt_dlp
from yt_dlp.utils import DownloadCancelled

from yubal.client import YTMusicProtocol
from yubal.config import DownloadConfig
from yubal.exceptions import CancellationError, DownloadError
from yubal.models.cancel import CancelToken
from yubal.models.enums import DownloadStatus, MatchResult, SkipReason
from yubal.models.progress import DownloadProgress
from yubal.models.results import DownloadResult
from yubal.models.track import TrackMetadata
from yubal.services.folder_presence_protocol import FolderPresence
from yubal.services.lyrics import (
    LrclibFetcher,
    LyricsFetcher,
    LyricsService,
    LyricsServiceProtocol,
    YouTubeMusicLyricsFetcher,
)
from yubal.services.qq_lyrics import QQMusicLyricsFetcher
from yubal.services.scrape_state import (
    COVER_APPLE,
    COVER_CHECK_DOWNLOAD,
    COVER_CHECK_PROBE,
    COVER_EMBEDDED,
    COVER_YTM,
    ScrapeStateStore,
)
from yubal.services.tagging_service import AudioFileTaggingService
from yubal.services.track_index import TrackFileIndex
from yubal.utils.apple_cover import (
    probe_image_dimensions,
    search_apple_cover_url,
    select_best_cover,
)
from yubal.utils.audio_assets import read_embedded_cover
from yubal.utils.cover import write_better_image
from yubal.utils.filename import (
    build_track_path,
    build_unmatched_track_path,
    build_unofficial_track_path,
)
from yubal.utils.image_quality import cover_quality_score, image_dimensions
from yubal.utils.library import DIRECT_FOLDER, STORAGE_EXTERNAL, detect_storage_for_path

logger = logging.getLogger(__name__)

# Normalize each fetcher's display name to a stable provenance token.
_LYRICS_SOURCE_TOKENS = {
    "lrclib": "lrclib",
    "youtube music": "ytm",
    "ytm": "ytm",
    "qq": "qq",
}


def _normalize_lyrics_source(name: str | None) -> str | None:
    if not name:
        return None
    return _LYRICS_SOURCE_TOKENS.get(name.strip().lower(), name.strip().lower())


@dataclass
class EnrichmentOutcome:
    """Result of enriching one existing file (cover + lyrics + tags).

    ``error`` is set (rather than raised) when tagging fails, so a library
    enrichment pass can record the failure and retry the track next cycle
    instead of aborting.
    """

    has_embedded_cover: bool
    has_lyrics_embedded: bool
    has_lyrics_sidecar: bool
    cover_source: str | None
    lyrics: str | None
    lyrics_source: str | None = None
    error: str | None = None


# ============================================================================
# PROTOCOL & CONSTANTS
# ============================================================================


class DownloaderProtocol(Protocol):
    """Protocol for download backends.

    This protocol enables dependency injection and testing.
    Implement this protocol to create mock downloaders for testing.
    """

    def download(
        self,
        video_id: str,
        output_path: Path,
        cancel_token: CancelToken | None = None,
    ) -> Path:
        """Download a track to the specified path.

        Returns:
            Actual path where file was saved (with extension).
        """
        ...


# ============================================================================
# YT-DLP BACKEND - Low-level downloader using yt-dlp library
# ============================================================================


class YTDLPDownloader:
    """yt-dlp based downloader for YouTube Music tracks.

    Wraps yt-dlp with consistent configuration and error handling.
    Implements DownloaderProtocol for dependency injection and testing.

    The downloader handles:
    - Audio extraction with FFmpeg post-processing
    - Output path management (creates directories as needed)
    - Capture of actual output path (which may differ from template)
    """

    YOUTUBE_MUSIC_URL = "https://music.youtube.com/watch?v={video_id}"
    MAX_RETRIES: int = 3
    RETRY_BASE_DELAY: float = 1.0  # seconds, doubles each retry (1s, 2s, 4s)

    def __init__(
        self,
        config: DownloadConfig,
        cookies_path: Path | None = None,
    ) -> None:
        """Initialize the downloader.

        Args:
            config: Download configuration (codec, quality, output paths).
            cookies_path: Optional path to cookies.txt for authentication.
                         Required for age-restricted or premium content.
        """
        self._config = config
        self._cookies_path = cookies_path

        if cookies_path and cookies_path.exists():
            logger.info("Using cookies for yt-dlp downloads")
        else:
            logger.info("No cookies configured for yt-dlp downloads")

    def _build_yt_dlp_options(
        self, output_path: Path, cookies_override: Path | None = None
    ) -> dict[str, Any]:
        """Build yt-dlp options for audio extraction and post-processing.

        Why these options: Configures yt-dlp to download best audio quality and
        convert to the target codec (e.g., MP3, M4A) using FFmpeg. The quiet
        flags control console output verbosity.

        Args:
            output_path: Target path for the downloaded file.
            cookies_override: Optional path to a temp cookies copy. When provided,
                this is used as cookiefile. Pass ``None`` for anonymous downloads
                (preferred for public catalog tracks).

        Returns:
            Dictionary of yt-dlp options.
        """
        codec = self._config.codec.value
        opts: dict[str, Any] = {
            "format": (
                f"bestaudio[ext={codec}]/bestaudio[acodec={codec}]/bestaudio/best"
            ),
            "outtmpl": str(output_path),
            "color": "never",  # Disable ANSI codes in error messages
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": self._config.codec.value,
                    "preferredquality": str(self._config.quality),
                }
            ],
            "quiet": self._config.quiet,
            "no_warnings": self._config.quiet,
            "noprogress": self._config.quiet,
            # Rate limiting mitigation: exponential backoff (1s, 2s, 4s, 8s, ...)
            "retry_sleep_functions": {
                "http": lambda n: min(2**n, 30),  # Cap at 30s
                "fragment": lambda n: min(2**n, 30),
            },
            # Fetch the EJS challenge solver. See issue #29 https://github.com/guillevc/yubal/issues/29
            "remote_components": ["ejs:github"],
        }

        # Use cookies only when an explicit cookies file is provided.
        # Callers that want cookies pass a path; cookieless downloads pass None.
        if cookies_override and cookies_override.exists():
            opts["cookiefile"] = str(cookies_override)

        return opts

    def _is_retryable_error(self, error_msg: str) -> bool:
        """Check if the error is a retryable transient network/HTTP error."""
        retryable_patterns = (
            "HTTP Error 403",
            "403 Forbidden",
            "HTTP Error 429",
            "HTTP Error 5",  # Catches 500, 502, 503, etc.
            # Flaky TLS to YouTube CDNs / Google APIs (common behind unstable links).
            "UNEXPECTED_EOF_WHILE_READING",
            "SSLEOFError",
            "SSL: UNEXPECTED_EOF",
        )
        return any(pattern in error_msg for pattern in retryable_patterns)

    @staticmethod
    def _is_format_unavailable(error_msg: str) -> bool:
        return "Requested format is not available" in error_msg

    def _cleanup_partial_downloads(self, output_path: Path) -> None:
        """Remove partial download files before retry."""
        for partial in output_path.parent.glob(f"{output_path.name}*.part"):
            partial.unlink(missing_ok=True)

    def download(
        self,
        video_id: str,
        output_path: Path,
        cancel_token: CancelToken | None = None,
    ) -> Path:
        """Download a track and extract audio to the specified path.

        Why hook-based path capture: yt-dlp may change the output filename during
        post-processing (e.g., adding codec extension). We use a postprocessor hook
        to capture the actual output path after FFmpeg completes.

        Error handling: Provides specific error messages for common issues like
        region-locked videos or authentication requirements, making debugging easier.

        Args:
            video_id: YouTube video ID.
            output_path: Target path for the downloaded file (without extension).
            cancel_token: Optional token for cancellation support.

        Returns:
            Actual path where file was saved (with extension).

        Raises:
            DownloadError: If download fails.
            CancellationError: If cancel_token is cancelled during download.
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Copy cookies to temp file to prevent yt-dlp from modifying the original.
        # yt-dlp writes back its cookie jar to cookiefile, stripping entries
        # needed by ytmusicapi (SID, HSID, SSID).
        temp_cookies: Path | None = None
        if self._cookies_path and self._cookies_path.exists():
            fd, tmp = tempfile.mkstemp(suffix=".txt", prefix="yubal_cookies_")
            os.close(fd)
            temp_cookies = Path(tmp)
            shutil.copy2(self._cookies_path, temp_cookies)

        actual_path: Path | None = None
        try:
            # Public catalog downloads: prefer anonymous first. Cookies often make
            # player clients return storyboard-only formats. Escalate to cookies
            # only when YouTube demands auth (age-restricted / private).
            cookies_attempted = False
            anon_after_cookies = False

            def capture_postprocessed_path(d: dict[str, Any]) -> None:
                """Capture the final output path after FFmpeg post-processing."""
                nonlocal actual_path
                # Capture filepath after FFmpeg postprocessor completes
                if d["status"] == "finished":
                    filepath = d.get("info_dict", {}).get("filepath")
                    if filepath:
                        actual_path = Path(filepath)

            def _cancel_hook(d: dict[str, Any]) -> None:
                if cancel_token and cancel_token.is_cancelled:
                    raise DownloadCancelled("Download cancelled")

            # Start without cookies even when a cookies file exists.
            opts = self._build_yt_dlp_options(output_path, None)
            url = self.YOUTUBE_MUSIC_URL.format(video_id=video_id)

            logger.debug("Downloading %s to %s", video_id, output_path)

            opts["progress_hooks"] = [_cancel_hook]
            opts["postprocessor_hooks"] = [
                capture_postprocessed_path,
                _cancel_hook,
            ]

            for attempt in range(self.MAX_RETRIES + 1):
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        ydl.download([url])
                    break  # Success
                except DownloadCancelled as e:
                    self._cleanup_partial_downloads(output_path)
                    raise CancellationError("Download cancelled") from e
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as e:
                    error_msg = str(e)

                    # Non-retryable errors - fail immediately
                    if "Video unavailable" in error_msg:
                        logger.error(
                            "Video %s is unavailable (may be region-locked)",
                            video_id,
                        )
                        raise DownloadError(
                            f"Video {video_id} is unavailable "
                            "(may be region-locked or removed)"
                        ) from e

                    needs_auth = "Sign in" in error_msg or (
                        "cookies" in error_msg.lower()
                        and not self._is_format_unavailable(error_msg)
                    )
                    # Anonymous failed → escalate to cookies once when available.
                    if (
                        needs_auth
                        and not cookies_attempted
                        and temp_cookies is not None
                        and temp_cookies.exists()
                        and not opts.get("cookiefile")
                    ):
                        logger.warning(
                            "Auth required for %s; retrying with cookies",
                            video_id,
                        )
                        opts["cookiefile"] = str(temp_cookies)
                        cookies_attempted = True
                        self._cleanup_partial_downloads(output_path)
                        continue
                    if needs_auth:
                        logger.error("Authentication required for video %s", video_id)
                        raise DownloadError(
                            f"Authentication required for {video_id}. "
                            "Try providing a cookies.txt file."
                        ) from e

                    # Cookies present but no audio formats → drop cookies once.
                    if (
                        not anon_after_cookies
                        and opts.get("cookiefile")
                        and self._is_format_unavailable(error_msg)
                    ):
                        logger.warning(
                            "No downloadable formats for %s with cookies; "
                            "retrying without cookies",
                            video_id,
                        )
                        opts.pop("cookiefile", None)
                        anon_after_cookies = True
                        self._cleanup_partial_downloads(output_path)
                        continue

                    # Retryable transient errors (403, 429, 5xx, SSL EOF)
                    if self._is_retryable_error(error_msg):
                        if attempt < self.MAX_RETRIES:
                            delay = self.RETRY_BASE_DELAY * (2**attempt)
                            logger.warning(
                                "Transient error downloading %s (attempt %d/%d), "
                                "retrying in %.1fs: %s",
                                video_id,
                                attempt + 1,
                                self.MAX_RETRIES + 1,
                                delay,
                                error_msg,
                            )
                            self._cleanup_partial_downloads(output_path)
                            time.sleep(delay)
                            continue
                        # All retries exhausted
                        logger.error(
                            "Failed to download %s after %d attempts: %s",
                            video_id,
                            self.MAX_RETRIES + 1,
                            error_msg,
                        )
                        raise DownloadError(
                            f"Failed to download {video_id} after "
                            f"{self.MAX_RETRIES + 1} attempts: {e}"
                        ) from e

                    # Other errors - fail immediately
                    logger.exception("Failed to download %s: %s", video_id, e)
                    raise DownloadError(f"Failed to download {video_id}: {e}") from e
        finally:
            if temp_cookies is not None:
                temp_cookies.unlink(missing_ok=True)

        # Return actual path captured by hook, or fallback to expected path
        return self._resolve_output_path(actual_path, output_path)

    def _resolve_output_path(
        self, captured_path: Path | None, expected_path: Path
    ) -> Path:
        """Resolve the final output path after download.

        Why fallback logic: If the hook didn't capture the path (edge cases,
        yt-dlp internals changed), we construct the expected path by adding
        the codec extension.

        Args:
            captured_path: Path captured by postprocessor hook (may be None).
            expected_path: Expected output path without extension.

        Returns:
            Final output path with extension.
        """
        # Use captured path if available and exists
        if captured_path and captured_path.exists():
            return captured_path

        # Fallback to expected path with codec extension
        # (use string concat - with_suffix breaks on dots in filename)
        expected_with_ext = Path(f"{expected_path}.{self._config.codec.value}")
        if expected_with_ext.exists():
            return expected_with_ext

        return expected_path


# ============================================================================
# DOWNLOAD SERVICE - High-level orchestration for track downloads
# ============================================================================


class DownloadService:
    """Service for downloading YouTube Music tracks.

    Pipeline Overview:
    ==================
    1. download_tracks() - Main entry point: iterates through tracks,
                          yields progress updates as downloads complete
    2. download_track() - Downloads single track: checks if exists,
                         selects video ID, downloads, tags with metadata
    3. _select_video_id_for_download() - Chooses ATV or OMV video ID
                         based on availability (prefers ATV for audio quality)
    4. _build_output_path_for_track() - Constructs file path using artist,
                         album, and track metadata
    5. _apply_metadata_tags() - Tags downloaded file with ID3/MP4 tags
                         and embeds cover art

    Example:
        >>> from yubal.config import DownloadConfig
        >>> config = DownloadConfig(base_path=Path("./music"))
        >>> service = DownloadService(config)
        >>> result = service.download_track(track_metadata)
        >>> if result.status == DownloadStatus.SUCCESS:
        ...     print(f"Downloaded to: {result.output_path}")
    """

    def __init__(
        self,
        config: DownloadConfig,
        downloader: DownloaderProtocol | None = None,
        cookies_path: Path | None = None,
        lyrics_service: LyricsServiceProtocol | None = None,
        ytmusic_client: YTMusicProtocol | None = None,
        folder_presence: FolderPresence | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            config: Download configuration.
            downloader: Optional downloader implementation.
                        Uses YTDLPDownloader if not provided.
            cookies_path: Optional path to cookies.txt for authentication.
                         Required for age-restricted or premium content.
            lyrics_service: Optional lyrics service implementation.
                           Uses LyricsService if fetch_lyrics is enabled.
            ytmusic_client: Optional YTMusic client used to enable the
                           YouTube Music lyrics fallback chain. When provided
                           and `config.ytmusic_lyrics_fallback` is True, the
                           composite tries lrclib first and falls back to
                           YouTube Music's lyrics.
            folder_presence: Optional per-save-folder catalog lookup (API only).
        """
        self._config = config
        self._downloader = downloader or YTDLPDownloader(config, cookies_path)
        self._tagger = AudioFileTaggingService()
        self._lyrics_service: LyricsServiceProtocol | None = (
            lyrics_service
            if lyrics_service is not None
            else self._build_lyrics_service(config, ytmusic_client)
        )
        self._library_folder = config.library_folder
        self._track_index = TrackFileIndex(config.base_path)
        self._scrape_state = ScrapeStateStore(config.base_path)
        self._folder_presence = folder_presence

    def set_folder_presence(self, source: FolderPresence | None) -> None:
        """Attach or clear per-folder catalog presence lookup."""
        self._folder_presence = source

    def set_library_folder(self, folder: str) -> None:
        """Set the top-level folder for matched track downloads."""
        self._library_folder = folder

    def has_local_copy(self, video_id: str) -> bool:
        """Return True if this video_id already has a file under the library."""
        folder = self._library_folder
        if self._folder_presence is not None and folder:
            if self._folder_presence.existing_path(video_id, folder) is not None:
                return True
        return self._track_index.get(video_id) is not None

    @property
    def staging_enabled(self) -> bool:
        return self._config.download_cache_path is not None

    def _staging_stem(self, final_stem: Path) -> Path:
        cache = self._config.download_cache_path
        if cache is None:
            return final_stem
        try:
            relative = final_stem.relative_to(self._config.base_path)
        except ValueError as e:
            raise DownloadError(
                f"Download destination escapes library root: {final_stem}"
            ) from e
        return cache / "yubal-staging" / relative

    @staticmethod
    def _ensure_free_space(path: Path, minimum_gb: float, label: str) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(path).free
        except OSError as e:
            raise DownloadError(f"{label} is unavailable: {path}: {e}") from e
        required = int(max(0.0, minimum_gb) * 1024**3)
        if free < required:
            raise DownloadError(
                f"{label} has only {free / 1024**3:.2f} GiB free; "
                f"{minimum_gb:.2f} GiB required ({path})"
            )

    @staticmethod
    def _atomic_copy(source: Path, destination: Path) -> None:
        """Copy across filesystems, then atomically reveal on the destination disk."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_tmp = tempfile.mkstemp(
            prefix=f".{destination.name}.yubal-publish-",
            dir=destination.parent,
        )
        os.close(fd)
        tmp = Path(raw_tmp)
        try:
            shutil.copy2(source, tmp)
            if tmp.stat().st_size != source.stat().st_size:
                raise OSError("published copy size mismatch")
            with tmp.open("rb") as handle:
                os.fsync(handle.fileno())
            os.replace(tmp, destination)
        finally:
            tmp.unlink(missing_ok=True)

    def publish_staged(self, result: DownloadResult) -> DownloadResult:
        """Publish one fully processed staged track; audio is made visible last."""
        source = result.output_path
        destination = result.final_path
        if (
            result.status != DownloadStatus.SUCCESS
            or source is None
            or destination is None
        ):
            return result
        try:
            self._ensure_free_space(
                self._config.base_path,
                self._config.data_min_free_gb,
                "Download library",
            )

            # Publish metadata sidecars first. External scanners only see a complete
            # track once the audio rename below succeeds.
            source_lrc = source.with_suffix(".lrc")
            if source_lrc.is_file():
                self._atomic_copy(source_lrc, destination.with_suffix(".lrc"))
            source_cover = source.parent / "cover.jpg"
            if source_cover.is_file():
                self._atomic_copy(source_cover, destination.parent / "cover.jpg")
            source_artist = source.parent.parent / "artist.jpg"
            if source_artist.is_file():
                self._atomic_copy(
                    source_artist, destination.parent.parent / "artist.jpg"
                )

            self._atomic_copy(source, destination)
            if (
                result.video_id_used
                and result.track.match_result == MatchResult.MATCHED
            ):
                self._track_index.set(result.video_id_used, destination)

            source.unlink(missing_ok=True)
            source_lrc.unlink(missing_ok=True)
            source_cover.unlink(missing_ok=True)
            source_artist.unlink(missing_ok=True)
            self._prune_staging_dirs(source.parent)
            logger.info("Published staged track: '%s'", destination)
            return result.model_copy(
                update={"output_path": destination, "final_path": None}
            )
        except (OSError, DownloadError) as e:
            logger.exception("Failed to publish staged track %s", source)
            return result.model_copy(
                update={
                    "status": DownloadStatus.FAILED,
                    "error": f"Failed to publish staged track: {e}",
                }
            )

    def _prune_staging_dirs(self, start: Path) -> None:
        cache = self._config.download_cache_path
        if cache is None:
            return
        boundary = cache / "yubal-staging"
        current = start
        while current != boundary and boundary in current.parents:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    @staticmethod
    def _build_lyrics_service(
        config: DownloadConfig,
        ytmusic_client: YTMusicProtocol | None,
    ) -> LyricsServiceProtocol | None:
        """Construct the default composite lyrics service.

        Returns None when lyrics fetching is disabled. Always includes the
        lrclib fetcher; appends YouTube Music and QQ Music fetchers when enabled.
        """
        if not config.fetch_lyrics:
            return None

        fetchers: list[LyricsFetcher] = [LrclibFetcher()]
        if config.ytmusic_lyrics_fallback and ytmusic_client is not None:
            fetchers.append(YouTubeMusicLyricsFetcher(ytmusic_client))
        if config.qq_lyrics_fallback:
            fetchers.append(QQMusicLyricsFetcher())
        return LyricsService(fetchers=fetchers)

    # ============================================================================
    # PUBLIC API - Main entry points for downloading tracks
    # ============================================================================

    def download_tracks(
        self,
        tracks: list[TrackMetadata],
        cancel_token: CancelToken | None = None,
    ) -> Iterator[DownloadProgress]:
        """Download multiple tracks with progress updates.

        Yields progress updates as each track is downloaded, making this ideal
        for CLI progress bars or UI updates. Supports cancellation via token.

        Why yield progress: Allows callers to display real-time feedback during
        long-running downloads (some playlists have hundreds of tracks).

        Args:
            tracks: List of track metadata to download.
            cancel_token: Optional token for cancellation support.

        Yields:
            DownloadProgress with current/total counts and the download result.

        Raises:
            CancellationError: If cancel_token.is_cancelled becomes True.

        Example:
            >>> for progress in downloader.download_tracks(tracks):
            ...     print(f"[{progress.current}/{progress.total}]")
        """
        total = len(tracks)

        for i, track in enumerate(tracks):
            # Check for cancellation before each download
            if cancel_token and cancel_token.is_cancelled:
                raise CancellationError("Download cancelled")

            result = self.download_track(track, cancel_token=cancel_token)
            self._log_track_outcome(i + 1, total, track, result)
            yield DownloadProgress(current=i + 1, total=total, result=result)

    def _track_label(self, track: TrackMetadata) -> str:
        if track.artist:
            return f"{track.artist} - {track.title}"
        return track.title

    def _log_track_outcome(
        self,
        current: int,
        total: int,
        track: TrackMetadata,
        result: DownloadResult,
    ) -> None:
        """Emit a single user-facing log line per track outcome."""
        label = self._track_label(track)
        prefix = f"[{current}/{total}] "
        base_extra = {
            "current": current,
            "total": total,
            "track_title": track.title,
            "track_artist": track.artist,
        }

        match result.status:
            case DownloadStatus.HARDLINKED:
                logger.info(
                    "%s%s — hardlinked from local library",
                    prefix,
                    label,
                    extra={**base_extra, "status": "hardlinked"},
                )
            case DownloadStatus.PRESELECTED:
                logger.info(
                    "%s%s — preselected from local library",
                    prefix,
                    label,
                    extra={**base_extra, "status": "preselected"},
                )
            case DownloadStatus.SKIPPED:
                reason = (
                    result.skip_reason.label.lower()
                    if result.skip_reason
                    else "skipped"
                )
                logger.info(
                    "%s%s — %s",
                    prefix,
                    label,
                    reason,
                    extra={**base_extra, "status": "skipped"},
                )
            case DownloadStatus.SUCCESS:
                logger.info(
                    "%s%s",
                    prefix,
                    label,
                    extra={**base_extra, "event_type": "track_download"},
                )
            case DownloadStatus.FAILED:
                return

    def download_track(
        self,
        track: TrackMetadata,
        cancel_token: CancelToken | None = None,
    ) -> DownloadResult:
        """Download a single track with metadata tagging.

        Download pipeline:
        1. Build output path from track metadata
        2. Skip if file already exists (no overwrite)
        3. Select video ID (prefer ATV for audio quality)
        4. Download audio using yt-dlp
        5. Apply metadata tags (ID3/MP4) and embed cover art

        Why skip existing: Prevents re-downloading tracks if the download is
        interrupted and resumed. Users can manually delete files to re-download.

        Args:
            track: Track metadata.

        Returns:
            DownloadResult with status, path, and any error information.
        """
        output_path = self._build_output_path_for_track(track)

        try:
            video_id = self._select_video_id_for_download(track)
        except DownloadError as e:
            logger.error("Track '%s' failed: %s", track.title, e)
            return DownloadResult(
                track=track,
                status=DownloadStatus.FAILED,
                error=str(e),
            )

        folder = self._library_folder
        if (
            self._folder_presence is not None
            and folder
            and track.match_result == MatchResult.MATCHED
        ):
            present = self._folder_presence.existing_path(video_id, folder)
            if present is not None:
                logger.debug("Skip catalog-present file: %s", present)
                self._enrich_local_copy(present, track)
                self._track_index.set(video_id, present)
                return DownloadResult(
                    track=track,
                    status=DownloadStatus.SKIPPED,
                    output_path=present,
                    video_id_used=video_id,
                    skip_reason=SkipReason.FILE_EXISTS,
                )

        # Skip existing files (with_suffix breaks on dots in filename)
        expected = Path(f"{output_path}.{self._config.codec.value}")
        if expected.exists():
            logger.debug("Skip existing file: %s", expected)
            self._enrich_local_copy(expected, track)
            if track.match_result == MatchResult.MATCHED:
                self._track_index.set(video_id, expected)
            return DownloadResult(
                track=track,
                status=DownloadStatus.SKIPPED,
                output_path=expected,
                video_id_used=video_id,
                skip_reason=SkipReason.FILE_EXISTS,
            )

        # Matched tracks: hardlink from an existing copy when possible
        # (Download-root dedupe or an already-ingested External/Organized file).
        if track.match_result == MatchResult.MATCHED:
            hardlinked = self._try_hardlink_existing(track, video_id, expected)
            if hardlinked is not None:
                return hardlinked

        try:
            download_stem = self._staging_stem(output_path)
            if self.staging_enabled:
                assert self._config.download_cache_path is not None
                self._ensure_free_space(
                    self._config.download_cache_path,
                    self._config.cache_min_free_gb,
                    "Download cache",
                )
            actual_path = self._downloader.download(
                video_id, download_stem, cancel_token
            )

            # Tag + lyrics + sidecars for a fresh download
            self._apply_metadata_tags(actual_path, track)

            final_path = (
                Path(f"{output_path}{actual_path.suffix}")
                if self.staging_enabled
                else actual_path
            )
            if track.match_result == MatchResult.MATCHED and not self.staging_enabled:
                self._track_index.set(video_id, actual_path)

            logger.debug("Downloaded: '%s'", actual_path)

            return DownloadResult(
                track=track,
                status=DownloadStatus.SUCCESS,
                output_path=actual_path,
                final_path=final_path if self.staging_enabled else None,
                video_id_used=video_id,
                origin="download",
            )
        except DownloadError as e:
            logger.error("Track '%s' failed: %s", track.title, e)
            return DownloadResult(
                track=track,
                status=DownloadStatus.FAILED,
                error=str(e),
                video_id_used=video_id,
            )

    def _try_hardlink_existing(
        self,
        track: TrackMetadata,
        video_id: str,
        dest: Path,
    ) -> DownloadResult | None:
        """Hardlink from a previously downloaded copy of the same video_id.

        Returns a DownloadResult on success or hardlink failure. Returns None
        when no existing source was found (caller should download).
        """
        source = self._track_index.get(video_id)
        if source is None or source.resolve() == dest.resolve():
            return None

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.link(source, dest)
        except OSError as e:
            msg = (
                f"Hardlink failed ({source} -> {dest}): {e}. "
                "Keep the data volume on a single filesystem."
            )
            logger.error(msg)
            return DownloadResult(
                track=track,
                status=DownloadStatus.FAILED,
                error=msg,
                video_id_used=video_id,
            )

        src_lrc = source.with_suffix(".lrc")
        dest_lrc = dest.with_suffix(".lrc")
        if src_lrc.is_file() and not dest_lrc.exists():
            try:
                os.link(src_lrc, dest_lrc)
            except OSError:
                try:
                    shutil.copy2(src_lrc, dest_lrc)
                except OSError:
                    logger.debug("Could not hardlink/copy lyrics: %s", src_lrc)

        self._enrich_local_copy(dest, track)

        # Keep the index pointing at the canonical source, not this new
        # hardlinked copy — the source is the one other placements should
        # link from (and, for External-sourced tracks, may be immutable).
        logger.debug("Hardlinked: '%s' <- '%s'", dest, source)
        detected = detect_storage_for_path(source)
        origin = (
            "library_link"
            if detected is not None and detected[0] == STORAGE_EXTERNAL
            else "dedupe_link"
        )
        return DownloadResult(
            track=track,
            status=DownloadStatus.HARDLINKED,
            output_path=dest,
            video_id_used=video_id,
            origin=origin,
        )

    # ============================================================================
    # VIDEO ID SELECTION - Choose best video variant for download
    # ============================================================================

    def _select_video_id_for_download(self, track: TrackMetadata) -> str:
        """Select the best video ID for downloading the track.

        Video ID Selection Priority:
        1. ATV (Audio Track Video) - Album version, best audio quality
        2. OMV (Official Music Video) - Fallback, may have different audio mix

        Why prefer ATV: Audio Track Videos contain the canonical album version
        with best audio quality. OMVs may have different mixing, radio edits,
        or background noise from the video.

        Args:
            track: Track metadata containing video IDs.

        Returns:
            Video ID to download.

        Raises:
            DownloadError: If no video ID is available for the track.
        """
        video_id = track.video_id
        if not video_id:
            raise DownloadError(
                f"No video ID available for track: '{track.title}' by {track.artist}"
            )
        return video_id

    # ============================================================================
    # PATH CONSTRUCTION - Build output paths from track metadata
    # ============================================================================

    def _build_output_path_for_track(self, track: TrackMetadata) -> Path:
        """Build output path for a track using organized directory structure.

        Matched tracks use: base_path/{library_folder}/Artist/YEAR - Album/Artist - Title
        Unmatched tracks use: base_path/Unmatched/Artist - Title [videoId]
        Unofficial tracks use: base_path/Unofficial/Artist - Title [videoId]

        The extension is added by yt-dlp during post-processing.

        Args:
            track: Track metadata.

        Returns:
            Output path (without extension, yt-dlp adds it during download).
        """
        match track.match_result:
            case MatchResult.UNMATCHED:
                return build_unmatched_track_path(
                    base=self._config.base_path,
                    artist=track.primary_album_artist,
                    title=track.title,
                    video_id=track.video_id or "unknown",
                    ascii_filenames=self._config.ascii_filenames,
                )
            case MatchResult.UNOFFICIAL:
                return build_unofficial_track_path(
                    base=self._config.base_path,
                    artist=track.primary_album_artist,
                    title=track.title,
                    video_id=track.video_id or "unknown",
                    ascii_filenames=self._config.ascii_filenames,
                )
            case MatchResult.MATCHED:
                library = self._library_folder or DIRECT_FOLDER
                track_root = self._config.base_path / library
                return build_track_path(
                    base=track_root,
                    artist=track.primary_album_artist,
                    year=track.year,
                    album=track.album,
                    track_number=track.track_number,
                    title=track.title,
                    ascii_filenames=self._config.ascii_filenames,
                )

    # ============================================================================
    # METADATA TAGGING - Embed ID3/MP4 tags, cover art, and lyrics
    # ============================================================================

    def _enrich_local_copy(self, path: Path, track: TrackMetadata) -> None:
        """Fill missing sidecars / lyrics / better covers for an existing file."""
        self._apply_best_cover(path, track)
        lyrics = self._ensure_lyrics_sidecar(path, track)
        if lyrics:
            self._ensure_embedded_lyrics(path, lyrics)

    def finalize_local_file(
        self, path: Path, track: TrackMetadata
    ) -> str | None:
        """Bring an externally-sourced audio file up to normal-download quality.

        Reuses the exact tagging / cover / lyrics helpers a fresh download runs,
        but never re-fetches audio. Writes ID3/MP4 tags, embeds the best
        available cover, and fetches + embeds lyrics (also saving a ``.lrc``
        sidecar next to the file). Folder-level ``cover.jpg`` / ``artist.jpg``
        sidecars are intentionally skipped so flat buckets (e.g. the Direct
        download folder) are not polluted. Returns the lyrics text when found.
        """
        return self.enrich_file(path, track, rewrite_metadata=True).lyrics

    def enrich_file(
        self,
        path: Path,
        track: TrackMetadata,
        *,
        rewrite_metadata: bool = False,
        respect_lyrics_cooldown: bool = True,
    ) -> EnrichmentOutcome:
        """Enrich an existing file and report authoritative asset state.

        Reuses the exact cover / lyrics / tagging helpers a fresh download
        runs (Apple/lyrics cooldowns included) but never re-fetches audio.
        By default only missing/better cover and lyrics assets are applied;
        existing title/artist/album tags are left untouched. Set
        ``rewrite_metadata`` only when finalizing a newly imported raw file.
        Cover and lyrics are independent: a cover failure never skips lyrics.
        Re-probes the file afterwards so callers can persist accurate
        ``has_embedded_cover`` / lyrics flags and the resolved cover source.
        Tagging failures are captured in ``error`` instead of raised.
        """
        error: str | None = None
        lyrics: str | None = None
        cover: bytes | None = None
        try:
            cover = self._resolve_best_cover_bytes(path, track)
        except Exception as e:
            logger.exception("Failed to resolve cover for %s: %s", path, e)
            error = str(e) or e.__class__.__name__

        try:
            lyrics = self._ensure_lyrics_sidecar(
                path,
                track,
                respect_cooldown=respect_lyrics_cooldown,
            )
            if rewrite_metadata:
                self._tagger.apply_metadata_tags(path, track, cover, lyrics)
            else:
                if cover:
                    current = read_embedded_cover(path)
                    if (
                        current is None
                        or cover_quality_score(cover)
                        > cover_quality_score(current[0])
                    ):
                        from mediafile import Image, MediaFile

                        audio = MediaFile(path)
                        audio.images = [Image(data=cover)]
                        audio.save()
                if lyrics:
                    self._ensure_embedded_lyrics(path, lyrics)
        except Exception as e:  # report, never abort a batch pass
            logger.exception("Failed to enrich %s: %s", path, e)
            if error is None:
                error = str(e) or e.__class__.__name__

        has_cover = False
        has_lyrics_embedded = False
        try:
            from mediafile import MediaFile

            audio = MediaFile(path)
            has_cover = bool(audio.images)
            has_lyrics_embedded = bool(audio.lyrics and str(audio.lyrics).strip())
        except Exception:
            pass
        has_lyrics_sidecar = path.with_suffix(".lrc").is_file()

        cover_source: str | None = None
        lyrics_source: str | None = None
        video_id = self._video_key(track)
        if video_id:
            state = self._scrape_state.get(video_id)
            cover_source = state.cover_source
            lyrics_source = state.lyrics_source

        return EnrichmentOutcome(
            has_embedded_cover=has_cover,
            has_lyrics_embedded=has_lyrics_embedded,
            has_lyrics_sidecar=has_lyrics_sidecar,
            cover_source=cover_source,
            lyrics=lyrics,
            lyrics_source=lyrics_source,
            error=error,
        )

    def _apply_metadata_tags(
        self, path: Path, track: TrackMetadata, lyrics: str | None = None
    ) -> None:
        """Apply ID3/MP4 metadata tags and embed best cover / lyrics.

        Fresh-download path: lyrics ignore prior miss cooldowns so every source
        is tried in this pass. Cover failures never skip the lyrics attempt.
        """
        cover: bytes | None = None
        try:
            cover = self._resolve_best_cover_bytes(path, track)
        except Exception as e:
            logger.exception("Failed to resolve cover for %s: %s", path, e)

        try:
            if lyrics is None:
                lyrics = self._ensure_lyrics_sidecar(
                    path, track, respect_cooldown=False
                )
            self._tagger.apply_metadata_tags(path, track, cover, lyrics)
            if cover:
                self._write_folder_sidecars(path, cover)
        except Exception as e:
            logger.exception("Failed to tag %s: %s", path, e)

    def _video_key(self, track: TrackMetadata) -> str:
        return track.video_id or track.source_video_id or ""

    def _resolve_best_cover_bytes(
        self, path: Path, track: TrackMetadata
    ) -> bytes | None:
        """Pick cover via probe-or-download comparison (no permanent Apple seal).

        Comparison set is embedded + Apple + YTM. Excellence threshold (config)
        permanently skips remotes. Otherwise shelf life: probe 7d / download 30d
        is enforced by callers via scrape state + tier computation; this method
        skips remotes while the prior round is still fresh.
        """
        from datetime import UTC, datetime

        from yubal.utils.cover_quality import (
            cover_comparison_fresh,
            cover_meets_excellence,
        )

        video_id = self._video_key(track)
        state = self._scrape_state.get(video_id) if video_id else None
        excellence = int(getattr(self._config, "cover_excellence_px", 0) or 0)

        embedded = None
        if path.is_file():
            got = read_embedded_cover(path)
            if got:
                embedded = got[0]
        local_dims = image_dimensions(embedded) if embedded else None

        def _persist(
            *,
            source: str | None,
            check_kind: str,
            dims: tuple[int, int] | None,
            data: bytes | None,
        ) -> bytes | None:
            if not video_id:
                return data if data is not None else embedded
            new_state = self._scrape_state.get(video_id)
            now = datetime.now(UTC)
            new_state.cover_compared_at = now
            new_state.cover_check_kind = check_kind
            new_state.apple_checked_at = now
            if dims:
                new_state.cover_width, new_state.cover_height = dims
            elif data:
                measured = image_dimensions(data)
                if measured:
                    new_state.cover_width, new_state.cover_height = measured
            if source == "apple":
                new_state.cover_source = COVER_APPLE
            elif source == "ytmusic":
                new_state.cover_source = COVER_YTM
            elif source == "embedded" or (source is None and embedded):
                if new_state.cover_source != COVER_APPLE:
                    new_state.cover_source = COVER_EMBEDDED
            self._scrape_state.set(video_id, new_state)
            return data if data is not None else embedded

        # Permanent seal: resolution already good enough.
        if local_dims and cover_meets_excellence(
            local_dims[0], local_dims[1], excellence
        ):
            return _persist(
                source="embedded",
                check_kind=COVER_CHECK_PROBE,
                dims=local_dims,
                data=embedded,
            )

        # Still within shelf life from a prior round — keep local.
        if state and cover_comparison_fresh(
            state.effective_compared_at(),
            state.effective_check_kind(),
            probe_days=int(
                getattr(self._config, "cover_probe_fresh_days", 7) or 7
            ),
            download_days=int(
                getattr(self._config, "cover_download_fresh_days", 30) or 30
            ),
        ):
            if local_dims and video_id and (
                state.cover_width is None or state.cover_height is None
            ):
                new_state = self._scrape_state.get(video_id)
                new_state.cover_width, new_state.cover_height = local_dims
                self._scrape_state.set(video_id, new_state)
            return embedded

        artist = track.artists[0] if track.artists else track.artist
        local_area = (local_dims[0] * local_dims[1]) if local_dims else 0

        # Probe remotes without full image download when possible.
        remote_areas: list[int] = []
        apple_meta = None
        try:
            apple_meta = search_apple_cover_url(
                artist=artist, album=track.album, title=track.title
            )
        except Exception:
            logger.debug("Apple cover probe failed for %s", track.title, exc_info=True)
        if apple_meta:
            remote_areas.append(apple_meta[1] * apple_meta[2])

        ytm_dims = None
        if track.cover_url:
            try:
                ytm_dims = probe_image_dimensions(track.cover_url)
            except Exception:
                logger.debug(
                    "YTM cover probe failed for %s", track.title, exc_info=True
                )
            if ytm_dims:
                remote_areas.append(ytm_dims[0] * ytm_dims[1])

        # No usable probe data, or a remote claims a larger canvas → full download.
        if embedded is None:
            need_download = True
        elif not remote_areas:
            need_download = True
        else:
            need_download = max(remote_areas) > local_area

        # Small embedded/YTM thumbs: always run a full Apple+YTM comparison when
        # the shelf has expired (we only reach here when not fresh).
        if (
            not need_download
            and local_dims is not None
            and min(local_dims) < 600
        ):
            need_download = True

        if not need_download:
            logger.info(
                "Cover probe kept local for '%s' (%s) — remotes not larger",
                track.title,
                f"{local_dims[0]}x{local_dims[1]}" if local_dims else "unknown",
            )
            return _persist(
                source="embedded",
                check_kind=COVER_CHECK_PROBE,
                dims=local_dims,
                data=embedded,
            )

        best = select_best_cover(
            embedded=embedded,
            ytm_url=track.cover_url,
            artist=artist,
            album=track.album,
            title=track.title,
            allow_apple=True,
            fetch_ytm=True,
        )
        if best is None:
            return _persist(
                source="embedded" if embedded else None,
                check_kind=COVER_CHECK_DOWNLOAD,
                dims=local_dims,
                data=embedded,
            )
        return _persist(
            source=best.source,
            check_kind=COVER_CHECK_DOWNLOAD,
            dims=best.dims,
            data=best.data,
        )

    def _apply_best_cover(self, path: Path, track: TrackMetadata) -> None:
        """Re-evaluate covers and rewrite embed + sidecars when a better one wins."""
        try:
            cover = self._resolve_best_cover_bytes(path, track)
            if not cover:
                return
            current = read_embedded_cover(path)
            if current is None or cover_quality_score(cover) > cover_quality_score(
                current[0]
            ):
                from mediafile import Image, MediaFile

                audio = MediaFile(path)
                audio.images = [Image(data=cover)]
                audio.save()
            self._write_folder_sidecars(path, cover)
        except Exception as e:
            logger.debug("Could not refresh cover for %s: %s", path, e)

    def _write_folder_sidecars(self, path: Path, cover: bytes) -> None:
        """Write album ``cover.jpg`` and artist ``artist.jpg`` when better/missing."""
        album_dir = path.parent
        write_better_image(album_dir / "cover.jpg", cover)
        artist_dir = album_dir.parent
        if artist_dir != self._config.base_path and artist_dir.name:
            write_better_image(artist_dir / "artist.jpg", cover)

    def _duration_seconds(
        self, path: Path, track: TrackMetadata
    ) -> int | None:
        """Best duration for lyrics lookup: metadata, else audio length."""
        raw = track.duration_seconds
        if raw is not None:
            try:
                seconds = int(raw)
            except (TypeError, ValueError):
                seconds = 0
            if seconds > 0:
                return seconds
        if not path.is_file():
            return None
        try:
            from mediafile import MediaFile

            length = MediaFile(path).length
            if length is not None and float(length) > 0:
                return max(1, int(round(float(length))))
        except Exception:
            logger.debug("Could not read duration from %s", path, exc_info=True)
        return None

    def _ensure_lyrics_sidecar(
        self,
        path: Path,
        track: TrackMetadata,
        *,
        respect_cooldown: bool = True,
    ) -> str | None:
        """Return lyrics text; never re-query when local lyrics already exist.

        When local lyrics are plain (no timestamps), still try remotes once for
        synced text in this call. Fresh downloads pass ``respect_cooldown=False``
        so a prior miss does not skip sources in the same import pass.
        """
        from datetime import UTC, datetime

        from yubal.services.lyrics import lyrics_look_synced

        video_id = self._video_key(track)
        lrc_path = path.with_suffix(".lrc")

        # Local lyrics win forever when already synced.
        existing: str | None = None
        if lrc_path.is_file():
            try:
                existing = (
                    lrc_path.read_text(encoding="utf-8", errors="ignore").strip()
                    or None
                )
            except OSError:
                existing = None
        if not existing and path.is_file():
            try:
                from mediafile import MediaFile

                audio = MediaFile(path)
                if audio.lyrics and str(audio.lyrics).strip():
                    existing = str(audio.lyrics).strip()
            except Exception:
                pass

        if existing and lyrics_look_synced(existing):
            if video_id:
                state = self._scrape_state.get(video_id)
                state.has_lyrics = True
                self._scrape_state.set(video_id, state)
            return existing

        if not self._lyrics_service:
            return existing

        duration = self._duration_seconds(path, track)
        # Need duration for lrclib/QQ, or video_id for YTM-only attempts.
        if not duration and not (track.video_id or track.source_video_id):
            return existing

        cooldown = self._config.scrape_cooldown_hours
        skip: set[str] = set()
        if respect_cooldown and cooldown > 0 and video_id:
            state = self._scrape_state.get(video_id)
            for name in ("lrclib", "YouTube Music", "qq"):
                if self._scrape_state.lyrics_source_in_cooldown(state, name, cooldown):
                    skip.add(name)
                    logger.debug(
                        "Lyrics source %s in cooldown for '%s'", name, track.title
                    )

        lyrics, hit_source, missed = self._lyrics_service.fetch_lyrics(
            title=track.title,
            artist=track.artists[0] if track.artists else (track.artist or ""),
            duration_seconds=int(duration or 0),
            video_id=track.video_id or track.source_video_id,
            skip_sources=skip or None,
        )

        if video_id:
            state = self._scrape_state.get(video_id)
            now = datetime.now(UTC)
            for source in missed:
                state.lyrics_checked[source] = now
            if lyrics and hit_source:
                state.has_lyrics = True
                # Record which provider won so the catalog can show provenance.
                state.lyrics_source = _normalize_lyrics_source(hit_source)
                # Successful hit: no need to keep miss cooldown for that source
                state.lyrics_checked.pop(hit_source, None)
            self._scrape_state.set(video_id, state)

        if lyrics:
            self._lyrics_service.save_lyrics(lyrics, path)
            logger.debug("Saved lyrics: %s", lrc_path)
            return lyrics
        return existing

    def _ensure_embedded_lyrics(self, path: Path, lyrics: str) -> None:
        """Embed lyrics into an existing file when the lyrics tag is empty."""
        try:
            from mediafile import MediaFile

            audio = MediaFile(path)
            if audio.lyrics and str(audio.lyrics).strip():
                return
            audio.lyrics = lyrics.strip()
            audio.save()
        except Exception as e:
            logger.debug("Could not embed lyrics into %s: %s", path, e)
