"""Read embedded cover art and sidecar lyrics from library audio files."""

from __future__ import annotations

import logging
from pathlib import Path

from mediafile import MediaFile, UnreadableFileError

logger = logging.getLogger(__name__)


def read_embedded_cover(path: Path) -> tuple[bytes, str] | None:
    """Return (image bytes, mime type) from embedded artwork, if any."""
    try:
        audio = MediaFile(path)
    except UnreadableFileError:
        return None
    except OSError as e:
        logger.debug("Could not read cover from %s: %s", path, e)
        return None

    if not audio.images:
        return None

    image = audio.images[0]
    data = image.data
    if not data:
        return None

    mime = (image.mime_type or "image/jpeg").strip() or "image/jpeg"
    return data, mime


def read_lyrics_sidecar(audio_path: Path) -> str | None:
    """Return synced lyrics text from a sibling ``.lrc`` file, if present."""
    lrc_path = audio_path.with_suffix(".lrc")
    if not lrc_path.is_file():
        return None
    try:
        text = lrc_path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None
    return text or None


def read_embedded_lyrics(path: Path) -> str | None:
    """Return lyrics text embedded in the audio file tags, if any."""
    try:
        audio = MediaFile(path)
    except UnreadableFileError:
        return None
    except OSError as e:
        logger.debug("Could not read lyrics from %s: %s", path, e)
        return None

    raw = audio.lyrics
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def write_lyrics_sidecar(audio_path: Path, lyrics: str) -> Path:
    """Write sibling ``.lrc`` next to the audio file (UTF-8)."""
    lrc_path = audio_path.with_suffix(".lrc")
    lrc_path.write_text(lyrics.strip() + "\n", encoding="utf-8")
    return lrc_path


def write_embedded_lyrics(path: Path, lyrics: str) -> None:
    """Embed lyrics text into the audio file tags."""
    audio = MediaFile(path)
    text = lyrics.strip()
    audio.lyrics = text if text else None
    audio.save()
