import { basePath } from "@/lib/base-path";
import { sharedJsonGet } from "./shared-get";

/** Pseudo save-folder for the wishlist; the stream API keys files by prefix. */
export const WANTED_FOLDER = "wanted";

export type WantedTrackDeleteMode = "remove" | "wipe_list" | "to_raw_delete";

export type WantedPlaylistDeleteMode = "wipe_list" | "to_raw_delete";

export type WantedSummary = {
  total_count: number;
  local_heart_count: number;
  recovery_count: number;
  matched_file_count: number;
  unmatched_count: number;
  exclusive_count: number;
  shared_count: number;
  hardlink_count: number;
  enabled: boolean;
  auto_match_enabled: boolean;
  last_matched_at?: string | null;
  last_job_status?: string | null;
};

export type WantedTrack = {
  id: string;
  display_index: string | null;
  title: string;
  artists: string;
  album: string | null;
  source: string;
  source_id: string | null;
  source_url: string | null;
  thumbnail_url: string | null;
  duration_seconds: number | null;
  relative_path: string | null;
  has_file: boolean;
  video_id: string | null;
  created_at: string;
  updated_at: string;
};

export type WantedAddRequest = {
  title: string;
  artists: string;
  album?: string;
  source: string;
  source_id: string;
  source_url?: string | null;
  thumbnail_url?: string | null;
  duration_seconds?: number | null;
};

async function errorText(
  response: Response,
  fallback: string,
): Promise<string> {
  const body = (await response.json().catch(() => null)) as {
    detail?: string;
    message?: string;
  } | null;
  return body?.detail ?? body?.message ?? fallback;
}

export async function getWantedSummary(): Promise<WantedSummary | null> {
  const result = await sharedJsonGet<WantedSummary>(
    `${basePath}/api/wanted/summary`,
  );
  return result.ok ? result.data : null;
}

export async function listWantedTracks(): Promise<WantedTrack[]> {
  const result = await sharedJsonGet<WantedTrack[]>(
    `${basePath}/api/wanted/tracks`,
  );
  return result.ok ? (result.data ?? []) : [];
}

export async function addWantedTrack(
  body: WantedAddRequest,
): Promise<{ data: WantedTrack } | { error: string }> {
  const res = await fetch(`${basePath}/api/wanted/tracks`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    return { error: await errorText(res, "Failed to add to wanted") };
  }
  return { data: (await res.json()) as WantedTrack };
}

export async function deleteWantedTrack(
  trackId: string,
  mode: WantedTrackDeleteMode,
): Promise<boolean> {
  const res = await fetch(
    `${basePath}/api/wanted/tracks/${encodeURIComponent(trackId)}/delete`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    },
  );
  return res.ok;
}

export async function deleteWantedPlaylist(
  mode: WantedPlaylistDeleteMode,
): Promise<{ removed: number } | { error: string }> {
  const res = await fetch(`${basePath}/api/wanted/delete`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode }),
  });
  if (!res.ok) {
    return { error: await errorText(res, "Failed to clear wanted") };
  }
  return (await res.json()) as { removed: number };
}

export async function matchWantedLocal(): Promise<
  { linked: number } | { error: string }
> {
  const res = await fetch(`${basePath}/api/wanted/match/local`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    return { error: await errorText(res, "Local match failed") };
  }
  return (await res.json()) as { linked: number };
}

export async function syncWanted(): Promise<
  { linked: number; matched?: number; failed?: number } | { error: string }
> {
  const res = await fetch(`${basePath}/api/wanted/sync`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    return { error: await errorText(res, "Wanted sync failed") };
  }
  return (await res.json()) as {
    linked: number;
    matched?: number;
    failed?: number;
  };
}

export async function matchWantedYtm(): Promise<
  { data: Record<string, unknown> } | { error: string }
> {
  const res = await fetch(`${basePath}/api/wanted/match/ytm`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    return { error: await errorText(res, "YTM match failed") };
  }
  return { data: (await res.json()) as Record<string, unknown> };
}

export async function matchWantedTrackYtm(
  trackId: string,
): Promise<{ data: Record<string, unknown> } | { error: string }> {
  const res = await fetch(
    `${basePath}/api/wanted/tracks/${encodeURIComponent(trackId)}/match-ytm`,
    { method: "POST", credentials: "include" },
  );
  if (!res.ok) {
    return { error: await errorText(res, "YTM match failed") };
  }
  return { data: (await res.json()) as Record<string, unknown> };
}

export async function linkWantedTrackLocal(
  trackId: string,
): Promise<{ data: WantedTrack } | { error: string }> {
  const res = await fetch(
    `${basePath}/api/wanted/tracks/${encodeURIComponent(trackId)}/link-local`,
    { method: "POST", credentials: "include" },
  );
  if (!res.ok) {
    return { error: await errorText(res, "Local link failed") };
  }
  return { data: (await res.json()) as WantedTrack };
}
