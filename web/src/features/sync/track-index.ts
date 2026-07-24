import type { TrackSortKey } from "@/api/settings";
import type { SyncTrackItem } from "@/api/sync-ledger";
import { pinyin } from "pinyin-pro";

export type { TrackSortKey };

export const DEFAULT_INDEX_THRESHOLD = 50;

export const INDEX_LETTERS = [
  "A",
  "B",
  "C",
  "D",
  "E",
  "F",
  "G",
  "H",
  "I",
  "J",
  "K",
  "L",
  "M",
  "N",
  "O",
  "P",
  "Q",
  "R",
  "S",
  "T",
  "U",
  "V",
  "W",
  "X",
  "Y",
  "Z",
  "#",
] as const;

export type IndexLetter = (typeof INDEX_LETTERS)[number];

export type TrackSection = {
  letter: IndexLetter;
  tracks: SyncTrackItem[];
};

/** Sort / section field value for a track. */
export function trackSortValue(
  track: SyncTrackItem,
  key: TrackSortKey,
): string {
  if (key === "artist") return (track.artist || "").trim();
  if (key === "album") return (track.album || "").trim();
  return (track.title || "").trim();
}

/** Contacts-style index letter: Latin A–Z, else pinyin initial, else #. */
export function indexLetterFor(text: string): IndexLetter {
  const raw = text.trim();
  if (!raw) return "#";
  const ch = raw[0]!;
  if (/[A-Za-z]/.test(ch)) return ch.toUpperCase() as IndexLetter;
  if (/[0-9]/.test(ch)) return "#";
  if (/[\u4e00-\u9fff]/.test(ch)) {
    const initial = pinyin(ch, {
      pattern: "first",
      toneType: "none",
      type: "array",
    })[0];
    const letter = (initial || "").trim().toUpperCase();
    if (letter >= "A" && letter <= "Z") return letter as IndexLetter;
  }
  return "#";
}

function compareLocale(a: string, b: string): number {
  return a.localeCompare(b, "zh-CN", { sensitivity: "base", numeric: true });
}

/** Sort tracks by key, then stable by original index. */
export function sortTracks(
  tracks: SyncTrackItem[],
  key: TrackSortKey,
): SyncTrackItem[] {
  return [...tracks].sort((a, b) => {
    const byKey = compareLocale(trackSortValue(a, key), trackSortValue(b, key));
    if (byKey !== 0) return byKey;
    return a.index - b.index;
  });
}

/** Build A–Z/# sections (empty letters omitted). */
export function buildTrackSections(
  tracks: SyncTrackItem[],
  key: TrackSortKey,
): TrackSection[] {
  const sorted = sortTracks(tracks, key);
  const buckets = new Map<IndexLetter, SyncTrackItem[]>();
  for (const track of sorted) {
    const letter = indexLetterFor(trackSortValue(track, key));
    const list = buckets.get(letter);
    if (list) list.push(track);
    else buckets.set(letter, [track]);
  }
  return INDEX_LETTERS.filter((letter) => buckets.has(letter)).map(
    (letter) => ({
      letter,
      tracks: buckets.get(letter)!,
    }),
  );
}

export function sectionDomId(folder: string, letter: IndexLetter): string {
  const safe = folder.replace(/[^a-zA-Z0-9_-]/g, "_") || "folder";
  return `track-sec-${safe}-${letter === "#" ? "hash" : letter}`;
}
