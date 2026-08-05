import { basePath } from "@/lib/base-path";
import { sharedJsonGet } from "./shared-get";

export type AudioFormat = "opus" | "mp3" | "m4a";
export type TrackSortKey = "title" | "artist" | "album";
export type MatchStrictness = "strict" | "relaxed";
export type ExternalNewPlaylistMode = "pending" | "readonly" | "managed";
export type FactoryResetMode = "preferences" | "invalid" | "full";

export type FactoryResetPreview = {
  mode: FactoryResetMode;
  token: string;
  expires_in_seconds: number;
  list_entries: number;
  files: number;
  paths: number;
  bytes: number;
  backups: number;
  clears_account: boolean;
  clears_external_originals: boolean;
};

export type FactoryResetResult = {
  mode: FactoryResetMode;
  list_entries: number;
  files: number;
  paths: number;
  bytes: number;
  backups: number;
  requires_setup: boolean;
};

export type AppSettings = {
  min_free_gb: number;
  direct_download_limit: number;
  index_threshold: number;
  track_sort_key: TrackSortKey;
  search_result_ttl_hours: number;
  audio_format: AudioFormat;
  audio_quality: number;
  fetch_lyrics: boolean;
  ytmusic_lyrics_fallback: boolean;
  qq_lyrics_fallback: boolean;
  scrape_cooldown_hours: number;
  download_ugc: boolean;
  replaygain: boolean;
  scheduler_enabled: boolean;
  scheduler_cron: string;
  external_inventory_schedule_enabled: boolean;
  external_inventory_schedule_time: string;
  job_timeout_seconds: number;
  external_library_enabled: boolean;
  external_new_playlist_mode: ExternalNewPlaylistMode;
  preselect_enabled: boolean;
  wash_enabled: boolean;
  preselect_root: string;
  preselect_place_mode: "link" | "copy";
  preselect_match_mode: "loose" | "standard" | "strict";
  preselect_hardlink_ok: boolean | null;
  preselect_indexed: number;
  preselect_placed: number;
  /** Days to back off re-matching a raw external file after a failed attempt. */
  match_backoff_cap_days: number;
  /** External YTM match: strict rejects Live/DJ↔studio; relaxed allows. */
  match_strictness: MatchStrictness;
  /** Min cover edge (px) for permanent premium; 0 = shelf-life only. */
  cover_excellence_px: number;
  /** Days a probe-only cover comparison stays fresh (default 7). */
  cover_probe_fresh_days: number;
  /** Days a full-download cover comparison stays fresh (default 30). */
  cover_download_fresh_days: number;
  download_cache_enabled: boolean;
  cache_path: string;
  cache_min_free_gb: number;
  cache_free_bytes: number;
  cache_free_gb: number;
  cache_available: boolean;
  data_path: string;
  free_bytes: number;
  free_gb: number;
  enough_space: boolean;
  maintenance_locked: boolean;
  telegram_bot_token: string;
  telegram_admin_ids: string;
  telegram_user_ids: string;
  telegram_daily_limit: number;
  telegram_api_url: string;
  telegram_bot_running: boolean;
  wanted_enabled: boolean;
  wanted_auto_match_enabled: boolean;
  wanted_max_items: number;
  wanted_sync_jitter_seconds: number;
  wanted_source_musicbrainz: boolean;
  wanted_source_qq: boolean;
  wanted_source_discogs: boolean;
  wanted_source_lastfm: boolean;
  lastfm_api_key: string;
};

export type SettingsUpdate = Partial<{
  min_free_gb: number;
  direct_download_limit: number;
  index_threshold: number;
  track_sort_key: TrackSortKey;
  search_result_ttl_hours: number;
  audio_format: AudioFormat;
  audio_quality: number;
  fetch_lyrics: boolean;
  ytmusic_lyrics_fallback: boolean;
  qq_lyrics_fallback: boolean;
  scrape_cooldown_hours: number;
  download_ugc: boolean;
  replaygain: boolean;
  scheduler_enabled: boolean;
  scheduler_cron: string;
  external_inventory_schedule_enabled: boolean;
  external_inventory_schedule_time: string;
  job_timeout_seconds: number;
  external_library_enabled: boolean;
  external_new_playlist_mode: ExternalNewPlaylistMode;
  preselect_enabled: boolean;
  wash_enabled: boolean;
  preselect_place_mode: "link" | "copy";
  preselect_match_mode: "loose" | "standard" | "strict";
  match_backoff_cap_days: number;
  match_strictness: MatchStrictness;
  cover_excellence_px: number;
  cover_probe_fresh_days: number;
  cover_download_fresh_days: number;
  download_cache_enabled: boolean;
  cache_min_free_gb: number;
  telegram_bot_token: string;
  telegram_admin_ids: string;
  telegram_user_ids: string;
  telegram_daily_limit: number;
  wanted_enabled: boolean;
  wanted_auto_match_enabled: boolean;
  wanted_max_items: number;
  wanted_sync_jitter_seconds: number;
  wanted_source_musicbrainz: boolean;
  wanted_source_qq: boolean;
  wanted_source_discogs: boolean;
  wanted_source_lastfm: boolean;
  lastfm_api_key: string;
}>;

