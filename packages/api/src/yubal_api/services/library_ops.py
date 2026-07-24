"""Filesystem helpers for membership-aware delete / move / reconcile."""

from __future__ import annotations

import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from yubal.utils.library import (
    AUDIO_SUFFIXES,
    classify_audio_file,
    iter_audio_files,
    resolve_under_data,
)

logger = logging.getLogger(__name__)


def parse_m3u_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.m3u")) + sorted(folder.glob("*.m3u8"))


def m3u_member_paths(m3u_path: Path) -> list[Path]:
    try:
        text = m3u_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    base = m3u_path.parent
    out: list[Path] = []
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


def all_m3u_members_under(folder: Path) -> set[Path]:
    members: set[Path] = set()
    for m3u in parse_m3u_files(folder):
        for p in m3u_member_paths(m3u):
            try:
                members.add(p.resolve())
            except OSError:
                pass
    return members


def collect_folder_audio_stats(folder: Path) -> tuple[int, int, int]:
    """Return (synced, real, hardlink) for audio files under folder."""
    real = hard = 0
    for path in iter_audio_files(folder):
        if classify_audio_file(path) == "hardlink":
            hard += 1
        else:
            real += 1
    synced = real + hard
    return synced, real, hard


_TRACK_NUM_RE = re.compile(r"^\d+\s*[-.]\s*(.+)$")


@dataclass(frozen=True)
class FolderTrack:
    """One track row for the Sync Center expand list."""

    index: int
    title: str
    artist: str | None
    exists: bool
    storage: str  # real | hardlink | missing
    relative_path: str


def _split_extinf_display(display: str) -> tuple[str | None, str]:
    """Parse ``Artist - Title`` from an #EXTINF display string."""
    text = display.strip()
    if " - " in text:
        artist, title = text.split(" - ", 1)
        artist = artist.strip() or None
        title = title.strip() or text
        return artist, title
    return None, text or "Unknown"


def _title_from_filename(path: Path) -> str:
    stem = path.stem
    match = _TRACK_NUM_RE.match(stem)
    return (match.group(1) if match else stem).strip() or path.name


def _artist_from_relative(rel: Path) -> str | None:
    """Best-effort artist from ``Artist/Album/track`` relative path."""
    parts = rel.parts
    if len(parts) >= 2:
        return parts[0]
    return None


