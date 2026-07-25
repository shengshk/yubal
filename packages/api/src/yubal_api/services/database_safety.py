"""Verified SQLite backups used before automatic schema migrations."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class DatabaseBackup:
    path: Path
    size_bytes: int
    created_at: datetime


def verify_sqlite_database(path: Path) -> None:
    """Raise when SQLite cannot open the database or quick_check is not clean."""
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute("PRAGMA quick_check").fetchall()
    finally:
        connection.close()
    messages = [str(row[0]) for row in rows]
    if messages != ["ok"]:
        raise RuntimeError(
            "SQLite integrity check failed: " + "; ".join(messages[:10])
        )


def create_verified_database_backup(
    database_path: Path,
    backup_dir: Path,
    *,
    retain: int = 10,
) -> DatabaseBackup | None:
    """Create an online SQLite backup, verify it, and prune old snapshots."""
    if not database_path.is_file():
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(UTC)
    stamp = created_at.strftime("%Y%m%dT%H%M%S.%fZ")
    destination = backup_dir / f"yubal-pre-migration-{stamp}.db"
    temporary = destination.with_suffix(".tmp")

    source = sqlite3.connect(database_path)
    target = sqlite3.connect(temporary)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    try:
        verify_sqlite_database(temporary)
        os.chmod(temporary, 0o600)
        temporary.replace(destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    snapshots = sorted(
        backup_dir.glob("yubal-pre-migration-*.db"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    )
    for stale in snapshots[max(1, retain) :]:
        stale.unlink(missing_ok=True)

    return DatabaseBackup(
        path=destination,
        size_bytes=destination.stat().st_size,
        created_at=created_at,
    )