function errorFromBody(
  body: {
    message?: string;
    error?: string;
    detail?: string | { msg?: string }[];
  } | null,
): { error: string; code?: string } {
  if (!body) return { error: "Failed to update settings" };
  if (typeof body.detail === "string") {
    return { error: body.detail, code: body.error };
  }
  if (Array.isArray(body.detail) && body.detail[0]?.msg) {
    return { error: body.detail[0].msg, code: body.error };
  }
  return {
    error: body.message ?? "Failed to update settings",
    code: body.error,
  };
}

export async function getSettings(): Promise<AppSettings | null> {
  const result = await sharedJsonGet<AppSettings>(`${basePath}/api/settings`);
  return result.ok ? result.data : null;
}

export async function updateSettings(
  updates: SettingsUpdate,
): Promise<AppSettings | { error: string; code?: string }> {
  const res = await fetch(`${basePath}/api/settings`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      message?: string;
      error?: string;
      detail?: string | { msg?: string }[];
    } | null;
    return errorFromBody(body);
  }
  return (await res.json()) as AppSettings;
}

export async function resetSettings(): Promise<
  AppSettings | { error: string }
> {
  const res = await fetch(`${basePath}/api/settings/reset`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      message?: string;
      detail?: string;
    } | null;
    return {
      error: body?.message ?? body?.detail ?? "Failed to reset settings",
    };
  }
  return (await res.json()) as AppSettings;
}

export async function previewFactoryReset(
  mode: FactoryResetMode,
): Promise<FactoryResetPreview | { error: string }> {
  const res = await fetch(`${basePath}/api/settings/factory/preview`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      message?: string;
      detail?: string;
    } | null;
    return {
      error: body?.message ?? body?.detail ?? "Failed to preview reset",
    };
  }
  return (await res.json()) as FactoryResetPreview;
}

export async function executeFactoryReset(
  preview: FactoryResetPreview,
  password = "",
): Promise<FactoryResetResult | { error: string }> {
  const res = await fetch(`${basePath}/api/settings/factory/execute`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      mode: preview.mode,
      token: preview.token,
      password,
    }),
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      message?: string;
      detail?: string;
    } | null;
    return {
      error: body?.message ?? body?.detail ?? "Failed to execute reset",
    };
  }
  return (await res.json()) as FactoryResetResult;
}

export async function clearScrapeCooldowns(): Promise<
  { cleared: number } | { error: string }
> {
  const res = await fetch(`${basePath}/api/settings/clear-scrape-cooldowns`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      message?: string;
      detail?: string;
    } | null;
    return {
      error:
        body?.message ?? body?.detail ?? "Failed to clear scrape cooldowns",
    };
  }
  return (await res.json()) as { cleared: number };
}

export async function clearMatchCooldowns(
  includeRejected = false,
): Promise<{ cleared: number } | { error: string }> {
  const params = new URLSearchParams({
    include_rejected: includeRejected ? "true" : "false",
  });
  const res = await fetch(
    `${basePath}/api/settings/clear-match-cooldowns?${params}`,
    { method: "POST", credentials: "include" },
  );
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      message?: string;
      detail?: string;
    } | null;
    return {
      error: body?.message ?? body?.detail ?? "Failed to clear match cooldowns",
    };
  }
  return (await res.json()) as { cleared: number };
}

export type ReclaimPitTarget = "delete" | "default" | "both";

export type ReclaimPitsResult = {
  deleted_files: number;
  deleted_raw: number;
  deleted_locations: number;
  errors: number;
};

export async function reclaimPits(
  target: ReclaimPitTarget,
): Promise<ReclaimPitsResult | { error: string }> {
  const params = new URLSearchParams({ target });
  const res = await fetch(`${basePath}/api/settings/reclaim-pits?${params}`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      message?: string;
      detail?: string;
    } | null;
    return {
      error: body?.message ?? body?.detail ?? "Failed to reclaim salvage pits",
    };
  }
  return (await res.json()) as ReclaimPitsResult;
}
