"""Library folder layout helpers.

Layout under the download root (per-folder mode)::

    direct/                 # one-shot downloads
    sublist/
      default/              # new subscriptions share this (editable per-sub)
        Artist/Year - Album/tracks
        {name}.m3u + cover
      {playlist}/           # older / manually set save folders
    unofficial/             # UGC (when enabled)
    unmatched/              # unmatched official tracks
"""

from __future__ import annotations

import os
from pathlib import Path

from yubal.utils.filename import _limit_path_component, clean_filename

PLAYLISTS_FOLDER = "_playlists"
DIRECT_FOLDER = "direct"
SUBLIST_FOLDER = "sublist"
SUBLIST_DEFAULT_FOLDER = f"{SUBLIST_FOLDER}/default"
UNMATCHED_FOLDER = "unmatched"
UNOFFICIAL_FOLDER = "unofficial"
TRACK_INDEX_DIR = ".yubal"
ERROR_FOLDER = ".error"
MOUNT_SENTINEL_NAME = ".yubal-mount"


def _library_roots() -> tuple[Path, Path, Path]:
    """Resolve download/external/wanted roots under a single library mount.

    Default layout: ``/data/download`` + ``/data/external`` + ``/data/wanted``
    (one ``./data:/data`` bind so hardlinks work). Optional
    ``YUBAL_LIBRARY_ROOT`` overrides the base for tests / non-Docker runs.
    """
    lib = (os.environ.get("YUBAL_LIBRARY_ROOT") or "/data").strip() or "/data"
    base = Path(lib)
    return base / "download", base / "external", base / "wanted"


# Triple-root library under one mount (default: /data).
DOWNLOAD_ROOT, EXTERNAL_ROOT, WANTED_ROOT = _library_roots()
# Backward-compatible alias
PRESELECT_EXTERNAL_ROOT = EXTERNAL_ROOT

EXTERNAL_RAW_DIR = "raw"
EXTERNAL_ORGANIZED_DIR = "organized"
EXTERNAL_RAW_ROOT = EXTERNAL_ROOT / EXTERNAL_RAW_DIR
EXTERNAL_ORGANIZED_ROOT = EXTERNAL_ROOT / EXTERNAL_ORGANIZED_DIR
# Special external playlist dirs (under raw/ and organized/).
# delete: offline / invalid-ID salvage (raw unmatched). default: valid-ID archive.
EXTERNAL_DELETE_DIR = "delete"
EXTERNAL_DEFAULT_DIR = "default"

STORAGE_DOWNLOAD = "download"
STORAGE_EXTERNAL = "external"
STORAGE_WANTED = "wanted"
STORAGE_ROOTS: dict[str, Path] = {
    STORAGE_DOWNLOAD: DOWNLOAD_ROOT,
    STORAGE_EXTERNAL: EXTERNAL_ROOT,
    STORAGE_WANTED: WANTED_ROOT,
}

DOWNLOAD_MOUNT_SENTINEL = DOWNLOAD_ROOT / MOUNT_SENTINEL_NAME
EXTERNAL_MOUNT_SENTINEL = EXTERNAL_ROOT / MOUNT_SENTINEL_NAME
WANTED_MOUNT_SENTINEL = WANTED_ROOT / MOUNT_SENTINEL_NAME


def ensure_wanted_layout() -> Path:
    """Create ``/data/wanted`` (and mount sentinel) if missing."""
    WANTED_ROOT.mkdir(parents=True, exist_ok=True)
    if not WANTED_MOUNT_SENTINEL.exists():
        try:
            WANTED_MOUNT_SENTINEL.write_text("wanted\n", encoding="utf-8")
        except OSError:
            pass
    return WANTED_ROOT


def inode_key(path: Path) -> tuple[int, int] | None:
    """Return ``(st_dev, st_ino)`` when ``path`` is an existing file."""
    try:
        if not path.is_file():
            return None
        st = path.stat()
        return (st.st_dev, st.st_ino)
    except OSError:
        return None


def same_filesystem(a: Path, b: Path) -> bool:
    """True when a hardlink can be created between the two directory trees.

    Probes with a real ``os.link``. Matching ``st_dev`` is not enough under
    Docker when Download and External are separate bind mounts of the same
    host filesystem.

    Probe filenames include pid/tid so concurrent health checks cannot race.
    """
    import threading

    probe_src: Path | None = None
    probe_dst: Path | None = None
    try:
        a.mkdir(parents=True, exist_ok=True)
        b.mkdir(parents=True, exist_ok=True)
        token = f"{os.getpid()}-{threading.get_ident()}"
        probe_src = a.resolve() / f".yubal-hardlink-probe-{token}"
        probe_dst = b.resolve() / f".yubal-hardlink-probe-link-{token}"
        probe_src.write_bytes(b"")
        os.link(probe_src, probe_dst)
        return True
    except OSError:
        return False
    finally:
        for p in (probe_dst, probe_src):
            if p is None:
                continue
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

