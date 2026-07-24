import { basePath } from "@/lib/base-path";

export type SearchTrack = {
  rank: number;
  video_id: string;
  title: string;
  artist: string;
  album: string | null;
  thumbnail_url: string | null;
  duration_seconds: number | null;
  matched: boolean;
  local_path: string | null;
  preview_cached: boolean;
};

export type SearchSnapshot = {
  query: string;
  searched_at: string;
  expires_at: string;
  total_count: number;
  matched_count: number;
  cached_count: number;
  tracks: SearchTrack[];
};

type ApiResult<T> = { data: T } | { error: string };

async function errorText(response: Response, fallback: string): Promise<string> {
  const body = (await response.json().catch(() => null)) as {
    detail?: string;
    message?: string;
  } | null;
  return body?.detail ?? body?.message ?? fallback;
}

export async function getSearchResults(): Promise<SearchSnapshot | null> {
  const response = await fetch(`${basePath}/api/search`, {
    credentials: "include",
  });
  if (!response.ok) return null;
  return (await response.json()) as SearchSnapshot | null;
}

export async function searchSongs(
  query: string,
): Promise<ApiResult<SearchSnapshot>> {
  const response = await fetch(`${basePath}/api/search`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
  });
  if (!response.ok) {
    return { error: await errorText(response, "Search failed") };
  }
  return { data: (await response.json()) as SearchSnapshot };
}

export async function deleteSearchResults(): Promise<boolean> {
  const response = await fetch(`${basePath}/api/search`, {
    method: "DELETE",
    credentials: "include",
  });
  return response.ok;
}

export async function prepareSearchPreview(
  videoId: string,
): Promise<ApiResult<{ video_id: string; url: string }>> {
  const response = await fetch(
    `${basePath}/api/search/preview/${encodeURIComponent(videoId)}`,
    {
      method: "POST",
      credentials: "include",
    },
  );
  if (!response.ok) {
    return { error: await errorText(response, "Preview failed") };
  }
  return {
    data: (await response.json()) as { video_id: string; url: string },
  };
}

export function searchPreviewUrl(videoId: string): string {
  return `${basePath}/api/search/preview/${encodeURIComponent(videoId)}/file`;
}

export type SearchLyrics = {
  available: boolean;
  content: string | null;
  source?: string | null;
};

export async function fetchSearchLyrics(
  videoId: string,
): Promise<SearchLyrics> {
  const res = await fetch(
    `${basePath}/api/search/lyrics/${encodeURIComponent(videoId)}`,
    { credentials: "include" },
  );
  if (!res.ok) return { available: false, content: null };
  return (await res.json()) as SearchLyrics;
}

export async function importSearchPreview(
  videoId: string,
): Promise<ApiResult<{ video_id: string; local_path: string; snapshot: SearchSnapshot }>> {
  const response = await fetch(
    `${basePath}/api/search/download/${encodeURIComponent(videoId)}`,
    {
      method: "POST",
      credentials: "include",
    },
  );
  if (!response.ok) {
    return { error: await errorText(response, "Import failed") };
  }
  return {
    data: (await response.json()) as {
      video_id: string;
      local_path: string;
      snapshot: SearchSnapshot;
    },
  };
}
