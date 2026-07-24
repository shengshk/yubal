#!/usr/bin/env python3
"""Regenerate PWA PNG icons from the disc mark (any + maskable + apple-touch)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent
BG = (16, 15, 15, 255)  # #100F0F
FG = (58, 169, 159, 255)  # #3AA99F


def draw_disc(size: int, logo_ratio: float) -> Image.Image:
    """Draw centered disc; logo_ratio is outer-ring diameter as fraction of canvas."""
    img = Image.new("RGBA", (size, size), BG)
    draw = ImageDraw.Draw(img)
    cx = cy = size / 2
    outer_r = size * logo_ratio / 2
    stroke = max(2, int(size * 0.055))
    bbox = [cx - outer_r, cy - outer_r, cx + outer_r, cy + outer_r]
    draw.ellipse(bbox, outline=FG, width=stroke)
    inner_r = outer_r * 0.18
    ibox = [cx - inner_r, cy - inner_r, cx + inner_r, cy + inner_r]
    draw.ellipse(ibox, outline=FG, width=stroke)
    mid_r = outer_r * 0.62
    arc_stroke = max(2, int(stroke * 0.9))
    mbox = [cx - mid_r, cy - mid_r, cx + mid_r, cy + mid_r]
    draw.arc(mbox, start=200, end=250, fill=FG, width=arc_stroke)
    draw.arc(mbox, start=20, end=70, fill=FG, width=arc_stroke)
    return img.convert("RGB")


def main() -> None:
    specs = [
        ("icon-192.png", 192, 0.72),  # any
        ("icon-512.png", 512, 0.72),
        ("icon-maskable-192.png", 192, 0.58),  # maskable safe zone
        ("icon-maskable-512.png", 512, 0.58),
        ("apple-touch-icon.png", 180, 0.70),
    ]
    for name, size, ratio in specs:
        path = OUT / name
        draw_disc(size, ratio).save(path, "PNG", optimize=True)
        print(f"wrote {path.name} ({size}px)")


if __name__ == "__main__":
    main()