def resolve_storage_path(storage: str, relative: str) -> Path:
    """Resolve ``storage`` + relative path under a known library root."""
    root = STORAGE_ROOTS.get(storage)
    if root is None:
        raise ValueError(f"Unknown storage root: {storage!r}")
    rel = (relative or "").strip().replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise ValueError(f"Invalid relative path: {relative!r}")
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if root_resolved not in target.parents and target != root_resolved:
        raise ValueError(f"Path escapes storage root: {relative}")
    return target


def detect_storage_for_path(file_path: Path) -> tuple[str, str] | None:
    """Return (storage, rel) when path is under Download or External."""
    resolved = file_path.resolve()
    for storage, root in STORAGE_ROOTS.items():
        try:
            root_r = root.resolve()
            rel = str(resolved.relative_to(root_r)).replace("\\", "/")
            return storage, rel
        except (ValueError, OSError):
            continue
    return None


def organized_save_folder(dir_name: str) -> str:
    """Relative save folder under External for an organized DIR playlist."""
    safe = clean_filename(dir_name) or "Untitled"
    return f"{EXTERNAL_ORGANIZED_DIR}/{safe}"


def ensure_external_layout() -> None:
    """Create Raw/Organized dirs under /External when the mount is present."""
    if not EXTERNAL_ROOT.is_dir():
        return
    EXTERNAL_RAW_ROOT.mkdir(parents=True, exist_ok=True)
    EXTERNAL_ORGANIZED_ROOT.mkdir(parents=True, exist_ok=True)
    (EXTERNAL_RAW_ROOT / EXTERNAL_DELETE_DIR).mkdir(parents=True, exist_ok=True)
    (EXTERNAL_RAW_ROOT / EXTERNAL_DEFAULT_DIR).mkdir(parents=True, exist_ok=True)
    (EXTERNAL_ORGANIZED_ROOT / EXTERNAL_DEFAULT_DIR).mkdir(
        parents=True, exist_ok=True
    )


RESERVED_LIBRARY_FOLDERS = frozenset(
    {
        DIRECT_FOLDER,
        SUBLIST_FOLDER,
        UNMATCHED_FOLDER,
        UNOFFICIAL_FOLDER,
        TRACK_INDEX_DIR,
        PLAYLISTS_FOLDER,
        ERROR_FOLDER,
        "_unmatched",
        "_unofficial",
    }
)

# Tops that must never be a save_folder / direct_folder root segment
FORBIDDEN_ROOT_SEGMENTS = frozenset(
    {
        UNMATCHED_FOLDER,
        UNOFFICIAL_FOLDER,
        TRACK_INDEX_DIR,
        PLAYLISTS_FOLDER,
        ERROR_FOLDER,
        "_unmatched",
        "_unofficial",
    }
)

AUDIO_SUFFIXES = frozenset(
    {".opus", ".mp3", ".m4a", ".flac", ".ogg", ".aac", ".wav", ".webm"}
)

MAX_SAVE_FOLDER_DEPTH = 3


def folder_depth(relative: str) -> int:
    return len([p for p in relative.split("/") if p.strip()])


def assert_folder_depth(relative: str, *, max_depth: int = MAX_SAVE_FOLDER_DEPTH) -> None:
    depth = folder_depth(relative)
    if depth > max_depth:
        raise ValueError(
            f"Folder path exceeds max depth of {max_depth} (got {depth}): {relative!r}"
        )


