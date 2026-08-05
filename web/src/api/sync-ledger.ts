import { basePath } from "@/lib/base-path";
import { sharedJsonGet } from "./shared-get";

export type SyncLedgerEntry = {
  id: string;
  key: string;
  kind: "subscription" | "direct";
  subscription_id: string | null;
  save_folder: string;
  title: string;
  thumbnail_url: string | null;
  content_kind: string;
  url: string | null;
  total_count: number;
  synced_count: number;
  real_download_count: number;
  hardlink_count: number;
  failed_count: number;
  skipped_ugc: number;
  skipped_region: number;
  skipped_other: number;
  last_job_id: string | null;
  last_job_status: string | null;
  last_synced_at: string | null;
  updated_at: string;
  enabled?: boolean | null;
  max_items?: number | null;
  sync_jitter_seconds?: number | null;
  offline_marking_enabled?: boolean | null;
  offline_cleanup_enabled?: boolean | null;
  offline_cleanup_action?: "delete" | "archive" | "to_wanted" | null;
  offline_cleanup_delay_hours?: number | null;
  offline_count?: number | null;
  id_invalid_count?: number | null;
  blocked_count?: number | null;
  missing_count?: number | null;
  cover_track_path?: string | null;
};

export type DirectPolicyUpdates = {
  enabled?: boolean;
  max_items?: number;
  sync_jitter_seconds?: number;
  offline_marking_enabled?: boolean;
  offline_cleanup_enabled?: boolean;
  offline_cleanup_action?: "delete" | "archive" | "to_wanted";
  offline_cleanup_delay_hours?: number;
};

export type DirectDeleteMode =
  | "keep_list"
  | "wipe_list"
  | "block"
  | "migrate_to_external"
  | "migrate_to_wanted"
  | "clear_offline_delete"
  | "clear_offline_to_raw_delete";

export type DirectPlaylistDeleteMode =
  | "keep_list"
  | "wipe_list"
  | "clear_offline_delete"
  | "clear_offline_to_raw_delete"
  | "clear_offline_to_wanted"
  | "migrate_to_external";

export async function listSyncLedger(): Promise<SyncLedgerEntry[]> {
  const result = await sharedJsonGet<{ items: SyncLedgerEntry[] }>(
    `${basePath}/api/sync-ledger`,
  );
  return result.ok ? (result.data?.items ?? []) : [];
}

export async function updateDirect(
  updates: DirectPolicyUpdates,
): Promise<"ok" | "folder_conflict" | "error"> {
  const res = await fetch(`${basePath}/api/sync-ledger/direct`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (res.status === 409) return "folder_conflict";
  if (!res.ok) return "error";
  return "ok";
}

export async function deleteDirect(
  confirm = false,
  mode: DirectPlaylistDeleteMode = "wipe_list",
): Promise<boolean> {
  const res = await fetch(
    `${basePath}/api/sync-ledger/direct?confirm=${confirm ? "true" : "false"}&mode=${mode}`,
    {
      method: "DELETE",
      credentials: "include",
    },
  );
  return res.ok;
}

export async function reconcileDirect(): Promise<SyncLedgerEntry | null> {
  const res = await fetch(`${basePath}/api/sync-ledger/direct/reconcile`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) return null;
  return (await res.json()) as SyncLedgerEntry;
}

export async function syncDirect(): Promise<string | null> {
  const res = await fetch(`${basePath}/api/sync-ledger/direct/sync`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) return null;
  const data = (await res.json()) as { job_id?: string };
  return data.job_id ?? null;
}

export type SyncTrackItem = {
  index: number;
  title: string;
  artist: string | null;
  album_artist?: string | null;
  display_label?: string | null;
  exists: boolean;
  storage: "real" | "hardlink" | "missing" | string;
  relative_path: string;
  video_id?: string | null;
  cover_url?: string | null;
  album?: string | null;
  year?: string | null;
  track_number?: number | null;
  /** "raw" = unmatched external-library file with no video_id. */
  tier?: "draft" | "complete" | "premium" | "raw" | null;
  has_embedded_cover?: boolean;
  has_lyrics?: boolean;
  has_synced_lyrics?: boolean;
  cover_source?: "apple" | "ytm" | "manual" | "embedded" | string | null;
  membership_status?:
    | "active"
    | "offline"
    | "id_invalid"
    | "blocked"
    | string
    | null;
  /** External unmatched: critical tags (title+artist+album) present. */
  tags_complete?: boolean;
  /** External: Wanted-source meta verification status. */
  meta_status?: "pending" | "verified" | "rejected" | string | null;
  meta_source?: string | null;
  meta_source_id?: string | null;
  meta_source_url?: string | null;
  /** External: junk row (readonly incomplete / rejected). */
  is_junk?: boolean;
  /** External junk grade: writable (rw) or readonly (ro). */
  junk_kind?: "rw" | "ro" | null;
  /** External matched track already has a Direct catalog location. */
  in_direct?: boolean;
  /** External raw permission resolved from its immutable original source. */
  can_mutate?: boolean;
  /** Wanted rows carry their wishlist id and origin link. */
  wanted_id?: string | null;
  source_url?: string | null;
};

export type EnrichmentSummary = {
  scanned: number;
  enriched: number;
  upgraded: number;
  failed: number;
  skipped_premium: number;
  already_running: boolean;
};

export async function enrichLibrary(
  budget = 100,
): Promise<EnrichmentSummary | null> {
  const res = await fetch(
    `${basePath}/api/sync-ledger/enrich?budget=${budget}`,
    { method: "POST", credentials: "include" },
  );
  if (!res.ok) return null;
  return (await res.json()) as EnrichmentSummary;
}

export async function enrichTrack(
  videoId: string,
): Promise<EnrichmentSummary | null> {
  const res = await fetch(
    `${basePath}/api/sync-ledger/enrich/${encodeURIComponent(videoId)}`,
    { method: "POST", credentials: "include" },
  );
  if (!res.ok) return null;
  return (await res.json()) as EnrichmentSummary;
}

export async function listSyncTracks(
  saveFolder: string,
): Promise<SyncTrackItem[]> {
  const result = await sharedJsonGet<{ items?: SyncTrackItem[] }>(
    `${basePath}/api/sync-ledger/tracks?save_folder=${encodeURIComponent(saveFolder)}`,
  );
  return result.ok ? (result.data?.items ?? []) : [];
}

export async function deleteDirectTrack(
  relativePath: string,
  mode: DirectDeleteMode = "keep_list",
): Promise<SyncLedgerEntry | null> {
  const res = await fetch(
    `${basePath}/api/sync-ledger/direct/track?relative_path=${encodeURIComponent(relativePath)}&mode=${mode}`,
    { method: "DELETE", credentials: "include" },
  );
  if (!res.ok) return null;
  return (await res.json()) as SyncLedgerEntry;
}

export async function unblockDirectTrack(
  videoId: string,
): Promise<SyncLedgerEntry | null> {
  const res = await fetch(
    `${basePath}/api/sync-ledger/direct/tracks/${encodeURIComponent(videoId)}/unblock`,
    { method: "POST", credentials: "include" },
  );
  if (!res.ok) return null;
  return (await res.json()) as SyncLedgerEntry;
}

export async function removeDirectTrackFromList(
  videoId: string,
): Promise<SyncLedgerEntry | null> {
  const res = await fetch(
    `${basePath}/api/sync-ledger/direct/tracks/${encodeURIComponent(videoId)}/remove-from-list`,
    { method: "POST", credentials: "include" },
  );
  if (!res.ok) return null;
  return (await res.json()) as SyncLedgerEntry;
}
