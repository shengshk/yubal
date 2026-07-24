"""Download result models and statistics."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from yubal.models.enums import DownloadStatus, SkipReason
from yubal.models.track import PlaylistInfo, TrackMetadata


class PhaseStats(BaseModel):
    """Statistics for a processing phase (extraction or download).

    Uses dictionary-based skip reason counts for scalability.
    Adding new skip reasons only requires updating the SkipReason enum.

    Attributes:
        success: Number of newly downloaded files (real downloads).
        hardlinked: Number of tracks linked to an existing local file.
        failed: Number of failed items.
        skipped_by_reason: Count of skipped items by reason.
    """

    model_config = ConfigDict(frozen=True)

    success: int = 0
    hardlinked: int = 0
    failed: int = 0
    skipped_by_reason: dict[SkipReason, int] = Field(default_factory=dict)

    @property
    def skipped(self) -> int:
        """Total number of skipped items across all reasons."""
        return sum(self.skipped_by_reason.values())

    @property
    def total(self) -> int:
        """Total items processed (success + hardlinked + failed + skipped)."""
        return self.success + self.hardlinked + self.failed + self.skipped


class DownloadResult(BaseModel):
    """Result of a single track download.

    Attributes:
        track: The track metadata that was downloaded.
        status: The download status.
        output_path: Path to the downloaded file (if successful).
        error: Error message (if failed).
        video_id_used: The video ID that was used for download.
        skip_reason: Why the track was skipped (if status is SKIPPED).
        origin: How the file entered B (download / preselect_link / …).
    """

    model_config = ConfigDict(frozen=True)

    track: TrackMetadata
    status: DownloadStatus
    output_path: Path | None = None
    final_path: Path | None = None
    error: str | None = None
    video_id_used: str | None = None
    skip_reason: SkipReason | None = None
    origin: str | None = None


def get_audio_bitrate(path: Path | None) -> int | None:
    """Get audio bitrate in kbps from a file on disk.

    Args:
        path: Path to the audio file.

    Returns:
        Bitrate in kbps, or None if the file doesn't exist or can't be read.
    """
    if not path or not path.exists():
        return None
    try:
        from mediafile import MediaFile

        audio = MediaFile(path)
        return audio.bitrate // 1000 if audio.bitrate else None
    except Exception:
        return None


def aggregate_skip_reasons(
    results: list[DownloadResult],
) -> dict[SkipReason, int]:
    """Aggregate skip reasons from download results into a count dictionary.

    This utility extracts skip reason counts from a list of download results,
    useful for logging and stats computation.

    Args:
        results: List of download results to aggregate.

    Returns:
        Dictionary mapping each encountered SkipReason to its count.

    Example:
        >>> reasons = aggregate_skip_reasons(download_results)
        >>> reasons[SkipReason.FILE_EXISTS]  # 5
    """
    counts: dict[SkipReason, int] = {}
    for result in results:
        if result.status == DownloadStatus.SKIPPED and result.skip_reason:
            counts[result.skip_reason] = counts.get(result.skip_reason, 0) + 1
    return counts


class PlaylistDownloadResult(BaseModel):
    """Complete result of a playlist download operation.

    Returned by PlaylistDownloadService after completing a download.

    Attributes:
        playlist_info: Metadata about the downloaded playlist.
        download_results: Results for each track download.
        m3u_path: Path to the generated M3U file (if created).
        cover_path: Path to the saved cover image (if created).
    """

    model_config = ConfigDict(frozen=True)

    playlist_info: PlaylistInfo
    download_results: list[DownloadResult]
    source_tracks: list[TrackMetadata] = Field(default_factory=list)
    m3u_path: Path | None = None
    cover_path: Path | None = None

    @property
    def success_count(self) -> int:
        """Newly acquired tracks (yt-dlp download or preselect into B)."""
        return sum(
            1
            for r in self.download_results
            if r.status in (DownloadStatus.SUCCESS, DownloadStatus.PRESELECTED)
        )

    @property
    def preselected_count(self) -> int:
        """Tracks placed from the preselect library."""
        return sum(
            1 for r in self.download_results if r.status == DownloadStatus.PRESELECTED
        )

    @property
    def hardlinked_count(self) -> int:
        """Number of tracks hardlinked to an existing local file."""
        return sum(
            1 for r in self.download_results if r.status == DownloadStatus.HARDLINKED
        )

    @property
    def skipped_count(self) -> int:
        """Number of skipped tracks (already exist)."""
        return sum(
            1 for r in self.download_results if r.status == DownloadStatus.SKIPPED
        )

    @property
    def failed_count(self) -> int:
        """Number of failed downloads."""
        return sum(
            1 for r in self.download_results if r.status == DownloadStatus.FAILED
        )

    @property
    def download_stats(self) -> PhaseStats:
        """Compute download phase statistics with skip reason breakdown."""
        return PhaseStats(
            success=self.success_count,
            hardlinked=self.hardlinked_count,
            failed=self.failed_count,
            skipped_by_reason=aggregate_skip_reasons(self.download_results),
        )
