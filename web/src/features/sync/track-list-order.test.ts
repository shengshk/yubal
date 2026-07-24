import { describe, expect, test } from "bun:test";
import type { SyncTrackItem } from "@/api/sync-ledger";
import {
  assignDisplayNumbers,
  buildOrderedTrackSections,
  classifyTrackBucket,
  sortTracksUnified,
  type TrackListOrderContext,
} from "./track-list-order";

function track(
  partial: Partial<SyncTrackItem> &
    Pick<SyncTrackItem, "title" | "relative_path">,
): SyncTrackItem {
  return {
    index: partial.index ?? 0,
    artist: partial.artist ?? null,
    album: partial.album ?? null,
    exists: partial.exists ?? true,
    storage: partial.storage ?? "real",
    ...partial,
  };
}

const emptySets = {
  offlineIds: new Set<string>(),
  blockedIds: new Set<string>(),
};

describe("classifyTrackBucket", () => {
  test("external junk grades", () => {
    const ctx: TrackListOrderContext = {
      external: true,
      allowMutate: true,
      ...emptySets,
    };
    expect(
      classifyTrackBucket(
        track({
          title: "A",
          relative_path: "Raw/a",
          tier: "raw",
          junk_kind: "rw",
          is_junk: true,
        }),
        ctx,
      ),
    ).toBe("junk_rw");
    expect(
      classifyTrackBucket(
        track({
          title: "B",
          relative_path: "Raw/b",
          tier: "raw",
          junk_kind: "ro",
          is_junk: true,
        }),
        { ...ctx, allowMutate: false },
      ),
    ).toBe("junk_ro");
    expect(
      classifyTrackBucket(
        track({
          title: "C",
          relative_path: "Raw/c",
          tier: "raw",
          tags_complete: true,
        }),
        ctx,
      ),
    ).toBe("unmatched");
  });

  test("offline and blocked before active", () => {
    const ctx: TrackListOrderContext = {
      external: false,
      allowMutate: true,
      offlineIds: new Set(["off1"]),
      blockedIds: new Set(["blk1"]),
    };
    expect(
      classifyTrackBucket(
        track({
          title: "O",
          relative_path: "a.flac",
          video_id: "off1",
        }),
        ctx,
      ),
    ).toBe("offline");
    expect(
      classifyTrackBucket(
        track({
          title: "B",
          relative_path: "b.flac",
          video_id: "blk1",
        }),
        ctx,
      ),
    ).toBe("blocked");
  });
});

describe("sortTracksUnified + assignDisplayNumbers", () => {
  test("bucket order and stable prefixes", () => {
    const ctx: TrackListOrderContext = {
      external: true,
      allowMutate: false,
      ...emptySets,
    };
    const tracks = [
      track({
        title: "Zebra",
        relative_path: "Raw/z",
        tier: "raw",
        junk_kind: "ro",
        is_junk: true,
      }),
      track({
        title: "Banana",
        relative_path: "Organized/b.flac",
        video_id: "v1",
        tier: "complete",
      }),
      track({
        title: "Apple",
        relative_path: "Organized/a.flac",
        video_id: "v2",
        tier: "complete",
      }),
      track({
        title: "Mango",
        relative_path: "Raw/m",
        tier: "raw",
        tags_complete: true,
      }),
    ];
    const ordered = sortTracksUnified(tracks, "title", ctx);
    expect(ordered.map((t) => t.title)).toEqual([
      "Apple",
      "Banana",
      "Mango",
      "Zebra",
    ]);
    const numbers = assignDisplayNumbers(tracks, "title", ctx);
    expect(numbers.get("v2")).toBe("1");
    expect(numbers.get("v1")).toBe("2");
    expect(numbers.get("Raw/m")).toBe("X1");
    expect(numbers.get("Raw/z")).toBe("R1");
  });

  test("pin does not change display numbers", () => {
    const ctx: TrackListOrderContext = {
      external: false,
      allowMutate: true,
      offlineIds: new Set(["off"]),
      blockedIds: new Set(),
    };
    const tracks = [
      track({
        title: "Live",
        relative_path: "a.flac",
        video_id: "live",
      }),
      track({
        title: "Gone",
        relative_path: "",
        video_id: "off",
        membership_status: "offline",
        exists: false,
        storage: "missing",
      }),
    ];
    const before = assignDisplayNumbers(tracks, "title", ctx);
    // Simulating pin: numbers are computed from full set, not display order.
    const after = assignDisplayNumbers(tracks, "title", ctx);
    expect(before.get("live")).toBe("1");
    expect(before.get("off")).toBe("L1");
    expect(after).toEqual(before);
  });

  test("indexed sections preserve bucket order (no cross-bucket resort)", () => {
    const ctx: TrackListOrderContext = {
      external: true,
      allowMutate: true,
      ...emptySets,
    };
    const tracks = [
      track({
        title: "Beta",
        relative_path: "Organized/b",
        video_id: "vb",
        tier: "complete",
      }),
      track({
        title: "AlphaRaw",
        relative_path: "Raw/a",
        tier: "raw",
        tags_complete: true,
      }),
      track({
        title: "Alpha",
        relative_path: "Organized/a",
        video_id: "va",
        tier: "complete",
      }),
    ];
    const ordered = sortTracksUnified(tracks, "title", ctx);
    expect(ordered.map((t) => t.title)).toEqual([
      "Alpha",
      "Beta",
      "AlphaRaw",
    ]);
    const sections = buildOrderedTrackSections(ordered, "title");
    expect(sections.map((s) => s.letter)).toEqual(["A", "B", "A"]);
    expect(sections[0]!.tracks[0]!.title).toBe("Alpha");
    expect(sections[2]!.tracks[0]!.title).toBe("AlphaRaw");
  });
});