def sanitize_save_folder(name: str, *, ascii_filenames: bool = False) -> str:
    """Sanitize a relative save path under the data root.

    Allows nested paths like ``test/徽常美Ⅱ``. Rejects ``..``, absolute paths,
    and reserved root segments (unmatched / unofficial / _playlists / …).
    The bare name ``direct`` is rewritten to ``direct Playlist`` so subscriptions
    do not collide with the default direct bucket unless the user picks a
    different shared path deliberately via nested names.
    """
    raw = (name or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or raw.startswith("~"):
        return "Untitled Playlist"

    parts: list[str] = []
    for segment in raw.split("/"):
        segment = segment.strip()
        if not segment or segment in {".", ".."}:
            continue
        safe = clean_filename(segment, ascii_filenames=ascii_filenames)
        if not safe or not safe.strip():
            continue
        safe = _limit_path_component(safe.strip())
        if safe.startswith("."):
            safe = _limit_path_component(f"{safe.lstrip('.')} Folder") or "Folder"
        parts.append(safe)

    if not parts:
        return "Untitled Playlist"

    # First segment cannot be a forbidden system folder
    if parts[0] in FORBIDDEN_ROOT_SEGMENTS:
        parts[0] = _limit_path_component(f"{parts[0]} Playlist")

    # Bare "direct" as a single-segment subscription folder → disambiguate
    if len(parts) == 1 and parts[0] == DIRECT_FOLDER:
        parts[0] = _limit_path_component(f"{DIRECT_FOLDER} Playlist")

    # Bare "sublist" is the subscriptions root, not a playlist folder
    if len(parts) == 1 and parts[0] == SUBLIST_FOLDER:
        parts[0] = _limit_path_component(f"{SUBLIST_FOLDER} Playlist")

    result = "/".join(parts)
    assert_folder_depth(result)
    return result


def default_subscription_save_folder(
    title: str, *, ascii_filenames: bool = False
) -> str:
    """Default save path for new subscriptions: ``sublist/default``.

    ``title`` / ``ascii_filenames`` are kept for call-site compatibility but
    ignored — new subscriptions share one folder so the same track resolves
    to one path (skip-if-exists) without needing a hardlink.
    """
    _ = (title, ascii_filenames)
    return SUBLIST_DEFAULT_FOLDER


def sanitize_direct_folder(name: str, *, ascii_filenames: bool = False) -> str:
    """Sanitize the Direct-download bucket path (may be nested)."""
    raw = (name or "").strip().replace("\\", "/") or DIRECT_FOLDER
    parts: list[str] = []
    for segment in raw.split("/"):
        segment = segment.strip()
        if not segment or segment in {".", ".."}:
            continue
        safe = clean_filename(segment, ascii_filenames=ascii_filenames)
        if not safe or not safe.strip():
            continue
        safe = _limit_path_component(safe.strip())
        if safe.startswith("."):
            safe = _limit_path_component(f"{safe.lstrip('.')} Folder") or "Folder"
        parts.append(safe)
    if not parts:
        return DIRECT_FOLDER
    if parts[0] in FORBIDDEN_ROOT_SEGMENTS:
        parts[0] = DIRECT_FOLDER
    result = "/".join(parts)
    assert_folder_depth(result)
    return result


def resolve_default_library_folder(
    kind: str,
    title: str | None,
    *,
    ascii_filenames: bool = False,
) -> str:
    """Pick library folder when none was provided by a subscription.

    Playlists use ``SubList/Default``; albums and single tracks use Direct.
    """
    if kind == "playlist":
        return default_subscription_save_folder(
            title or "Untitled Playlist", ascii_filenames=ascii_filenames
        )
    return DIRECT_FOLDER


def track_index_path(base_path: Path) -> Path:
    """Path to the video_id → relative-file index used for hardlink dedupe."""
    return base_path / TRACK_INDEX_DIR / "track_index.json"


def resolve_under_data(data_path: Path, relative: str) -> Path:
    """Resolve a relative library path under data_path; raise if it escapes."""
    data = data_path.resolve()
    target = (data / relative).resolve()
    if data not in target.parents and target != data:
        raise ValueError(f"Path escapes data root: {relative}")
    return target


def list_library_folder_options(data_path: Path) -> list[str]:
    """List relative dirs suitable as save_folder / direct_folder targets.

    Includes top-level library folders and empty nesting containers (e.g.
    ``test/徽常美Ⅱ``). Stops descending once a folder subtree contains audio,
    so Artist/Album leaves are not offered as save targets.
    """
    data = data_path.resolve()
    if not data.is_dir():
        return []

    skip_names = FORBIDDEN_ROOT_SEGMENTS | {TRACK_INDEX_DIR}
    found: set[str] = set()

    def subtree_has_audio(folder: Path) -> bool:
        try:
            for path in folder.rglob("*"):
                if path.is_file() and path.suffix.lower() in AUDIO_SUFFIXES:
                    return True
        except OSError:
            return False
        return False

    def walk(rel_parts: tuple[str, ...], abs_path: Path) -> None:
        if rel_parts:
            found.add("/".join(rel_parts))
        if subtree_has_audio(abs_path):
            return
        try:
            children = sorted(abs_path.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for child in children:
            if not child.is_dir():
                continue
            if child.name in skip_names or child.name.startswith("."):
                continue
            walk((*rel_parts, child.name), child)

    try:
        top = sorted(data.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []

    for child in top:
        if not child.is_dir():
            continue
        if child.name in skip_names or child.name.startswith("."):
            continue
        walk((child.name,), child)

    return sorted(found, key=lambda s: s.lower())


def folder_contains_files(folder: Path) -> bool:
    """True if any file exists under folder (dirs alone do not count)."""
    if not folder.is_dir():
        return False
    try:
        for path in folder.rglob("*"):
            if path.is_file():
                return True
    except OSError:
        return True
    return False


def is_empty_library_folder(folder: Path) -> bool:
    """Empty = directory exists and contains no files (empty subdirs OK)."""
    return folder.is_dir() and not folder_contains_files(folder)


def delete_empty_library_folder(folder: Path) -> None:
    """Remove an empty directory tree (no files). Raises ValueError if not empty."""
    if not folder.is_dir():
        raise ValueError("Folder does not exist")
    if folder_contains_files(folder):
        raise ValueError("Folder is not empty")
    # Remove deepest dirs first
    for path in sorted(folder.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_dir():
            path.rmdir()
    folder.rmdir()


def iter_audio_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return [
        p
        for p in folder.rglob("*")
        if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES
    ]


def classify_audio_file(path: Path) -> str:
    """Return 'hardlink' or 'real' based on nlink alone.

    Prefer ``yubal_api.services.library_hardlink.classify_catalog_file`` for
    Sync Center / External stats: nlink>1 also covers Raw↔Organized pairs
    that are not cross-playlist library hardlinks.
    """
    try:
        if path.stat().st_nlink > 1:
            return "hardlink"
    except OSError:
        pass
    return "real"
