"""Tests for cover quality scoring."""

from yubal.utils.image_quality import cover_quality_score, image_dimensions


def _png(w: int, h: int) -> bytes:
    # Minimal valid-enough PNG with IHDR (not a full PNG decode needed)
    import struct
    import zlib

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    ihdr_len = struct.pack(">I", 13)
    ihdr_crc = struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF)
    return signature + ihdr_len + b"IHDR" + ihdr_data + ihdr_crc


def test_png_dimensions() -> None:
    data = _png(1200, 1200)
    assert image_dimensions(data) == (1200, 1200)


def test_higher_resolution_scores_higher() -> None:
    small = _png(100, 100) + b"\x00" * 100
    large = _png(1200, 1200) + b"\x00" * 100
    assert cover_quality_score(large) > cover_quality_score(small)
