"""Tests for Telegram file_id cache and audio delivery helpers."""

from __future__ import annotations

from pathlib import Path

from yubal_api.services.telegram.sender import (
    _INLINE_AUDIO_EXTS,
    extract_delivery,
    extract_file_id,
)
from yubal_api.services.telegram.stores import FileIdStore


def test_inline_audio_exts_cover_matrix_music_formats() -> None:
    assert {".mp3", ".m4a", ".flac", ".wav", ".aac"} <= _INLINE_AUDIO_EXTS
    assert ".ogg" not in _INLINE_AUDIO_EXTS
    assert ".opus" not in _INLINE_AUDIO_EXTS
    assert ".webm" not in _INLINE_AUDIO_EXTS


def test_extract_delivery_prefers_audio() -> None:
    message = {
        "audio": {"file_id": "AUDIO1"},
        "document": {"file_id": "DOC1"},
    }
    assert extract_delivery(message) == ("AUDIO1", "audio")
    assert extract_file_id(message) == "AUDIO1"


def test_extract_delivery_voice() -> None:
    assert extract_delivery({"voice": {"file_id": "V1"}}) == ("V1", "voice")


def test_file_id_store_kind_mismatch_and_legacy(tmp_path: Path) -> None:
    path = tmp_path / "telegram_file_ids.json"
    path.write_text('{"vid-legacy": "LEGACY_ID"}\n', encoding="utf-8")
    store = FileIdStore(path)

    assert store.get("vid-legacy") == "LEGACY_ID"
    assert store.get("vid-legacy", kind="audio") is None
    assert store.get("vid-legacy", kind="document") is None

    store.put("vid-a", "AUDIO_ID", kind="audio")
    assert store.get("vid-a", kind="audio") == "AUDIO_ID"
    assert store.get("vid-a", kind="document") is None

    store.put("vid-d", "DOC_ID", kind="document")
    assert store.get("vid-d", kind="document") == "DOC_ID"
    assert store.get("vid-d", kind="audio") is None

    reloaded = FileIdStore(path)
    assert reloaded.get("vid-a", kind="audio") == "AUDIO_ID"
    assert reloaded.get("vid-legacy", kind="audio") is None
