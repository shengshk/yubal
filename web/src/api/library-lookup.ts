import { basePath } from "@/lib/base-path";

export type LibraryLocationHit = {
  kind: "subscription" | "external";
  expand_key: string;
  title: string;
  enabled: boolean | null;
};

export type TrackPresence = {
  video_id: string;
  title: string | null;
  artist: string | null;
  in_direct: boolean;
  locations: LibraryLocationHit[];
};

export type PlaylistPresence = {
  url: string;
  subscription: LibraryLocationHit | null;
  in_direct_url: boolean;
};

export type TextMatchHit = {
  video_id: string;
  title: string;
  artist: string;
  in_direct: boolean;
  locations: LibraryLocationHit[];
};

export type TextPresence = {
  query: string;
  matches: TextMatchHit[];
};

async function lookupJson<T>(
  path: string,
): Promise<T | { error: string }> {
  const response = await fetch(`${basePath}/api${path}`, {
    credentials: "include",
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
      message?: string;
    } | null;
    return {
      error:
        body?.detail ??
        body?.message ??
        `Lookup failed (${response.status})`,
    };
  }
  return (await response.json()) as T;
}

export function lookupTrackPresence(
  videoId: string,
): Promise<TrackPresence | { error: string }> {
  return lookupJson(
    `/library/lookup/track?video_id=${encodeURIComponent(videoId)}`,
  );
}

export function lookupPlaylistPresence(
  url: string,
): Promise<PlaylistPresence | { error: string }> {
  return lookupJson(
    `/library/lookup/playlist?url=${encodeURIComponent(url)}`,
  );
}

export function lookupTextPresence(
  query: string,
): Promise<TextPresence | { error: string }> {
  return lookupJson(
    `/library/lookup/text?q=${encodeURIComponent(query)}`,
  );
}
