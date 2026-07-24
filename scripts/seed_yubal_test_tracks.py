#!/usr/bin/env python3
"""Seed / purge marked Direct test tracks for A–Z index UI QA.

Markers (any one is enough to identify fixtures):
  - relative path under Direct/__YUBAL_TEST__/
  - video_id prefix ``ybtest``
  - track origin ``yubal_test``
  - artist ``__YUBAL_TEST__``

Usage:
  python3 scripts/seed_yubal_test_tracks.py seed [--count 80]
  python3 scripts/seed_yubal_test_tracks.py purge
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIRECT = ROOT / "data" / "Download" / "Direct"
TEST_ARTIST = "__YUBAL_TEST__"
TEST_ARTIST_LABEL = "YUBALTEST"
TEST_ALBUM = "YUBALTEST Seed"
TEST_YEAR = "2026"
TEST_DIR = DATA_DIRECT / TEST_ARTIST / f"{TEST_YEAR} - {TEST_ALBUM}"
DB_PATH = ROOT / "config" / "yubal" / "yubal.db"
VIDEO_PREFIX = "ybtest"
ORIGIN = "yubal_test"

# Titles spanning Latin A–Z plus Chinese (pinyin initials) for index demos.
TITLE_POOL = [
    "Autumn Leaves",
    "Blue Moon Rising",
    "Crystal Clear",
    "Daydream Avenue",
    "Echoes of Rain",
    "Fading Lights",
    "Golden Hour",
    "Harbor Lights",
    "Ivory Tower",
    "June Bug",
    "Kingdom Come",
    "Lemon Tree",
    "Midnight Sun",
    "Northern Wind",
    "Ocean Drive",
    "Paper Planes",
    "Quiet Storm",
    "River Song",
    "Silver Lining",
    "Tuesday Waltz",
    "Under Stars",
    "Velvet Sky",
    "Winter Road",
    "Xylophone Heart",
    "Yellow Fields",
    "Zen Garden",
    "爱是怀疑",
    "北京一夜",
    "春风十里",
    "东风破",
    "平凡之路",
    "告白气球",
    "海阔天空",
    "江南",
    "可可托海的牧羊人",
    "蓝莲花",
    "明天会更好",
    "南方姑娘",
    "欧若拉",
    "平凡的一天",
    "起风了",
    "如愿",
    "十年",
    "特别的人",
    "唯一",
    "喜欢你",
    "一生所爱",
    "追光者",
    "最初的梦想",
]


def _source_opus() -> Path:
    matches = list(DATA_DIRECT.rglob("*.opus"))
    real = [p for p in matches if TEST_ARTIST not in p.parts]
    if not real:
        raise SystemExit(f"No source .opus under {DATA_DIRECT}")
    return real[0]


def seed(count: int) -> None:
    if count < 1:
        raise SystemExit("count must be >= 1")
    src = _source_opus()
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
    con = sqlite3.connect(DB_PATH)
    try:
        for i in range(1, count + 1):
            title = TITLE_POOL[(i - 1) % len(TITLE_POOL)]
            if i > len(TITLE_POOL):
                title = f"{title} {i}"
            video_id = f"{VIDEO_PREFIX}{i:04d}"
            filename = f"{i:02d} - {title}.opus"
            rel = f"{TEST_ARTIST}/{TEST_YEAR} - {TEST_ALBUM}/{filename}"
            dest = DATA_DIRECT / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                try:
                    os.link(src, dest)
                except OSError:
                    shutil.copy2(src, dest)

            con.execute(
                """
                INSERT INTO tracks (
                  video_id, title, artist, album_artist, album, track_number,
                  year, cover_url, lyrics, has_embedded_cover, has_lyrics_embedded,
                  has_lyrics_sidecar, cover_source, lyrics_source,
                  last_enriched_at, last_enrich_error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0, 0, 0, NULL, NULL, NULL, NULL, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                  title=excluded.title,
                  artist=excluded.artist,
                  album_artist=excluded.album_artist,
                  album=excluded.album,
                  track_number=excluded.track_number,
                  year=excluded.year,
                  updated_at=excluded.updated_at
                """,
                (
                    video_id,
                    title,
                    TEST_ARTIST_LABEL,
                    TEST_ARTIST_LABEL,
                    TEST_ALBUM,
                    i,
                    TEST_YEAR,
                    now,
                ),
            )
            existing = con.execute(
                """
                SELECT id FROM track_locations
                WHERE video_id=? AND save_folder='Direct'
                """,
                (video_id,),
            ).fetchone()
            if existing:
                con.execute(
                    """
                    UPDATE track_locations
                    SET relative_path=?, origin=?, updated_at=?
                    WHERE id=?
                    """,
                    (rel, ORIGIN, now, existing[0]),
                )
            else:
                con.execute(
                    """
                    INSERT INTO track_locations (
                      id, video_id, save_folder, relative_path, origin, updated_at
                    ) VALUES (?, ?, 'Direct', ?, ?, ?)
                    """,
                    (uuid.uuid4().hex, video_id, rel, ORIGIN, now),
                )

        total = con.execute(
            "SELECT COUNT(*) FROM track_locations WHERE save_folder='Direct'"
        ).fetchone()[0]
        con.execute(
            """
            UPDATE sync_ledger
            SET total_count=?, synced_count=?, real_download_count=?,
                hardlink_count=0, updated_at=?
            WHERE key='direct'
            """,
            (total, total, total, now),
        )
        con.commit()
    finally:
        con.close()
    print(f"Seeded {count} test tracks under {TEST_DIR}")
    print(f"Direct catalog now has {total} locations")
    print("Markers: path __YUBAL_TEST__ / video_id ybtest* / origin yubal_test")


def purge() -> None:
    con = sqlite3.connect(DB_PATH)
    try:
        ids = [
            row[0]
            for row in con.execute(
                "SELECT video_id FROM tracks WHERE video_id LIKE ?",
                (f"{VIDEO_PREFIX}%",),
            )
        ]
        # Also catch by origin / path
        loc_ids = [
            row[0]
            for row in con.execute(
                """
                SELECT video_id FROM track_locations
                WHERE save_folder='Direct' AND (
                  origin=? OR relative_path LIKE ?
                )
                """,
                (ORIGIN, f"{TEST_ARTIST}/%"),
            )
        ]
        victims = sorted(set(ids) | set(loc_ids))
        for video_id in victims:
            con.execute(
                "DELETE FROM track_locations WHERE video_id=? AND save_folder='Direct'",
                (video_id,),
            )
            # Remove track row only if no other locations remain
            left = con.execute(
                "SELECT COUNT(*) FROM track_locations WHERE video_id=?",
                (video_id,),
            ).fetchone()[0]
            if left == 0:
                con.execute("DELETE FROM tracks WHERE video_id=?", (video_id,))

        total = con.execute(
            "SELECT COUNT(*) FROM track_locations WHERE save_folder='Direct'"
        ).fetchone()[0]
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
        con.execute(
            """
            UPDATE sync_ledger
            SET total_count=?, synced_count=?, real_download_count=?,
                hardlink_count=0, updated_at=?
            WHERE key='direct'
            """,
            (total, total, total, now),
        )
        con.commit()
    finally:
        con.close()

    artist_root = DATA_DIRECT / TEST_ARTIST
    if artist_root.exists():
        shutil.rmtree(artist_root)
        print(f"Removed {artist_root}")
    print(f"Purged {len(victims)} test video_ids; Direct locations now {total}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    seed_p = sub.add_parser("seed", help="Create marked Direct fixtures")
    seed_p.add_argument("--count", type=int, default=80)
    sub.add_parser("purge", help="Delete all marked Direct fixtures")
    args = parser.parse_args()
    if args.cmd == "seed":
        seed(args.count)
    else:
        purge()


if __name__ == "__main__":
    main()
