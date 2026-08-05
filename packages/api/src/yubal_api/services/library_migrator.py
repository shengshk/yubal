"""Migrate on-disk library between per_folder and unified layouts.

Uses same-filesystem rename/hardlink (no bulk copy). Conflicts keep the
larger file and stash the other under ``data/.error/`` (72h retention).
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from yubal.utils.library import (
    DIRECT_FOLDER,
    PLAYLISTS_FOLDER,
    RESERVED_LIBRARY_FOLDERS,
    TRACK_INDEX_DIR,
    UNMATCHED_FOLDER,
    UNOFFICIAL_FOLDER,
    track_index_path,
)

logger = logging.getLogger(__name__)

ERROR_FOLDER = ".error"
MIGRATE_STAGING = ".yubal-migrate-new"
ERROR_RETENTION = timedelta(hours=72)

AUDIO_SUFFIXES = frozenset(
    {".opus", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".wav", ".webm"}
)
ARTIFACT_SUFFIXES = frozenset({".m3u", ".m3u8", ".jpg", ".jpeg", ".png", ".webp"})


@dataclass
class ConflictRecord:
    kept: str
    stashed: str
    kept_bytes: int
    stashed_bytes: int


@dataclass
class MigrationResult:
    from_layout: str
    to_layout: str
    moved: int = 0
    linked: int = 0
    skipped_same: int = 0
    conflicts: list[ConflictRecord] = field(default_factory=list)
    errors_dir: str = ""
    message: str = ""


def cleanup_error_folder(data_path: Path, *, now: datetime | None = None) -> int:
    """Delete files under data/.error older than 72 hours. Returns removed count."""
    root = data_path / ERROR_FOLDER
    if not root.is_dir():
        return 0
    cutoff = (now or datetime.now(UTC)).timestamp() - ERROR_RETENTION.total_seconds()
    removed = 0
    for path in sorted(root.rglob("*"), reverse=True):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
            elif path.is_dir() and path != root and not any(path.iterdir()):
                path.rmdir()
        except OSError as e:
            logger.warning("Failed cleaning %s: %s", path, e)
    return removed


def _same_file(a: Path, b: Path) -> bool:
    try:
        sa, sb = a.stat(), b.stat()
        return sa.st_ino == sb.st_ino and sa.st_dev == sb.st_dev
    except OSError:
        return False


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _stash_to_error(data_path: Path, src: Path, result: MigrationResult) -> Path:
    """Move ``src`` under data/.error preserving a unique relative name."""
    errors = data_path / ERROR_FOLDER
    errors.mkdir(parents=True, exist_ok=True)
    try:
        rel = src.resolve().relative_to(data_path.resolve())
    except ValueError:
        rel = Path(src.name)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    dest = errors / rel.parent / f"{src.stem}__{stamp}{src.suffix}"
    n = 1
    while dest.exists():
        dest = errors / rel.parent / f"{src.stem}__{stamp}_{n}{src.suffix}"
        n += 1
    _ensure_parent(dest)
    shutil.move(str(src), str(dest))
    return dest


def place_file(
    data_path: Path,
    src: Path,
    dest: Path,
    result: MigrationResult,
) -> None:
    """Place src at dest using rename/hardlink; resolve size conflicts into .error."""
    if not src.exists():
        return
    if src.resolve() == dest.resolve():
        result.skipped_same += 1
        return

    _ensure_parent(dest)

    if not dest.exists():
        try:
            os.rename(src, dest)
            result.moved += 1
            return
        except OSError:
            # Cross-device or busy: fall back to hardlink+unlink when possible
            try:
                os.link(src, dest)
                src.unlink(missing_ok=True)
                result.linked += 1
                return
            except OSError as e:
                logger.error("Failed placing %s → %s: %s", src, dest, e)
                raise

    if _same_file(src, dest):
        if src.resolve() != dest.resolve():
            src.unlink(missing_ok=True)
        result.skipped_same += 1
        return

    # Conflict: keep larger file, stash the other
    src_size = src.stat().st_size
    dest_size = dest.stat().st_size
    if src_size > dest_size:
        stashed = _stash_to_error(data_path, dest, result)
        os.rename(src, dest)
        result.moved += 1
        result.conflicts.append(
            ConflictRecord(
                kept=str(dest.relative_to(data_path)),
                stashed=str(stashed.relative_to(data_path)),
                kept_bytes=src_size,
                stashed_bytes=dest_size,
            )
        )
    elif dest_size > src_size:
        stashed = _stash_to_error(data_path, src, result)
        result.conflicts.append(
            ConflictRecord(
                kept=str(dest.relative_to(data_path)),
                stashed=str(stashed.relative_to(data_path)),
                kept_bytes=dest_size,
                stashed_bytes=src_size,
            )
        )
    else:
        # Equal size: keep existing dest, stash src
        stashed = _stash_to_error(data_path, src, result)
        result.conflicts.append(
            ConflictRecord(
                kept=str(dest.relative_to(data_path)),
                stashed=str(stashed.relative_to(data_path)),
                kept_bytes=dest_size,
                stashed_bytes=src_size,
            )
        )


def _is_maintenance_dir(name: str) -> bool:
    return name.startswith(".") and name not in {ERROR_FOLDER}


def _iter_audio(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    out: list[Path] = []
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES:
            out.append(path)
    return out


def _relative_under(folder: Path, file_path: Path) -> Path | None:
    try:
        return file_path.resolve().relative_to(folder.resolve())
    except ValueError:
        return None


def _move_artifacts_to_playlists(
    data_path: Path, source_dir: Path, result: MigrationResult
) -> None:
    playlists = data_path / PLAYLISTS_FOLDER
    playlists.mkdir(parents=True, exist_ok=True)
    if not source_dir.is_dir():
        return
    for path in list(source_dir.iterdir()):
        if path.is_file() and path.suffix.lower() in ARTIFACT_SUFFIXES:
            dest = playlists / path.name
            place_file(data_path, path, dest, result)


def _remove_empty_dirs(root: Path) -> None:
    if not root.is_dir():
        return
    # bottom-up
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        path = Path(dirpath)
        if path == root:
            continue
        try:
            if not any(path.iterdir()):
                path.rmdir()
        except OSError:
            pass


def migrate_per_folder_to_unified(data_path: Path) -> MigrationResult:
    """Collapse playlist/Direct trees into flat Artist/Album + _Playlists."""
    result = MigrationResult(
        from_layout="per_folder",
        to_layout="unified",
        errors_dir=str(data_path / ERROR_FOLDER),
    )
    data_path = data_path.resolve()

    top_dirs = (
        [p for p in data_path.iterdir() if p.is_dir()] if data_path.is_dir() else []
    )

    for folder in top_dirs:
        name = folder.name
        if name in {UNMATCHED_FOLDER, UNOFFICIAL_FOLDER, TRACK_INDEX_DIR, ERROR_FOLDER}:
            continue
        if name in {MIGRATE_STAGING, PLAYLISTS_FOLDER}:
            continue
        if _is_maintenance_dir(name):
            continue

        # Audio: {folder}/Artist/Album/track → Artist/Album/track
        for audio in _iter_audio(folder):
            rel = _relative_under(folder, audio)
            if rel is None or len(rel.parts) < 2:
                # Odd layout — park under Direct-equivalent path name
                dest = data_path / DIRECT_FOLDER / audio.name
            else:
                dest = data_path / rel
            place_file(data_path, audio, dest, result)

        _move_artifacts_to_playlists(data_path, folder, result)

        if name != DIRECT_FOLDER:
            _remove_empty_dirs(folder)
            try:
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
            except OSError:
                pass

    # Direct folder may remain empty after moving Artist trees to root
    direct = data_path / DIRECT_FOLDER
    _remove_empty_dirs(direct)
    try:
        if direct.is_dir() and not any(direct.iterdir()):
            direct.rmdir()
    except OSError:
        pass

    _rebuild_track_index(data_path)
    result.message = _summary_message(result)
    return result


def migrate_unified_to_per_folder(
    data_path: Path,
    *,
    playlist_folders: list[tuple[str, Path | None]],
) -> MigrationResult:
    """Expand flat library into per-subscription folders using m3u membership.

    ``playlist_folders``: list of (save_folder_name, optional_m3u_path).
    Tracks listed in an m3u are hardlinked/moved into that save folder.
    Remaining Artist-tree audio goes to Direct/.
    """
    result = MigrationResult(
        from_layout="unified",
        to_layout="per_folder",
        errors_dir=str(data_path / ERROR_FOLDER),
    )
    data_path = data_path.resolve()
    playlists_dir = data_path / PLAYLISTS_FOLDER

    claimed_sources: dict[str, Path] = {}  # original resolved src → current path

    for save_folder, m3u_path in playlist_folders:
        target_root = data_path / save_folder
        target_root.mkdir(parents=True, exist_ok=True)

        entries = _parse_m3u_paths(m3u_path, data_path) if m3u_path else []
        for src in entries:
            try:
                src_key = str(src.resolve())
            except OSError:
                continue

            current = claimed_sources.get(src_key, src)
            if not current.is_file():
                continue

            try:
                rel = current.resolve().relative_to(data_path)
            except ValueError:
                # Already under a save folder — use path relative to that folder's parent artist tree
                try:
                    # Prefer artist/album/file from filename structure under save folder
                    rel = (
                        Path(*current.parts[-3:])
                        if len(current.parts) >= 3
                        else Path(current.name)
                    )
                except Exception:
                    rel = Path(current.name)

            top = rel.parts[0] if rel.parts else ""
            if top in {
                UNMATCHED_FOLDER,
                UNOFFICIAL_FOLDER,
                PLAYLISTS_FOLDER,
                ERROR_FOLDER,
                TRACK_INDEX_DIR,
                DIRECT_FOLDER,
            }:
                # strip reserved prefix if present
                rel = Path(*rel.parts[1:]) if len(rel.parts) > 1 else Path(current.name)

            # If file already lives under some save folder, rel should be under Artist/...
            for sf, _ in playlist_folders:
                prefix = data_path / sf
                try:
                    rel = current.resolve().relative_to(prefix.resolve())
                    break
                except ValueError:
                    pass
            else:
                try:
                    rel = current.resolve().relative_to(data_path)
                    if rel.parts and rel.parts[0] == PLAYLISTS_FOLDER:
                        continue
                except ValueError:
                    rel = Path(current.name)

            dest = target_root / rel

            if src_key not in claimed_sources:
                place_file(data_path, current, dest, result)
                if dest.is_file():
                    claimed_sources[src_key] = dest
            else:
                current = claimed_sources[src_key]
                if not dest.exists():
                    _ensure_parent(dest)
                    try:
                        os.link(current, dest)
                        result.linked += 1
                    except OSError:
                        place_file(data_path, current, dest, result)
                        if dest.is_file():
                            claimed_sources[src_key] = dest
                else:
                    result.skipped_same += 1

        if playlists_dir.is_dir() and m3u_path and m3u_path.exists():
            stem = m3u_path.stem
            for art in list(playlists_dir.iterdir()):
                if art.is_file() and art.stem == stem:
                    place_file(data_path, art, target_root / art.name, result)

    # Leftover matched audio at data root Artist trees → Direct/
    for audio in _iter_audio(data_path):
        try:
            rel = audio.resolve().relative_to(data_path)
        except ValueError:
            continue
        top = rel.parts[0] if rel.parts else ""
        if top in RESERVED_LIBRARY_FOLDERS or top.startswith("."):
            continue
        # Already under a save folder (has more structure from migration)
        # Root-level Artist/Album/track has top = Artist name (not reserved)
        # If file still under data/Artist/... (not under save_folder), move to Direct
        if any(
            (data_path / sf).resolve() in audio.resolve().parents
            for sf, _ in playlist_folders
        ):
            continue
        dest = data_path / DIRECT_FOLDER / rel
        place_file(data_path, audio, dest, result)

    # Leftover _Playlists artifacts → drop into Direct or keep? Move unknown to Direct
    if playlists_dir.is_dir():
        for art in list(playlists_dir.iterdir()):
            if art.is_file():
                place_file(data_path, art, data_path / DIRECT_FOLDER / art.name, result)
        _remove_empty_dirs(playlists_dir)
        try:
            if playlists_dir.is_dir() and not any(playlists_dir.iterdir()):
                playlists_dir.rmdir()
        except OSError:
            pass

    _rebuild_track_index(data_path)
    result.message = _summary_message(result)
    return result


def _rebuild_track_index(data_path: Path) -> None:
    """Rebuild video_id index from filenames containing [videoId]."""
    import json
    import re

    video_re = re.compile(r"\[([A-Za-z0-9_-]{11})\]")
    mapping: dict[str, str] = {}
    for audio in _iter_audio(data_path):
        try:
            rel = str(audio.resolve().relative_to(data_path.resolve()))
        except ValueError:
            continue
        if rel.startswith(f"{ERROR_FOLDER}/") or ERROR_FOLDER in Path(rel).parts:
            continue
        match = video_re.search(audio.name)
        if match:
            mapping[match.group(1)] = rel

    path = track_index_path(data_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _summary_message(result: MigrationResult) -> str:
    parts = [
        f"moved={result.moved}",
        f"linked={result.linked}",
        f"same={result.skipped_same}",
        f"conflicts={len(result.conflicts)}",
    ]
    return ", ".join(parts)


def _parse_m3u_paths(m3u_path: Path | None, data_path: Path) -> list[Path]:
    if m3u_path is None or not m3u_path.is_file():
        return []
    out: list[Path] = []
    try:
        text = m3u_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    base = m3u_path.parent
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = Path(line)
        if not p.is_absolute():
            p = (base / p).resolve()
        else:
            p = p.resolve()
        out.append(p)
    return out


def find_m3u_for_folder(data_path: Path, save_folder: str) -> Path | None:
    """Best-effort locate an m3u for a subscription folder name."""
    candidates = [
        data_path / save_folder,
        data_path / PLAYLISTS_FOLDER,
    ]
    for folder in candidates:
        if not folder.is_dir():
            continue
        m3us = sorted(folder.glob("*.m3u")) + sorted(folder.glob("*.m3u8"))
        if len(m3us) == 1:
            return m3us[0]
        for m in m3us:
            if save_folder.lower() in m.stem.lower():
                return m
        if m3us:
            return m3us[0]
    return None


def run_layout_migration(
    data_path: Path,
    *,
    from_layout: str,
    to_layout: str,
    playlist_folders: list[tuple[str, Path | None]] | None = None,
) -> MigrationResult:
    """Entry point used by the settings API."""
    cleanup_error_folder(data_path)
    if from_layout == to_layout:
        return MigrationResult(
            from_layout=from_layout,
            to_layout=to_layout,
            message="noop",
        )
    if from_layout == "per_folder" and to_layout == "unified":
        return migrate_per_folder_to_unified(data_path)
    if from_layout == "unified" and to_layout == "per_folder":
        return migrate_unified_to_per_folder(
            data_path,
            playlist_folders=playlist_folders or [],
        )
    raise ValueError(f"Unsupported migration {from_layout!r} → {to_layout!r}")
