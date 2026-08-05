import { basePath } from "@/lib/base-path";
import { sharedJsonGet } from "./shared-get";

/** "raw" = scanned but unmatched (no video_id); others mirror sync-ledger tiers. */
export type ExternalTrackTier = "raw" | "draft" | "complete" | "premium";
export type ExternalMatchStatus =
  | "matched"
  | "unmatched"
  | "pending"
  | "rejected";

export type ExternalDeleteMode =
  | "forget_matched"
  | "delete_matched"
  | "move_matched_to_direct"
  | "add_matched_to_direct"
  | "add_meta_verified_to_wanted"
  | "delete_unmatched"
  | "archive_meta_rejected"
  | "delete_meta_rejected"
  | "delete_all"
  | "clear_offline_delete"
  | "clear_offline_to_raw_delete";

export type ExternalPlaylist = {
  dir_name: string;
  allow_mutate: boolean;
  access_mode: "pending" | "readonly" | "managed";
  access_mode_locked: boolean;
  source_mutated_at?: string | null;
  source_mutation_kind?: string | null;
  show_raw: boolean;
  show_junk: boolean;
  inventory_scanned: boolean;
  unmatched_count: number;
  matched_count: number;
  meta_verified_count: number;
  meta_rejected_count: number;
  meta_rejected_mutable_count: number;
  cloud: number;
  local: number;
  offline: number;
  exclusive: number;
  shared: number;
  hardlink: number;
  cover_track_path?: string | null;
  enabled: boolean;
  max_items: number;
  sync_jitter_seconds: number;
  offline_marking_enabled: boolean;
  offline_cleanup_enabled: boolean;
  offline_cleanup_action: "delete" | "archive";
  offline_cleanup_delay_hours: number;
  last_synced_at: string | null;
  last_sync_status: string | null;
  /** @deprecated prefer unmatched_count + matched_count */
  raw_count?: number;
  total_count?: number;
};

export type ExternalPlaylistUpdate = Partial<{
  allow_mutate: boolean;
  access_mode: "pending" | "readonly" | "managed";
  show_raw: boolean;
  show_junk: boolean;
  enabled: boolean;
  max_items: number;
  sync_jitter_seconds: number;
  offline_marking_enabled: boolean;
  offline_cleanup_enabled: boolean;
  offline_cleanup_action: "delete" | "archive";
  offline_cleanup_delay_hours: number;
}>;

export type ExternalTrack = {
  rel_path: string;
  title: string;
  artist: string | null;
  album?: string | null;
  album_artist?: string | null;
  year?: string | null;
  track_number?: number | null;
  match_status: ExternalMatchStatus;
  video_id?: string | null;
  tier?: ExternalTrackTier | null;
  cover_source?: string | null;
  cover_url?: string | null;
  has_embedded_cover?: boolean;
  exists?: boolean;
  is_raw?: boolean;
  tags_complete?: boolean;
  /** Readonly incomplete / rejected — junk tier for UI. */
  is_junk?: boolean;
  /** Writable (rw) vs readonly (ro) junk grade. */
  junk_kind?: "rw" | "ro" | null;
  /** Already present under Direct (catalog); add button should stay disabled. */
  in_direct?: boolean;
  meta_status?: "pending" | "verified" | "rejected" | string;
  meta_source?: string | null;
  meta_source_id?: string | null;
  meta_source_url?: string | null;
  /** Permission is derived from the original source, not the display playlist. */
  can_mutate?: boolean;
};

export type ExternalTrackPage = {
  total: number;
  offset: number;
  next_offset: number | null;
  items: ExternalTrack[];
};

export type ExternalScanResult = {
  scanned: number;
  added: number;
  updated: number;
  removed: number;
  errors: number;
};

export type ExternalMatchCandidate = {
  video_id: string;
  title: string;
  artists: string;
  album?: string;
  thumbnail_url?: string | null;
  title_score?: number;
  artist_score?: number;
  score?: number;
};

