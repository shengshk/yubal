"""Cover art fetching with caching."""

import logging
import re
import threading
import time
import urllib.request
from importlib.metadata import version
from pathlib import Path
from urllib.error import HTTPError, URLError

from yubal.utils.filename import format_playlist_filename

logger = logging.getLogger(__name__)

# Get version from package metadata for User-Agent
_VERSION = version("yubal")


class CoverCache:
    """Thread-safe cover art cache with explicit lifecycle management.

    This class provides caching for cover art downloads to avoid
    redundant network requests for the same album artwork.
    Uses threading.Lock for thread-safe concurrent access.
    """

    __slots__ = ("_cache", "_lock")

    def __init__(self) -> None:
        """Initialize an empty cover cache with thread lock."""
        self._cache: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def fetch(self, url: str | None, timeout: float = 30.0) -> bytes | None:
        """Fetch cover art from URL with caching.

        Thread-safe: uses lock for cache access to prevent race conditions.

        Args:
            url: Cover art URL.
            timeout: Request timeout in seconds.

        Returns:
            Cover image bytes or None if unavailable.
        """
        if not url:
            return None

        # Check cache with lock
        with self._lock:
            if url in self._cache:
                logger.debug("Cover cache hit: %s", url)
                return self._cache[url]

        # Fetch outside lock to avoid blocking other threads
        data = self._fetch_from_network(url, timeout)

        if data:
            with self._lock:
                self._cache[url] = data

        return data

    def _fetch_from_network(self, url: str, timeout: float) -> bytes | None:
        """Fetch cover art from network with a few retries."""
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                request = urllib.request.Request(
                    url,
                    headers={"User-Agent": f"yubal/{_VERSION}"},
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    data = response.read()
                    logger.debug("Fetched cover: %s (%d bytes)", url, len(data))
                    return data
            except (HTTPError, URLError, OSError, TimeoutError) as e:
                last_error = e
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue
        logger.warning("Failed to fetch cover from %s: %s", url, last_error)
        return None

    def clear(self) -> None:
        """Clear the cover art cache. Thread-safe."""
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        """Get the number of cached cover images. Thread-safe."""
        with self._lock:
            return len(self._cache)


# Default instance for backwards compatibility
_default_cache = CoverCache()


def fetch_cover(url: str | None, timeout: float = 30.0) -> bytes | None:
    """Fetch cover art from URL with caching.

    Args:
        url: Cover art URL.
        timeout: Request timeout in seconds.

    Returns:
        Cover image bytes or None if unavailable.
    """
    return _default_cache.fetch(url, timeout)


def clear_cover_cache() -> None:
    """Clear the cover art cache."""
    _default_cache.clear()


def get_cover_cache_size() -> int:
    """Get the number of cached cover images.

    Returns:
        Number of URLs currently cached.
    """
    return len(_default_cache)


def write_image_if_missing(path: Path, data: bytes) -> Path | None:
    """Write image bytes to ``path`` when the file does not already exist."""
    if path.exists():
        return path
    return write_image(path, data)


def write_image(path: Path, data: bytes) -> Path | None:
    """Write image bytes to ``path``, creating parents as needed."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError as e:
        logger.warning("Failed to write image %s: %s", path, e)
        return None
    logger.debug("Wrote image: %s", path)
    return path


def write_better_image(path: Path, data: bytes) -> Path | None:
    """Write ``data`` when path is missing or existing image scores lower."""
    from yubal.utils.image_quality import cover_quality_score

    if path.is_file():
        try:
            existing = path.read_bytes()
        except OSError:
            existing = b""
        if existing and cover_quality_score(existing) >= cover_quality_score(data):
            return path
    return write_image(path, data)


_ALBUM_COVER_NAMES = (
    "cover.jpg",
    "cover.png",
    "cover.jpeg",
    "cover.webp",
    "folder.jpg",
    "Folder.jpg",
    "Cover.jpg",
)


def find_album_folder_cover(audio_path: Path) -> Path | None:
    """Return album ``cover.*`` / ``folder.jpg`` beside an audio file, if any."""
    parent = audio_path.parent
    for name in _ALBUM_COVER_NAMES:
        candidate = parent / name
        if candidate.is_file():
            return candidate
    return None


def find_playlist_folder_cover(folder: Path) -> Path | None:
    """Locate a playlist sidecar cover in ``folder`` (next to ``.m3u`` or ``*[id].jpg``)."""
    if not folder.is_dir():
        return None
    try:
        m3us = sorted(folder.glob("*.m3u"))
    except OSError:
        return None
    for m3u in m3us:
        for suffix in (".jpg", ".jpeg", ".png", ".webp"):
            candidate = m3u.with_suffix(suffix)
            if candidate.is_file():
                return candidate
    # Glob treats ``[]`` as a character class; match playlist sidecars via regex.
    sidecar = re.compile(r".+\[[^\]]+\]\.(jpg|jpeg|png|webp)$", re.IGNORECASE)
    try:
        hits = sorted(
            p for p in folder.iterdir() if p.is_file() and sidecar.match(p.name)
        )
    except OSError:
        return None
    return hits[0] if hits else None


def write_playlist_cover(
    base_path: Path,
    playlist_name: str,
    playlist_id: str,
    cover_url: str | None,
    *,
    ascii_filenames: bool = False,
) -> tuple[Path | None, str | None]:
    """Write a playlist cover image as a sidecar file.

    Creates a JPEG file with the same name as the playlist M3U file.
    Most media players (Jellyfin, Plex, foobar2000) will automatically
    pick up this sidecar image.

    When the sidecar already exists and is non-empty, the network fetch is
    skipped entirely (no re-download on every sync).

    Args:
        base_path: Playlist save folder (e.g., ./data/Liked Songs).
        playlist_name: Name of the playlist (will be sanitized for filename).
        playlist_id: Unique playlist ID (last 8 chars appended to filename).
        cover_url: URL of the cover image to download.
        ascii_filenames: If True, transliterate unicode to ASCII in filenames.

    Returns:
        ``(path, status)`` where status is ``\"skipped\"`` (kept existing),
        ``\"written\"`` (fetched and saved), or ``None`` on failure / no URL.
    """
    base_path.mkdir(parents=True, exist_ok=True)
    filename = format_playlist_filename(
        playlist_name, playlist_id, ascii_filenames=ascii_filenames
    )
    cover_path = base_path / f"{filename}.jpg"

    # Existing sidecar: do not re-fetch on every playlist sync.
    if cover_path.is_file():
        try:
            if cover_path.stat().st_size > 0:
                logger.debug("Playlist cover exists, skip fetch: %s", cover_path)
                return cover_path, "skipped"
        except OSError:
            pass

    if not cover_url:
        return None, None

    cover_data = fetch_cover(cover_url)
    if not cover_data:
        return None, None

    written = write_better_image(cover_path, cover_data)
    if written is None:
        return None, None

    logger.debug("Wrote playlist cover: %s", written)
    return written, "written"
