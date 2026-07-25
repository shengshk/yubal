import type { TrackSortKey } from "@/api/settings";
import type { SyncTrackItem } from "@/api/sync-ledger";
import {
  INDEX_LETTERS,
  indexLetterFor,
  trackSortValue,
  type IndexLetter,
  type TrackSection,
} from "@/features/sync/track-index";

/** Shared playlist status buckets (priority top → bottom). */
export type TrackBucket =
  | "active"
  | "offline"
  | "blocked"
  | "meta_verified"
  | "unmatched"
  | "junk_rw"
  | "junk_ro";

export type JunkKind = "rw" | "ro";

export const TRACK_BUCKET_ORDER: readonly TrackBucket[] = [
  "active",
  "offline",
  "blocked",
  "meta_verified",
  "unmatched",
  "junk_rw",
  "junk_ro",
] as const;

const BUCKET_PREFIX: Record<TrackBucket, string> = {
  active: "",
  offline: "L",
  blocked: "B",
  meta_verified: "V",
  unmatched: "X",
  junk_rw: "W",
  junk_ro: "R",
};

/** i18n key suffix for each display-number prefix (external / Direct / Sub). */
export const BUCKET_PREFIX_HINT_KEY: Record<string, string> = {
  "": "indexHintActive",
  L: "indexHintOffline",
  B: "indexHintBlocked",
  V: "indexHintMetaVerified",
  X: "indexHintUnmatched",
  W: "indexHintJunkRw",
  R: "indexHintJunkRo",
};

/** Wanted-list numbering prefixes. */
export const WANTED_PREFIX_HINT_KEY: Record<string, string> = {
  "": "indexHintWantedMatched",
  W: "indexHintWantedUnmatched",
};

const BUCKET_RANK = Object.fromEntries(
  TRACK_BUCKET_ORDER.map((bucket, i) => [bucket, i]),
) as Record<TrackBucket, number>;

export type TrackListOrderContext = {
  external: boolean;
  allowMutate: boolean;
  offlineIds: ReadonlySet<string>;
  blockedIds: ReadonlySet<string>;
};

function compareLocale(a: string, b: string): number {
  return a.localeCompare(b, "zh-CN", { sensitivity: "base", numeric: true });
}

/** Stable identity for numbering / pin keys. */
export function trackIdentity(track: SyncTrackItem): string {
  return track.video_id || track.relative_path;
}

export function resolveJunkKind(
  track: SyncTrackItem,
  allowMutate: boolean,
): JunkKind | null {
  // A valid Wanted-source verification outranks a failed YTM lookup. Keep the
  // row in V so the list, summary count, and heart action share one meaning.
  if (track.meta_status === "verified") {
    return null;
  }
  if (track.junk_kind === "rw" || track.junk_kind === "ro") {
    return track.junk_kind;
  }
  if (track.is_junk) {
    return allowMutate ? "rw" : "ro";
  }
  if (!allowMutate && track.tags_complete === false) {
    return "ro";
  }
  return null;
}

export function classifyTrackBucket(
  track: SyncTrackItem,
  ctx: TrackListOrderContext,
): TrackBucket {
  const vid = track.video_id ?? "";
  if (
    track.membership_status === "blocked" ||
    (vid && ctx.blockedIds.has(vid))
  ) {
    return "blocked";
  }
  if (
    track.membership_status === "offline" ||
    (vid && ctx.offlineIds.has(vid))
  ) {
    return "offline";
  }

  const unmatched = track.tier === "raw" || (ctx.external && !track.video_id);
  if (ctx.external && unmatched) {
    const kind = resolveJunkKind(track, ctx.allowMutate);
    if (kind === "rw") return "junk_rw";
    if (kind === "ro") return "junk_ro";
    if (track.meta_status === "verified") return "meta_verified";
    return "unmatched";
  }
  return "active";
}

function stableKey(track: SyncTrackItem): string {
  return (track.relative_path || track.video_id || "").trim();
}

/** Bucket order → sort key → relative_path / video_id. */
export function sortTracksUnified(
  tracks: readonly SyncTrackItem[],
  sortKey: TrackSortKey,
  ctx: TrackListOrderContext,
): SyncTrackItem[] {
  return [...tracks].sort((a, b) => {
    const bucketDiff =
      BUCKET_RANK[classifyTrackBucket(a, ctx)] -
      BUCKET_RANK[classifyTrackBucket(b, ctx)];
    if (bucketDiff !== 0) return bucketDiff;
    const byKey = compareLocale(
      trackSortValue(a, sortKey),
      trackSortValue(b, sortKey),
    );
    if (byKey !== 0) return byKey;
    return compareLocale(stableKey(a), stableKey(b));
  });
}

/**
 * Bucket-stable display numbers. Independent of pin / screen order.
 * Prefixes: (none) / L / B / V / X / W / R.
 */
export function assignDisplayNumbers(
  tracks: readonly SyncTrackItem[],
  sortKey: TrackSortKey,
  ctx: TrackListOrderContext,
): Map<string, string> {
  const numbers = new Map<string, string>();
  const counters: Record<TrackBucket, number> = {
    active: 0,
    offline: 0,
    blocked: 0,
    meta_verified: 0,
    unmatched: 0,
    junk_rw: 0,
    junk_ro: 0,
  };
  const ordered = sortTracksUnified(tracks, sortKey, ctx);
  for (const track of ordered) {
    const bucket = classifyTrackBucket(track, ctx);
    counters[bucket] += 1;
    numbers.set(
      trackIdentity(track),
      `${BUCKET_PREFIX[bucket]}${counters[bucket]}`,
    );
  }
  return numbers;
}

/** Letter prefix of a display index like ``V3`` / ``12`` / ``W1``. */
export function displayIndexPrefix(displayIndex: string): string {
  const m = /^([A-Za-z]*)/.exec(displayIndex.trim());
  return m?.[1] ?? "";
}

/**
 * A–Z sections over an already bucket-sorted list.
 * Letter may repeat when a new bucket starts; order is never re-sorted.
 */
export function buildOrderedTrackSections(
  orderedTracks: readonly SyncTrackItem[],
  sortKey: TrackSortKey,
): TrackSection[] {
  const sections: TrackSection[] = [];
  let current: TrackSection | null = null;
  for (const track of orderedTracks) {
    const letter: IndexLetter = indexLetterFor(trackSortValue(track, sortKey));
    if (!current || current.letter !== letter) {
      current = { letter, tracks: [] };
      sections.push(current);
    }
    current.tracks.push(track);
  }
  return sections;
}

/** Unique section id when the same letter can appear more than once. */
export function orderedSectionDomId(
  folder: string,
  letter: IndexLetter,
  occurrence: number,
): string {
  const safe = folder.replace(/[^a-zA-Z0-9_-]/g, "_") || "folder";
  const letterPart = letter === "#" ? "hash" : letter;
  return `track-sec-${safe}-${letterPart}-${occurrence}`;
}

export function lettersInSections(
  sections: readonly TrackSection[],
): IndexLetter[] {
  const seen = new Set<IndexLetter>();
  const out: IndexLetter[] = [];
  for (const letter of INDEX_LETTERS) {
    if (sections.some((s) => s.letter === letter) && !seen.has(letter)) {
      seen.add(letter);
      out.push(letter);
    }
  }
  return out;
}
