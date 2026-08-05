"""Persistent Telegram helpers: file_id cache and daily quota."""

from __future__ import annotations

import json
import logging
import threading
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class FileIdStore:
    """Map video_id → Telegram file_id (+ delivery kind) for the current bot."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        # video_id → {"file_id": str, "kind": str}
        self._data: dict[str, dict[str, str]] = {}
        self._load()

    def get(self, video_id: str, *, kind: str | None = None) -> str | None:
        """Return cached file_id when present and kind matches (if required).

        Legacy bare-string entries are treated as kind ``legacy`` and never
        match an explicit audio/document request (forces one re-upload).
        """
        with self._lock:
            entry = self._data.get(video_id)
            if not entry:
                return None
            fid = (entry.get("file_id") or "").strip()
            if not fid:
                return None
            if kind is not None and entry.get("kind") != kind:
                return None
            return fid

    def put(self, video_id: str, file_id: str, *, kind: str) -> None:
        vid = (video_id or "").strip()
        fid = (file_id or "").strip()
        k = (kind or "").strip()
        if not vid or not fid or not k:
            return
        with self._lock:
            prev = self._data.get(vid)
            if prev and prev.get("file_id") == fid and prev.get("kind") == k:
                return
            self._data[vid] = {"file_id": fid, "kind": k}
            self._save_unlocked()

    def clear(self) -> None:
        with self._lock:
            self._data = {}
            self._path.unlink(missing_ok=True)

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load telegram file_ids: %s", exc)
            return
        if not isinstance(raw, dict):
            return
        data: dict[str, dict[str, str]] = {}
        for key, value in raw.items():
            if not key:
                continue
            vid = str(key)
            if isinstance(value, str) and value.strip():
                # Pre-kind cache: do not reuse for typed send* calls.
                data[vid] = {"file_id": value.strip(), "kind": "legacy"}
            elif isinstance(value, dict):
                fid = str(value.get("file_id") or "").strip()
                kind = str(value.get("kind") or "").strip() or "legacy"
                if fid:
                    data[vid] = {"file_id": fid, "kind": kind}
        self._data = data

    def _save_unlocked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except OSError as exc:
            logger.error("Failed to save telegram file_ids: %s", exc)


class DailyQuota:
    """Per-user daily counter for online search / preview."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._day: str = ""
        self._counts: dict[str, int] = {}
        self._load()

    def remaining(self, user_id: int, limit: int) -> int:
        self._roll()
        with self._lock:
            used = self._counts.get(str(user_id), 0)
            return max(0, limit - used)

    def consume(self, user_id: int, limit: int) -> bool:
        """Return True if the action is allowed and counted."""
        self._roll()
        with self._lock:
            key = str(user_id)
            used = self._counts.get(key, 0)
            if used >= limit:
                return False
            self._counts[key] = used + 1
            self._save_unlocked()
            return True

    def clear(self) -> None:
        with self._lock:
            self._day = date.today().isoformat()
            self._counts = {}
            self._path.unlink(missing_ok=True)

    def _roll(self) -> None:
        today = date.today().isoformat()
        with self._lock:
            if self._day == today:
                return
            self._day = today
            self._counts = {}
            self._save_unlocked()

    def _load(self) -> None:
        if not self._path.exists():
            self._day = date.today().isoformat()
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load telegram quota: %s", exc)
            self._day = date.today().isoformat()
            return
        self._day = str(raw.get("day") or date.today().isoformat())
        counts = raw.get("counts") or {}
        if isinstance(counts, dict):
            self._counts = {
                str(k): int(v)
                for k, v in counts.items()
                if str(k).lstrip("-").isdigit()
            }
        if self._day != date.today().isoformat():
            self._day = date.today().isoformat()
            self._counts = {}

    def _save_unlocked(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {"day": self._day, "counts": self._counts}
        tmp = self._path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            tmp.replace(self._path)
        except OSError as exc:
            logger.error("Failed to save telegram quota: %s", exc)
