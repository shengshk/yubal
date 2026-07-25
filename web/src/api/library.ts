import { basePath } from "@/lib/base-path";
import { sharedJsonGet } from "./shared-get";

export type LibraryTrackSummary = {
  effective_count: number;
  identified_count: number;
  unidentified_count: number;
  verified_count: number;
  unverified_count: number;
  physical_count: number;
  hardlink_duplicate_count: number;
};

export async function getLibraryTrackSummary(): Promise<LibraryTrackSummary | null> {
  const result = await sharedJsonGet<LibraryTrackSummary>(
    `${basePath}/api/library/track-summary`,
  );
  return result.ok ? result.data : null;
}

export type LibraryFolders = {
  items: string[];
  direct_folder: string;
  subscription_folders: string[];
  empty_folders: string[];
  shared_folders: Record<string, number>;
};

function errorMessage(body: { detail?: string; message?: string } | null) {
  return body?.detail ?? body?.message ?? "Request failed";
}

export async function listLibraryFolders(): Promise<LibraryFolders> {
  const res = await fetch(`${basePath}/api/library/folders`, {
    credentials: "include",
  });
  if (!res.ok) {
    return {
      items: [],
      direct_folder: "direct",
      subscription_folders: [],
      empty_folders: [],
      shared_folders: {},
    };
  }
  return (await res.json()) as LibraryFolders;
}

export async function createLibraryFolder(
  path: string,
): Promise<{ path: string } | { error: string }> {
  const res = await fetch(`${basePath}/api/library/folders`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      detail?: string;
      message?: string;
    } | null;
    return { error: errorMessage(body) };
  }
  return (await res.json()) as { path: string };
}

export async function renameLibraryFolder(
  path: string,
  newName: string,
): Promise<{ path: string } | { error: string }> {
  const res = await fetch(`${basePath}/api/library/folders`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, new_name: newName }),
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      detail?: string;
      message?: string;
    } | null;
    return { error: errorMessage(body) };
  }
  return (await res.json()) as { path: string };
}

export async function deleteLibraryFolder(
  path: string,
): Promise<{ ok: true } | { error: string }> {
  const res = await fetch(
    `${basePath}/api/library/folders?path=${encodeURIComponent(path)}`,
    {
      method: "DELETE",
      credentials: "include",
    },
  );
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      detail?: string;
      message?: string;
    } | null;
    return { error: errorMessage(body) };
  }
  return { ok: true };
}

export function trackCoverUrl(filePath: string): string {
  return `${basePath}/api/library/track-cover?path=${encodeURIComponent(filePath)}`;
}

export function playlistCoverUrl(folder: string): string {
  return `${basePath}/api/library/playlist-cover?folder=${encodeURIComponent(folder)}`;
}

/** Default wishlist cover: empty (no hardlinks) vs matched. */
export function wantedCoverUrl(hasMatched: boolean): string {
  return `${basePath}/api/library/wanted-cover?matched=${hasMatched ? "1" : "0"}`;
}

export function albumCoverUrl(filePath: string): string {
  return `${basePath}/api/library/album-cover?path=${encodeURIComponent(filePath)}`;
}

export type TrackLyrics = {
  available: boolean;
  content: string | null;
  source?: string | null;
};

export async function fetchTrackLyrics(filePath: string): Promise<TrackLyrics> {
  const res = await fetch(
    `${basePath}/api/library/track-lyrics?path=${encodeURIComponent(filePath)}`,
    { credentials: "include" },
  );
  if (!res.ok) {
    return { available: false, content: null };
  }
  return (await res.json()) as TrackLyrics;
}

export type SaveTrackLyricsResult = {
  ok: boolean;
  sidecar: boolean;
  embedded: boolean;
  catalog: boolean;
  errors: string[];
};

export async function saveTrackLyrics(
  filePath: string,
  content: string,
): Promise<SaveTrackLyricsResult | { error: string }> {
  const res = await fetch(`${basePath}/api/library/track-lyrics`, {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path: filePath, content }),
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      detail?: string;
      message?: string;
    } | null;
    return { error: errorMessage(body) };
  }
  return (await res.json()) as SaveTrackLyricsResult;
}

export type TrackTagUpdate = {
  title?: string;
  artist?: string;
  artists?: string[];
  album_artist?: string;
  album_artists?: string[];
  album?: string;
  year?: string | null;
  track_number?: number | null;
  cover_url?: string | null;
  refresh_cover?: boolean;
  lyrics?: string | null;
};

export type TrackTagUpdateResponse = {
  video_id: string;
  title: string;
  artist: string;
  album_artist: string;
  album: string;
  year?: string | null;
  track_number?: number | null;
  cover_url?: string | null;
  lyrics_applied?: boolean;
  cover_applied?: boolean;
  locations: Array<{
    save_folder: string;
    old_relative_path: string;
    new_relative_path: string;
  }>;
  warnings?: string[];
};

export async function updateTrackTags(
  videoId: string,
  update: TrackTagUpdate,
): Promise<TrackTagUpdateResponse | null> {
  const res = await fetch(
    `${basePath}/api/library/tracks/${encodeURIComponent(videoId)}`,
    {
      method: "PATCH",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(update),
    },
  );
  if (!res.ok) return null;
  return (await res.json()) as TrackTagUpdateResponse;
}

export type MetadataCandidate = {
  rank: number;
  candidate_video_id: string;
  title: string;
  artist: string;
  album: string | null;
  album_id: string | null;
  thumbnail_url: string | null;
  duration_seconds: number | null;
  title_score: number | null;
  artist_score: number | null;
};

export type MetadataSearchResponse = {
  query: string;
  default_query: string;
  candidates: MetadataCandidate[];
};

export type MetadataSuggestion = {
  candidate_video_id: string;
  title: string;
  artist: string;
  album_artist: string;
  album: string;
  year: string | null;
  track_number: number | null;
  total_tracks: number | null;
  cover_url: string | null;
  lyrics: string | null;
  lyrics_source: string | null;
  match_result: string;
  source: string;
};

export async function searchTrackMetadata(
  videoId: string,
  query?: string | null,
): Promise<MetadataSearchResponse | { error: string }> {
  const res = await fetch(
    `${basePath}/api/library/tracks/${encodeURIComponent(videoId)}/metadata/search`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query: query?.trim() ? query.trim() : null }),
    },
  );
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      detail?: string;
      message?: string;
    } | null;
    return { error: errorMessage(body) };
  }
  return (await res.json()) as MetadataSearchResponse;
}

export async function resolveTrackMetadata(
  videoId: string,
  candidateVideoId: string,
  fetchLyrics = true,
): Promise<MetadataSuggestion | { error: string }> {
  const res = await fetch(
    `${basePath}/api/library/tracks/${encodeURIComponent(videoId)}/metadata/resolve`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        candidate_video_id: candidateVideoId,
        fetch_lyrics: fetchLyrics,
      }),
    },
  );
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      detail?: string;
      message?: string;
    } | null;
    return { error: errorMessage(body) };
  }
  return (await res.json()) as MetadataSuggestion;
}
