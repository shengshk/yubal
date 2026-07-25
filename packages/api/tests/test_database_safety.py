from __future__ import annotations

import sqlite3
from pathlib import Path

from yubal_api.services.database_safety import (
    create_verified_database_backup,
    verify_sqlite_database,
)


def _create_database(path: Path, value: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sample VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def test_create_verified_database_backup(tmp_path: Path) -> None:
    database = tmp_path / "yubal.db"
    backups = tmp_path / "backups"
    _create_database(database, "before migration")

    result = create_verified_database_backup(database, backups)

    assert result is not None
    assert result.path.is_file()
    verify_sqlite_database(result.path)
    connection = sqlite3.connect(result.path)
    try:
        assert connection.execute("SELECT value FROM sample").fetchone() == (
            "before migration",
        )
    finally:
        connection.close()


def test_database_backup_retention(tmp_path: Path) -> None:
    database = tmp_path / "yubal.db"
    backups = tmp_path / "backups"
    _create_database(database, "value")

    for _ in range(4):
        create_verified_database_backup(database, backups, retain=2)

    assert len(list(backups.glob("yubal-pre-migration-*.db"))) == 2
