"""Image dimension parsing and cover quality scoring."""

from __future__ import annotations

import logging
import struct

logger = logging.getLogger(__name__)


def image_dimensions(data: bytes) -> tuple[int, int] | None:
    """Return (width, height) for JPEG/PNG/WebP bytes, or None."""
    if not data or len(data) < 24:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return _png_size(data)
    if data[:2] == b"\xff\xd8":
        return _jpeg_size(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return _webp_size(data)
    return None


def cover_quality_score(data: bytes) -> int:
    """Higher is better: prefer more pixels, then larger file."""
    dims = image_dimensions(data)
    if dims is None:
        # Unknown format — still usable, rank by size only
        return max(0, len(data) // 10)
    w, h = dims
    return w * h * 10 + min(len(data), 5_000_000) // 1000


def _png_size(data: bytes) -> tuple[int, int] | None:
    try:
        # IHDR is the first chunk after the 8-byte signature
        if data[12:16] != b"IHDR":
            return None
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    except (struct.error, IndexError):
        return None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    i = 2
    n = len(data)
    try:
        while i + 9 < n:
            if data[i] != 0xFF:
                return None
            marker = data[i + 1]
            i += 2
            # Soften / RST / TEM have no length
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                continue
            if i + 2 > n:
                return None
            length = struct.unpack(">H", data[i : i + 2])[0]
            if length < 2:
                return None
            # SOF0..SOF3, SOF5..SOF7, SOF9..SOF11, SOF13..SOF15
            if marker in {
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            }:
                if i + 7 > n:
                    return None
                h, w = struct.unpack(">HH", data[i + 3 : i + 7])
                return int(w), int(h)
            i += length
    except (struct.error, IndexError):
        return None
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    try:
        if data[12:16] == b"VP8X" and len(data) >= 30:
            # canvas width/height are 24-bit little-endian minus 1
            w = 1 + int.from_bytes(data[24:27], "little")
            h = 1 + int.from_bytes(data[27:30], "little")
            return w, h
        if data[12:16] == b"VP8 " and len(data) >= 30:
            w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
            h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
            return w, h
        if data[12:16] == b"VP8L" and len(data) >= 25:
            bits = struct.unpack("<I", data[21:25])[0]
            w = (bits & 0x3FFF) + 1
            h = ((bits >> 14) & 0x3FFF) + 1
            return w, h
    except (struct.error, IndexError):
        return None
    return None