export type ExternalMetaCandidate = {
  source: string;
  source_id: string;
  title: string;
  artists: string;
  album?: string;
  source_url?: string | null;
  thumbnail_url?: string | null;
  duration_seconds?: number | null;
  score?: number;
};

export type ExternalMatchResult = {
  rel_path?: string;
  matched: boolean;
  video_id?: string | null;
  ingested?: boolean;
  mode_used?: "strict" | "relaxed";
  candidates?: ExternalMatchCandidate[];
  meta_candidates?: ExternalMetaCandidate[];
  match_status?: ExternalMatchStatus;
};

export type ExternalMatchBatchResult = {
  checked: number;
  matched: number;
  deferred: number;
  rejected: number;
  errors: number;
};

export type ExternalSyncResult = {
  matched: number;
  recovered: number;
  checked: number;
  errors: number;
  deferred: number;
  rejected: number;
  meta_checked: number;
  meta_verified: number;
  enriched: number;
  upgraded: number;
  asset_errors: number;
  queued?: boolean;
};

export type ExternalDeleteResult = {
  deleted_files: number;
  deleted_locations: number;
  deleted_raw: number;
  moved: number;
  reset_matches: number;
  skipped_readonly: number;
  errors: number;
};

function errorMessage(body: { detail?: string; message?: string } | null) {
  return body?.detail ?? body?.message ?? "Request failed";
}

async function errorFromResponse(res: Response): Promise<{ error: string }> {
  const body = (await res.json().catch(() => null)) as {
    detail?: string;
    message?: string;
  } | null;
  return { error: errorMessage(body) };
}

export async function listExternalPlaylists(): Promise<ExternalPlaylist[]> {
  const result = await sharedJsonGet<
    ExternalPlaylist[] | { items?: ExternalPlaylist[] }
  >(`${basePath}/api/external/playlists`);
  if (!result.ok || !result.data) return [];
  const data = result.data;
  return Array.isArray(data) ? data : (data.items ?? []);
}

export async function updateExternalPlaylist(
  dirName: string,
  updates: ExternalPlaylistUpdate,
): Promise<ExternalPlaylist | { error: string }> {
  const res = await fetch(
    `${basePath}/api/external/playlists/${encodeURIComponent(dirName)}`,
    {
      method: "PATCH",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    },
  );
  if (!res.ok) return errorFromResponse(res);
  return (await res.json()) as ExternalPlaylist;
}

export async function activatePendingExternalPlaylists(
  accessMode: "readonly" | "managed",
): Promise<{ activated: number } | { error: string }> {
  const res = await fetch(`${basePath}/api/external/activate-pending`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_mode: accessMode }),
  });
  if (!res.ok) return errorFromResponse(res);
  return (await res.json()) as { activated: number };
}

export async function listExternalPlaylistTracks(
  dirName: string,
): Promise<ExternalTrack[]> {
  const result = await sharedJsonGet<
    ExternalTrack[] | { items?: ExternalTrack[] }
  >(`${basePath}/api/external/playlists/${encodeURIComponent(dirName)}/tracks`);
  if (!result.ok || !result.data) return [];
  const data = result.data;
  return Array.isArray(data) ? data : (data.items ?? []);
}

export async function listExternalPlaylistTracksPage(
  dirName: string,
  options: { offset?: number; limit?: number; refresh?: boolean } = {},
): Promise<ExternalTrackPage> {
  const params = new URLSearchParams({
    offset: String(options.offset ?? 0),
    limit: String(options.limit ?? 100),
  });
  if (options.refresh) params.set("refresh", "true");
  const result = await sharedJsonGet<ExternalTrackPage>(
    `${basePath}/api/external/playlists/${encodeURIComponent(dirName)}/tracks/page?${params}`,
  );
  return result.ok && result.data
    ? result.data
    : { total: 0, offset: 0, next_offset: null, items: [] };
}

export async function scanExternal(): Promise<
  ExternalScanResult | { error: string }
> {
  const res = await fetch(`${basePath}/api/external/scan`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) return errorFromResponse(res);
  return (await res.json()) as ExternalScanResult;
}

