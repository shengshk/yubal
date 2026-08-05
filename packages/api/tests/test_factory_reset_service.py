from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import Engine
from yubal_api.db.external_library import ExternalRawTrack
from yubal_api.db.external_library_repository import ExternalLibraryRepository
from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.db.wanted import WantedTrack
from yubal_api.db.wanted_repository import WantedRepository
from yubal_api.services.auth import AuthManager
from yubal_api.services.factory_reset_service import (
    FactoryResetMode,
    FactoryResetService,
)
from yubal_api.services.preferences import Preferences, PreferencesStore


def _service(
    tmp_path: Path,
    engine: Engine,
    *,
    auth_enabled: bool = False,
) -> tuple[FactoryResetService, PreferencesStore, AuthManager, dict[str, Path]]:
    roots = {
        "download": tmp_path / "data" / "download",
        "external": tmp_path / "data" / "external",
        "wanted": tmp_path / "data" / "wanted",
        "cache": tmp_path / "data" / "cache",
        "config": tmp_path / "config",
    }
    for root in roots.values():
        root.mkdir(parents=True, exist_ok=True)
    preferences = PreferencesStore(
        roots["config"] / "preferences.json",
        roots["download"],
        defaults=Preferences(),
    )
    auth = AuthManager(
        enabled=auth_enabled,
        auth_file=roots["config"] / "auth.json",
    )
    service = FactoryResetService(
        engine=engine,
        preferences=preferences,
        auth=auth,
        download_root=roots["download"],
        external_root=roots["external"],
        wanted_root=roots["wanted"],
        cache_root=roots["cache"],
        config_root=roots["config"],
        db_path=roots["config"] / "yubal" / "yubal.db",
        cookies_path=roots["config"] / "ytdlp" / "cookies.txt",
    )
    return service, preferences, auth, roots


def _write(path: Path, content: bytes = b"audio") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_preference_reset_preserves_access_credentials(
    tmp_path: Path,
    engine: Engine,
) -> None:
    service, preferences, _auth, _roots = _service(tmp_path, engine)
    preferences.update(
        scheduler_enabled=False,
        telegram_bot_token="token",
        telegram_admin_ids="123",
        telegram_user_ids="456",
        lastfm_api_key="lastfm",
    )

    preview = service.preview(FactoryResetMode.PREFERENCES)
    service.execute(FactoryResetMode.PREFERENCES, preview.token)

    effective = preferences.effective()
    assert effective.scheduler_enabled is True
    assert effective.telegram_bot_token == "token"
    assert effective.telegram_admin_ids == "123"
    assert effective.telegram_user_ids == "456"
    assert effective.lastfm_api_key == "lastfm"


def test_invalid_cleanup_preserves_trusted_hardlinks_and_external_originals(
    tmp_path: Path,
    engine: Engine,
) -> None:
    service, preferences, _auth, roots = _service(tmp_path, engine)
    preferences.update(scheduler_enabled=False, telegram_bot_token="token")

    catalog = TrackCatalogRepository(engine)
    valid = _write(roots["download"] / "direct" / "valid.mp3", b"valid")
    catalog.upsert_track(
        video_id="valid-id",
        title="Valid",
        artist="Artist",
        album_artist="Artist",
    )
    catalog.upsert_location(
        video_id="valid-id",
        save_folder="direct",
        relative_path="valid.mp3",
    )
    linked = roots["wanted"] / "linked-valid.mp3"
    os.link(valid, linked)

    bad_download = _write(
        roots["download"] / "direct" / "untracked.mp3",
        b"untracked",
    )
    bad_wanted = _write(roots["wanted"] / "bad.mp3", b"bad-wanted")
    wanted = WantedRepository(engine)
    wanted.add(
        WantedTrack(
            title="Bad",
            artists="Unknown",
            source="manual",
            relative_path="bad.mp3",
        )
    )
    wanted.add(
        WantedTrack(
            title="Verified",
            artists="Known",
            source="qq",
            source_id="qq-1",
        )
    )

    external_file = _write(
        roots["external"] / "raw" / "legacy" / "original.flac",
        b"external",
    )
    external = ExternalLibraryRepository(engine)
    external.upsert_playlist("legacy")
    external.upsert(
        ExternalRawTrack(
            rel_path="legacy/original.flac",
            dir_name="legacy",
        )
    )

    preview = service.preview(FactoryResetMode.INVALID)
    assert preview.list_entries == 1
    assert preview.files == 2
    assert preview.paths == 2

    service.execute(FactoryResetMode.INVALID, preview.token)

    assert not bad_download.exists()
    assert not bad_wanted.exists()
    assert valid.exists()
    assert linked.exists()
    assert external_file.exists()
    assert len(wanted.list_all()) == 1
    assert preferences.effective().scheduler_enabled is True
    assert preferences.effective().telegram_bot_token == "token"


def test_full_reset_requires_password_and_clears_all_product_data(
    tmp_path: Path,
    engine: Engine,
) -> None:
    service, preferences, auth, roots = _service(
        tmp_path,
        engine,
        auth_enabled=True,
    )
    ok, error, _cookie = auth.setup("owner", "secret", "secret")
    assert ok, error
    preferences.update(
        scheduler_enabled=False,
        telegram_bot_token="token",
        telegram_admin_ids="123",
    )

    for name in ("download", "external", "wanted"):
        _write(roots[name] / ".yubal-mount", name.encode())
    source = _write(roots["download"] / "direct" / "song.mp3", b"song")
    linked = roots["external"] / "raw" / "song.mp3"
    linked.parent.mkdir(parents=True, exist_ok=True)
    os.link(source, linked)
    _write(roots["wanted"] / "wish.flac", b"wish")
    _write(roots["cache"] / "preview.webm", b"preview")

    WantedRepository(engine).add(
        WantedTrack(title="Wish", artists="Artist", source="manual")
    )
    cookies = _write(
        roots["config"] / "ytdlp" / "cookies.txt",
        b"cookies",
    )
    _write(roots["config"] / "search_results.json", b"{}")
    runtime_state = _write(
        roots["config"] / "state" / "download" / "scrape_state.json",
        b"{}",
    )
    backup = _write(
        roots["config"] / "yubal" / "backups" / "backup.db",
        b"backup",
    )

    preview = service.preview(FactoryResetMode.FULL)
    assert preview.clears_account is True
    assert preview.clears_external_originals is True
    assert preview.backups == 1
    assert preview.files >= 3
    assert preview.paths > preview.files

    with pytest.raises(PermissionError):
        service.execute(
            FactoryResetMode.FULL,
            preview.token,
            password="wrong",
        )

    result = service.execute(
        FactoryResetMode.FULL,
        preview.token,
        password="secret",
    )

    assert result.requires_setup is True
    assert auth.needs_setup() is True
    assert preferences.effective().telegram_bot_token == ""
    assert WantedRepository(engine).list_all() == []
    assert not cookies.exists()
    assert not backup.exists()
    assert not (roots["config"] / "search_results.json").exists()
    assert not runtime_state.exists()
    for name in ("download", "external", "wanted"):
        assert sorted(path.name for path in roots[name].iterdir()) == [".yubal-mount"]
    assert list(roots["cache"].iterdir()) == []
