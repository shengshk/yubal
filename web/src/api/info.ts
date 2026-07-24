import { basePath } from "@/lib/base-path";

export type ContentInfo = {
  title: string;
  artist: string;
  year: number | null;
  track_count: number | null;
  playlist_id: string;
  url: string | null;
  thumbnail_url: string | null;
  kind: "track" | "album" | "playlist" | string;
};

export async function getContentInfo(
  url: string,
): Promise<ContentInfo | { error: string }> {
  const response = await fetch(
    `${basePath}/api/info?url=${encodeURIComponent(url)}`,
    { credentials: "include" },
  );
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      detail?: string;
      message?: string;
    } | null;
    return {
      error:
        body?.detail ??
        body?.message ??
        `Unable to inspect URL (${response.status})`,
    };
  }
  return (await response.json()) as ContentInfo;
}
