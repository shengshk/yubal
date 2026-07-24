"""Send library tracks to Telegram with format-aware delivery."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from yubal_api.services.telegram.client import BotApiClient
from yubal_api.services.telegram.stores import FileIdStore

logger = logging.getLogger(__name__)

OFFICIAL_MAX_BYTES = 49 * 1024 * 1024
TRANSCODE_DIR = Path("/data/cache/tg-transcode")
_BAD_NAME = re.compile(r'[\x00-\x1f\\/:*?"<>|]+')
# Empirically playable as music bubbles via sendAudio (local tgapi).
# ogg/opus become voice (no title); webm is unreliable → document.
_INLINE_AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".wav", ".aac"}


def extract_file_id(message: dict) -> str | None:
    file_id, _kind = extract_delivery(message)
    return file_id


def extract_delivery(message: dict) -> tuple[str | None, str | None]:
    """Return (file_id, kind) preferring audio over document over voice."""
    for key in ("audio", "document", "voice"):
        block = message.get(key)
        if isinstance(block, dict) and block.get("file_id"):
            return str(block["file_id"]), key
    return None, None


def display_filename(
    path: Path, *, title: str | None, performer: str | None
) -> str:
    artist = _safe_component(performer) or "Unknown Artist"
    track = _safe_component(title) or "Unknown Track"
    ext = path.suffix.lower() if path.suffix else ""
    name = f"{artist} - {track}{ext}"
    if len(name.encode("utf-8")) > 200:
        name = f"{artist[:40]} - {track[:40]}{ext}"
    return name


def probe_duration_seconds(path: Path) -> int | None:
    """Best-effort duration for sendAudio progress UI."""
    try:
        from mediafile import MediaFile

        length = MediaFile(path).length
        if length is None:
            return None
        seconds = int(round(float(length)))
        return seconds if seconds > 0 else None
    except Exception:
        logger.debug("Could not probe duration for %s", path, exc_info=True)
        return None


def _safe_component(value: str | None) -> str:
    text = (value or "").strip()
    text = _BAD_NAME.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text


class AudioSender:
    """Deliver a track; prefer in-app audio when the format supports it."""

    def __init__(self, client: BotApiClient, file_ids: FileIdStore) -> None:
        self._client = client
        self._file_ids = file_ids

    async def send(
        self,
        chat_id: int,
        *,
        video_id: str,
        path: Path | None,
        title: str | None = None,
        performer: str | None = None,
        remember: bool = True,
    ) -> dict:
        label = display_filename(
            path if path is not None else Path(f"{video_id}.bin"),
            title=title,
            performer=performer,
        )
        inline = bool(path is not None and path.suffix.lower() in _INLINE_AUDIO_EXTS)
        kind = "audio" if inline else "document"
        cached = self._file_ids.get(video_id, kind=kind) if path is not None else None
        if cached:
            try:
                if inline:
                    duration = (
                        probe_duration_seconds(path) if path is not None else None
                    )
                    result = await self._client.send_audio_by_file_id(
                        chat_id,
                        cached,
                        title=title,
                        performer=performer,
                        duration=duration,
                    )
                else:
                    result = await self._client.send_document_by_file_id(
                        chat_id, cached
                    )
                logger.info(
                    "TG send via file_id chat=%s video=%s name=%s kind=%s",
                    chat_id,
                    video_id,
                    label,
                    kind,
                )
                return result
            except Exception:
                logger.info(
                    "Cached Telegram file_id unusable for %s; re-uploading",
                    video_id,
                )

        if path is None or not path.is_file():
            raise FileNotFoundError(f"No audio file for {video_id}")

        send_path = path
        temp: Path | None = None
        try:
            if not self._client.is_local and path.stat().st_size > OFFICIAL_MAX_BYTES:
                temp = await self._transcode(path)
                send_path = temp
            filename = display_filename(
                send_path, title=title, performer=performer
            )
            inline = send_path.suffix.lower() in _INLINE_AUDIO_EXTS
            kind = "audio" if inline else "document"
            logger.info(
                "TG send via upload chat=%s video=%s name=%s size=%s kind=%s",
                chat_id,
                video_id,
                filename,
                send_path.stat().st_size,
                kind,
            )
            if inline:
                result = await self._client.send_audio_path(
                    chat_id,
                    send_path,
                    filename=filename,
                    title=title,
                    performer=performer,
                    duration=probe_duration_seconds(send_path),
                )
            else:
                result = await self._client.send_document_path(
                    chat_id, send_path, filename=filename
                )
        finally:
            if temp is not None:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass

        if remember:
            file_id, delivered = extract_delivery(result)
            # Only cache when Telegram kept the intended delivery kind.
            # (ogg/opus via sendAudio become voice — do not cache as audio.)
            if file_id and delivered == kind:
                self._file_ids.put(video_id, file_id, kind=delivered)
                logger.info(
                    "TG file_id cached video=%s name=%s kind=%s",
                    video_id,
                    filename,
                    delivered,
                )
        return result

    async def _transcode(self, source: Path) -> Path:
        if shutil.which("ffmpeg") is None:
            raise RuntimeError("File too large and ffmpeg is not available")
        TRANSCODE_DIR.mkdir(parents=True, exist_ok=True)
        dest = TRANSCODE_DIR / f"{uuid.uuid4().hex}.mp3"
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "2",
            str(dest),
        ]
        logger.info("Transcoding oversized Telegram upload: %s", source.name)
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0 or not dest.is_file():
            dest.unlink(missing_ok=True)
            raise RuntimeError(
                f"ffmpeg failed: {(proc.stderr or proc.stdout or '')[-400:]}"
            )
        if dest.stat().st_size > OFFICIAL_MAX_BYTES:
            dest.unlink(missing_ok=True)
            raise RuntimeError("Transcoded file still exceeds Telegram limit")
        return dest
