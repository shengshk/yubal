"""Tests for post-delete folder / cover GC helpers."""

from pathlib import Path

from yubal_api.services.library_ops import cleanup_after_audio_removed


def test_cleanup_after_audio_removed_drops_covers_and_empty_dirs(
    tmp_path: Path,
) -> None:
    root = tmp_path / "Organized" / "Playlist"
    album = root / "Artist" / "2020 - Album"
    album.mkdir(parents=True)
    track = album / "01 - Song.flac"
    track.write_bytes(b"audio")
    (album / "cover.jpg").write_bytes(b"cover")
    (album.parent / "artist.jpg").write_bytes(b"artist")

    track.unlink()
    cleanup_after_audio_removed(album, root)

    assert not (album / "cover.jpg").exists()
    assert not (album.parent / "artist.jpg").exists()
    assert not album.exists()
    assert not album.parent.exists()
    assert root.is_dir()


def test_cleanup_keeps_sidecars_when_audio_remains(tmp_path: Path) -> None:
    root = tmp_path / "Organized" / "Playlist"
    album = root / "Artist" / "2020 - Album"
    album.mkdir(parents=True)
    (album / "01 - A.flac").write_bytes(b"a")
    (album / "02 - B.flac").write_bytes(b"b")
    cover = album / "cover.jpg"
    cover.write_bytes(b"cover")
    artist = album.parent / "artist.jpg"
    artist.write_bytes(b"artist")

    (album / "01 - A.flac").unlink()
    cleanup_after_audio_removed(album, root)

    assert cover.is_file()
    assert artist.is_file()
    assert album.is_dir()
