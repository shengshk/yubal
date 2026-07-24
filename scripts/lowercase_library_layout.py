#!/usr/bin/env python3
"""One-shot: lowercase library layout dirs + rewrite DB / track_index paths.

Run on the host against stacks/yubal/data (stop yubal writers first if possible).
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"

# Exact directory names to rename (leaf segment). Order: deepest / specific first.
DIR_RENAMES: list[tuple[str, str]] = [
    ("Download", "download"),
    ("External", "external"),
    ("Cache", "cache"),
    ("Direct", "direct"),
    ("SubList", "sublist"),
    ("Liked", "liked"),
    ("Unofficial", "unofficial"),
    ("Unmatched", "unmatched"),
    ("Organized", "organized"),
    ("Raw", "raw"),
    ("Default", "default"),
    ("Delete", "delete"),
    ("_Playlists", "_playlists"),
    ("_Unmatched", "_unmatched"),
    ("_Unofficial", "_unofficial"),
]


def rename_tree(root: Path) -> list[tuple[Path, Path]]:
    """Rename matching directory segments bottom-up. Returns (old, new) pairs."""
    done: list[tuple[Path, Path]] = []
    # Collect all dirs deepest-first
    dirs = sorted(
        (p for p in root.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    )
    mapping = dict(DIR_RENAMES)
    for path in dirs:
        name = path.name
        if name not in mapping:
            continue
        target = path.with_name(mapping[name])
        if target.exists():
            # Merge into existing lowercase dir if both somehow exist
            for child in list(path.iterdir()):
                dest = target / child.name
                if dest.exists():
                    continue
                shutil.move(str(child), str(dest))
            try:
                path.rmdir()
            except OSError:
                pass
            done.append((path, target))
            continue
        path.rename(target)
        done.append((path, target))
    return done


def rewrite_path_string(value: str) -> str:
    if not value:
        return value
    out = value.replace("\\", "/")
    # storage-prefixed track_index style: download:Direct/... or external:Organized/...
    for old, new in DIR_RENAMES:
        out = re.sub(rf"(^|[:/|]){re.escape(old)}(?=/|$)", rf"\1{new}", out)
    return out


def rewrite_sqlite(db: Path) -> int:
    if not db.is_file():
        print(f"skip db (missing): {db}")
        return 0
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    changed = 0

    tables_cols = [
        ("track_locations", "save_folder"),
        ("tracks", "canonical_rel"),
        ("tracks", "canonical_storage"),
        ("sync_ledger", "save_folder"),
        ("sync_ledger", "title"),
        ("subscriptions", "save_folder"),
        ("external_playlists", "dir_name"),
        ("external_raw_tracks", "dir_name"),
        ("external_raw_tracks", "rel_path"),
    ]
    for table, col in tables_cols:
        try:
            cur.execute(f"SELECT rowid, {col} FROM {table}")
        except sqlite3.OperationalError:
            continue
        rows = cur.fetchall()
        for row in rows:
            old = row[1]
            if old is None:
                continue
            new = rewrite_path_string(str(old))
            if new != old:
                cur.execute(
                    f"UPDATE {table} SET {col}=? WHERE rowid=?",
                    (new, row[0]),
                )
                changed += 1
    con.commit()
    con.close()
    return changed


def rewrite_track_index(path: Path) -> int:
    if not path.is_file():
        print(f"skip track_index (missing): {path}")
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    if isinstance(data, dict):
        for key, val in list(data.items()):
            if not isinstance(val, str):
                continue
            new = rewrite_path_string(val)
            if new != val:
                data[key] = new
                changed += 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed


def main() -> None:
    if not DATA.is_dir():
        raise SystemExit(f"data root missing: {DATA}")

    print(f"data root: {DATA}")
    renamed = rename_tree(DATA)
    for old, new in renamed:
        print(f"  mv {old.relative_to(DATA)} -> {new.relative_to(DATA)}")
    print(f"dirs renamed: {len(renamed)}")

    db = DATA.parent / "config" / "yubal" / "yubal.db"
    if not db.is_file():
        # legacy path before config was split out of /data
        db = DATA / "config" / "yubal" / "yubal.db"
    n = rewrite_sqlite(db)
    print(f"db cells updated: {n} ({db})")

    # track_index may live under download/.yubal after rename
    for idx in DATA.rglob("track_index.json"):
        c = rewrite_track_index(idx)
        print(f"track_index updated: {c} ({idx.relative_to(DATA)})")

    print("done")


if __name__ == "__main__":
    main()