def _storage_for(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, "missing"
    return True, classify_audio_file(path)


def list_folder_tracks(folder: Path) -> list[FolderTrack]:
    """List tracks under a save folder by scanning audio files on disk.

    Order is relative path. Artist is taken from the first path segment
    (album-artist folder). Prefer the track catalog for Sync Center display.
    """
    if not folder.is_dir():
        return []

    tracks: list[FolderTrack] = []
    root = folder.resolve()
    for path in sorted(iter_audio_files(folder), key=lambda p: str(p).lower()):
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = Path(path.name)
        exists, storage = _storage_for(path)
        tracks.append(
            FolderTrack(
                index=len(tracks) + 1,
                title=_title_from_filename(path),
                artist=_artist_from_relative(rel),
                exists=exists,
                storage=storage,
                relative_path=str(rel),
            )
        )
    return tracks


_ALBUM_SIDECAR_NAMES = (
    "cover.jpg",
    "cover.png",
    "cover.jpeg",
    "cover.webp",
    "Cover.jpg",
    "folder.jpg",
    "Folder.jpg",
)
_ARTIST_SIDECAR_NAMES = (
    "artist.jpg",
    "artist.png",
    "artist.jpeg",
    "artist.webp",
    "Artist.jpg",
)


def _dir_has_audio(directory: Path) -> bool:
    """True when ``directory`` still contains any audio file (recursive)."""
    if not directory.is_dir():
        return False
    try:
        for path in directory.rglob("*"):
            if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES:
                return True
    except OSError:
        return True
    return False


def _unlink_named_sidecars(directory: Path, names: tuple[str, ...]) -> None:
    if not directory.is_dir():
        return
    for name in names:
        path = directory / name
        if path.is_file():
            try:
                path.unlink()
            except OSError as e:
                logger.warning("Could not remove sidecar %s: %s", path, e)


def _remove_empty_parents(path: Path, stop_at: Path) -> None:
    stop = stop_at.resolve()
    current = path.resolve() if path.is_dir() else path.parent.resolve()
    while current != stop and stop in current.parents:
        try:
            if current.is_dir() and not any(current.iterdir()):
                current.rmdir()
                current = current.parent
                continue
        except OSError:
            pass
        break


def cleanup_after_audio_removed(audio_parent: Path, stop_at: Path) -> None:
    """GC folder covers then prune empty parents after an audio file is gone.

    Typical layout: ``Artist / Album / track.flac`` with ``cover.jpg`` beside
    the track and ``artist.jpg`` under the artist folder. When the last audio
    in an album (or artist tree) is removed, drop those sidecars so empty-dir
    pruning is not blocked by orphan images.
    """
    album = audio_parent
    try:
        album_resolved = album.resolve()
        stop = stop_at.resolve()
    except OSError:
        return

    if album_resolved.is_dir() and not _dir_has_audio(album_resolved):
        _unlink_named_sidecars(album_resolved, _ALBUM_SIDECAR_NAMES)

    artist = album_resolved.parent
    if (
        artist != stop
        and stop in artist.parents
        and artist.is_dir()
        and not _dir_has_audio(artist)
    ):
        _unlink_named_sidecars(artist, _ARTIST_SIDECAR_NAMES)

    _remove_empty_parents(album_resolved, stop_at)


def move_membership_to_direct(
    *,
    data_path: Path,
    source_folder: str,
    direct_folder: str,
    only_m3u: Path | None = None,
) -> int:
    """Move audio listed in m3u(s) under source_folder into direct_folder.

    Returns number of files moved/linked.
    """
    src_root = resolve_under_data(data_path, source_folder)
    dest_root = resolve_under_data(data_path, direct_folder)
    dest_root.mkdir(parents=True, exist_ok=True)

    m3us = [only_m3u] if only_m3u else parse_m3u_files(src_root)
    moved = 0
    for m3u in m3us:
        if m3u is None or not m3u.is_file():
            continue
        for src in m3u_member_paths(m3u):
            if not src.is_file():
                continue
            try:
                rel = src.resolve().relative_to(src_root.resolve())
            except ValueError:
                rel = Path(src.name)
            dest = dest_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                if src.resolve() != dest.resolve():
                    src.unlink(missing_ok=True)
                continue
            try:
                os.rename(src, dest)
                moved += 1
            except OSError:
                try:
                    os.link(src, dest)
                    src.unlink(missing_ok=True)
                    moved += 1
                except OSError as e:
                    logger.warning("Could not move %s → %s: %s", src, dest, e)
        # Move playlist artifacts into Direct as well
        for art in (m3u, m3u.with_suffix(".jpg"), m3u.with_suffix(".png")):
            if art.is_file():
                target = dest_root / art.name
                if not target.exists():
                    try:
                        shutil.move(str(art), str(target))
                    except OSError:
                        pass
                else:
                    art.unlink(missing_ok=True)

    _remove_empty_parents(src_root, data_path)
    return moved


def delete_membership_files(
    *,
    data_path: Path,
    source_folder: str,
    protected_paths: set[Path] | None = None,
) -> int:
    """Delete audio files under source_folder that are not in protected_paths.

    Protected = files still referenced by other subscriptions' m3us.
    Also deletes m3u/covers in the folder. Returns deleted file count.
    """
    src_root = resolve_under_data(data_path, source_folder)
    protected = {p.resolve() for p in (protected_paths or set())}
    members = all_m3u_members_under(src_root)
    # If no m3u, fall back to all audio in folder
    targets = members if members else {p.resolve() for p in iter_audio_files(src_root)}

    deleted = 0
    for path in targets:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in protected:
            continue
        if not resolved.is_file():
            continue
        # Keep if hardlinked elsewhere and still needed — protected covers that
        try:
            resolved.unlink()
            deleted += 1
        except OSError as e:
            logger.warning("Could not delete %s: %s", resolved, e)

    for art in parse_m3u_files(src_root):
        for path in (
            art,
            art.with_suffix(".jpg"),
            art.with_suffix(".png"),
            art.with_suffix(".jpeg"),
            art.with_suffix(".webp"),
        ):
            if path.is_file():
                path.unlink(missing_ok=True)

    # Remove leftover audio only if exclusive (not protected)
    for path in iter_audio_files(src_root):
        try:
            if path.resolve() in protected:
                continue
            path.unlink(missing_ok=True)
            deleted += 1
        except OSError:
            pass

    _remove_empty_parents(src_root, data_path)
    return deleted


def delete_tree_audio(folder: Path) -> int:
    """Delete all audio (+ m3u/covers) under folder. Returns deleted count."""
    if not folder.is_dir():
        return 0
    deleted = 0
    for path in list(folder.rglob("*")):
        if not path.is_file():
            continue
        suf = path.suffix.lower()
        if suf in AUDIO_SUFFIXES or suf in {
            ".m3u",
            ".m3u8",
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".lrc",
        }:
            try:
                path.unlink()
                deleted += 1
            except OSError:
                pass
    # prune empty dirs bottom-up
    for dirpath, _, _ in os.walk(folder, topdown=False):
        p = Path(dirpath)
        try:
            if p.is_dir() and not any(p.iterdir()):
                p.rmdir()
        except OSError:
            pass
    return deleted


@dataclass
class ReconcileCounts:
    total_count: int
    synced_count: int
    real_download_count: int
    hardlink_count: int


def reconcile_folder_counts(folder: Path) -> ReconcileCounts:
    synced, real, hard = collect_folder_audio_stats(folder)
    return ReconcileCounts(
        total_count=max(synced, 0),
        synced_count=synced,
        real_download_count=real,
        hardlink_count=hard,
    )


def rewrite_path_in_m3u(m3u_path: Path, old_target: Path, new_target: Path) -> None:
    """Rewrite m3u entries that point at ``old_target`` to ``new_target``."""
    try:
        text = m3u_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    base = m3u_path.parent
    old_resolved = old_target.resolve()
    new_rel = new_target.resolve().relative_to(base.resolve())
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    changed = False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("#EXTINF:"):
            if i + 1 < len(lines):
                path_line = lines[i + 1].strip()
                if path_line and not path_line.startswith("#"):
                    p = Path(path_line)
                    if not p.is_absolute():
                        p = (base / p).resolve()
                    else:
                        p = p.resolve()
                    if p == old_resolved:
                        out.append(line)
                        out.append(str(new_rel).replace("\\", "/"))
                        i += 2
                        changed = True
                        continue
            out.append(line)
            i += 1
            continue
        if stripped and not stripped.startswith("#"):
            p = Path(stripped)
            if not p.is_absolute():
                p = (base / p).resolve()
            else:
                p = p.resolve()
            if p == old_resolved:
                out.append(str(new_rel).replace("\\", "/"))
                i += 1
                changed = True
                continue
        out.append(line)
        i += 1
    if changed:
        try:
            m3u_path.write_text(
                "\n".join(out) + ("\n" if out else ""),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Could not rewrite m3u %s: %s", m3u_path, e)


def _remove_path_from_m3u(m3u_path: Path, target: Path) -> None:
    """Drop EXTINF+path pairs that point at ``target`` from an m3u file."""
    try:
        text = m3u_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    base = m3u_path.parent
    target_resolved = target.resolve()
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    changed = False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("#EXTINF:"):
            if i + 1 < len(lines):
                path_line = lines[i + 1].strip()
                if path_line and not path_line.startswith("#"):
                    p = Path(path_line)
                    if not p.is_absolute():
                        p = (base / p).resolve()
                    else:
                        p = p.resolve()
                    if p == target_resolved:
                        i += 2
                        changed = True
                        continue
            out.append(line)
            i += 1
            continue
        if stripped and not stripped.startswith("#"):
            p = Path(stripped)
            if not p.is_absolute():
                p = (base / p).resolve()
            else:
                p = p.resolve()
            if p == target_resolved:
                i += 1
                changed = True
                continue
        out.append(line)
        i += 1
    if changed:
        try:
            m3u_path.write_text(
                "\n".join(out) + ("\n" if out else ""),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Could not rewrite m3u %s: %s", m3u_path, e)


def delete_track_file(
    *,
    data_path: Path,
    save_folder: str,
    relative_path: str,
) -> bool:
    """Delete one audio file (+ sidecar .lrc) under save_folder.

    Returns True if deleted.

    Also removes matching entries from any m3u in the save folder and prunes
    empty parent directories.
    """
    folder = (save_folder or "").strip().replace("\\", "/")
    rel = (relative_path or "").strip().replace("\\", "/")
    if not folder or not rel or ".." in rel.split("/") or rel.startswith("/"):
        raise ValueError("invalid path")
    root = resolve_under_data(data_path, folder)
    target = resolve_under_data(data_path, f"{folder}/{rel}")
    if root not in target.parents and target != root:
        raise ValueError("path escapes save folder")
    if target.suffix.lower() not in AUDIO_SUFFIXES:
        raise ValueError("not an audio file")
    deleted = False
    if target.is_file():
        try:
            target.unlink()
            deleted = True
        except OSError as e:
            logger.warning("Could not delete %s: %s", target, e)
            raise
    lrc = target.with_suffix(".lrc")
    if lrc.is_file():
        lrc.unlink(missing_ok=True)
    for m3u in parse_m3u_files(root):
        _remove_path_from_m3u(m3u, target)
    cleanup_after_audio_removed(target.parent, root)
    return deleted
