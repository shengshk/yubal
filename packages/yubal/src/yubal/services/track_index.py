"""Persistent video_id → file path index for hardlink deduplication.

Entries are stored as ``"<storage>:<relative>"`` (e.g. ``"download:Direct/x.opus"``
or ``"external:Organized/DIR/Artist/Album/Song.flac"``), relative to the
respective library root (see ``yubal.utils.library.STORAGE_ROOTS``). Legacy
bare-path entries (pre dual-root) are treated as ``download:`` on load.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from yubal.utils.library import (
    DIRECT_FOLDER,
    STORAGE_DOWNLOAD,
    STORAGE_EXTERNAL,
    STORAGE_ROOTS,
    detect_storage_for_path,
    track_index_path,
)

logger = logging.getLogger(__name__)


def _normalize_rel(path: str) -> str:
    return path.strip().replace("\\", "/")


def _split_entry(raw: str) -> tuple[str, str]:
    """Split a stored value into (storage, rel); bare paths default to download."""
    value = _normalize_rel(raw)
    for storage in STORAGE_ROOTS:
        prefix = f"{storage}:"
        if value.startswith(prefix):
            return storage, value[len(prefix) :]
    return STORAGE_DOWNLOAD, value


def _make_entry(storage: str, rel: str) -> str:
    return f"{storage}:{_normalize_rel(rel)}"


def _load_index_file(path: Path) -> dict[str, str]:
    """Load raw index rows, migrating bare (legacy) paths to ``download:``."""
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            out: dict[str, str] = {}
            for k, v in raw.items():
                if not isinstance(v, str) or not v:
                    continue
                storage, rel = _split_entry(v)
                out[str(k)] = _make_entry(storage, rel)
            return out
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read track index: %s", e)
    return {}


def _save_index_file(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def rewrite_track_index_prefix(
    base_path: Path,
    old_prefix: str,
    new_prefix: str,
) -> int:
    """Rewrite download-root index paths when a library folder is renamed.

    Only touches ``download:`` entries; External/Organized entries are not
    affected by Download-side folder renames.

    Returns the number of entries updated.
    """
    old_p = _normalize_rel(old_prefix).rstrip("/")
    new_p = _normalize_rel(new_prefix).rstrip("/")
    if not old_p or old_p == new_p:
        return 0

    path = track_index_path(base_path)
    data = _load_index_file(path)
    if not data:
        return 0

    updated = 0
    for video_id, entry in list(data.items()):
        storage, rel = _split_entry(entry)
        if storage != STORAGE_DOWNLOAD:
            continue
        if rel == old_p:
            data[video_id] = _make_entry(storage, new_p)
            updated += 1
        elif rel.startswith(f"{old_p}/"):
            data[video_id] = _make_entry(storage, new_p + rel[len(old_p) :])
            updated += 1

    if updated:
        _save_index_file(path, data)
        logger.info(
            "Rewrote %d track index entries: %s -> %s",
            updated,
            old_p,
            new_p,
        )
    return updated


def repair_track_index(
    base_path: Path,
    *,
    save_folders: list[str] | None = None,
) -> int:
    """Fix index entries whose stored path no longer exists.

    Download-root entries are matched by path suffix (everything after the
    first folder segment), preferring subscription save folders over Direct.
    External-root entries are checked for existence under External/Organized
    (and dropped if missing — External/Raw files are not index-managed).
    """
    path = track_index_path(base_path)
    data = _load_index_file(path)
    if not data:
        return 0

    preferred = [_normalize_rel(f).rstrip("/") for f in save_folders or [] if f]
    preferred = [f for f in preferred if f and f != DIRECT_FOLDER]
    search_roots = [*preferred, DIRECT_FOLDER]
    try:
        for child in sorted(base_path.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if child.name == ".yubal":
                continue
            if child.name not in search_roots:
                search_roots.append(child.name)
    except OSError:
        pass

    repaired = 0
    for video_id, entry in list(data.items()):
        storage, rel = _split_entry(entry)

        if storage == STORAGE_EXTERNAL:
            absolute = STORAGE_ROOTS[STORAGE_EXTERNAL] / rel
            if absolute.is_file():
                continue
            data.pop(video_id, None)
            repaired += 1
            logger.info(
                "Removed stale external track index entry %s: %s", video_id, rel
            )
            continue

        absolute = base_path / rel
        if absolute.is_file():
            continue

        suffix = rel.split("/", 1)[1] if "/" in rel else ""
        if not suffix:
            data.pop(video_id, None)
            repaired += 1
            continue

        found: str | None = None
        for root in search_roots:
            candidate = f"{root}/{suffix}"
            if (base_path / candidate).is_file():
                found = candidate
                break

        if found:
            data[video_id] = _make_entry(STORAGE_DOWNLOAD, found)
            repaired += 1
            logger.info("Repaired track index %s: %s -> %s", video_id, rel, found)
        else:
            data.pop(video_id, None)
            repaired += 1
            logger.info("Removed stale track index entry %s: %s", video_id, rel)

    if repaired:
        _save_index_file(path, data)
    return repaired


class TrackFileIndex:
    """Maps YouTube video IDs to absolute paths under Download or External.

    Used so the same track appearing in multiple playlist folders (or across
    the Download/External roots) can be hardlinked instead of re-downloaded.
    ``base_path`` is the Download root; kept as the constructor argument name
    for call-site compatibility even though lookups may resolve under
    External too.
    """

    def __init__(self, base_path: Path) -> None:
        self._base_path = base_path
        self._path = track_index_path(base_path)
        self._lock = threading.Lock()
        self._cache: dict[str, str] | None = None

    def get(self, video_id: str) -> Path | None:
        """Return absolute path for a video_id if the file still exists."""
        if not video_id:
            return None
        with self._lock:
            data = self._load()
            entry = data.get(video_id)
            if not entry:
                return None
            storage, rel = _split_entry(entry)
            root = STORAGE_ROOTS.get(storage)
            if root is None:
                data.pop(video_id, None)
                self._save(data)
                return None
            absolute = root / rel
            if absolute.is_file():
                return absolute
            # Stale entry
            data.pop(video_id, None)
            self._save(data)
            return None

    def set(self, video_id: str, file_path: Path) -> None:
        """Record a downloaded/placed file path for video_id (either root)."""
        if not video_id:
            return
        detected = detect_storage_for_path(file_path)
        if detected is None:
            logger.warning(
                "Track path outside known library roots, not indexing: %s",
                file_path,
            )
            return
        storage, rel = detected
        with self._lock:
            data = self._load()
            data[video_id] = _make_entry(storage, rel)
            self._save(data)

    def _load(self) -> dict[str, str]:
        if self._cache is not None:
            return self._cache
        self._cache = _load_index_file(self._path)
        return self._cache

    def _save(self, data: dict[str, str]) -> None:
        self._cache = data
        try:
            _save_index_file(self._path, data)
        except OSError as e:
            logger.warning("Failed to write track index: %s", e)