export async function syncExternalPlaylist(
  dirName: string,
  actions?: {
    enrich?: boolean;
    raw_match?: boolean;
    verify_meta?: boolean;
    junk_match?: boolean;
  },
): Promise<ExternalSyncResult | { error: string }> {
  const body = {
    enrich: actions?.enrich ?? true,
    raw_match: actions?.raw_match ?? true,
    verify_meta: actions?.verify_meta ?? true,
    junk_match: actions?.junk_match ?? false,
  };
  const res = await fetch(
    `${basePath}/api/external/playlists/${encodeURIComponent(dirName)}/sync`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  if (!res.ok) return errorFromResponse(res);
  return (await res.json()) as ExternalSyncResult;
}

export async function deleteExternalPlaylist(
  dirName: string,
  mode: ExternalDeleteMode,
): Promise<ExternalDeleteResult | { error: string }> {
  const params = new URLSearchParams({
    confirm: "true",
    mode,
  });
  const res = await fetch(
    `${basePath}/api/external/playlists/${encodeURIComponent(dirName)}?${params}`,
    { method: "DELETE", credentials: "include" },
  );
  if (!res.ok) return errorFromResponse(res);
  return (await res.json()) as ExternalDeleteResult;
}

/** Match a single raw file by its path relative to External (Raw/… or Raw-relative). */
export async function matchExternalTrack(
  relPath: string,
  mode?: "strict" | "relaxed",
): Promise<ExternalMatchResult | { error: string }> {
  const body: { rel_path: string; mode?: "strict" | "relaxed" } = {
    rel_path: relPath,
  };
  if (mode) body.mode = mode;
  const res = await fetch(`${basePath}/api/external/match/one`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) return errorFromResponse(res);
  return (await res.json()) as ExternalMatchResult;
}

/** Accept a manually chosen YTM candidate for a raw external track. */
export async function acceptExternalMatch(
  relPath: string,
  videoId: string,
  score?: number,
): Promise<ExternalMatchResult | { error: string }> {
  const res = await fetch(`${basePath}/api/external/match/accept`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      rel_path: relPath,
      video_id: videoId,
      score: score ?? null,
    }),
  });
  if (!res.ok) return errorFromResponse(res);
  return (await res.json()) as ExternalMatchResult;
}

/** Accept a Wanted-source hit as tags-verified (no YTM video_id). */
export async function acceptExternalMeta(body: {
  rel_path: string;
  source: string;
  source_id: string;
  title: string;
  artists: string;
  album?: string;
  source_url?: string | null;
  thumbnail_url?: string | null;
}): Promise<{ verified: boolean; meta_status: string } | { error: string }> {
  const res = await fetch(`${basePath}/api/external/meta/accept`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) return errorFromResponse(res);
  return (await res.json()) as { verified: boolean; meta_status: string };
}

export type ExternalTrackDeleteMode =
  | "keep_match"
  | "clear_match"
  | "delete_raw"
  | "move_to_direct"
  | "add_to_direct";

export async function deleteExternalTrack(
  dirName: string,
  relPath: string,
  mode: ExternalTrackDeleteMode,
): Promise<{ ok: boolean } | { error: string }> {
  const params = new URLSearchParams({ rel_path: relPath, mode });
  const res = await fetch(
    `${basePath}/api/external/playlists/${encodeURIComponent(dirName)}/tracks?${params}`,
    { method: "DELETE", credentials: "include" },
  );
  if (!res.ok) return errorFromResponse(res);
  const data = (await res.json()) as { ok?: boolean };
  return { ok: data.ok !== false };
}

/** Batch-match up to `limit` unmatched raw tracks (optionally in one DIR). */
export async function matchExternalPlaylist(
  dirName: string,
  limit = 50,
): Promise<ExternalMatchBatchResult | { error: string }> {
  const res = await fetch(`${basePath}/api/external/match/batch`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dir_name: dirName, limit }),
  });
  if (!res.ok) return errorFromResponse(res);
  return (await res.json()) as ExternalMatchBatchResult;
}
