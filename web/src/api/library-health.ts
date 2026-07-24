import { basePath } from "@/lib/base-path";

export type LibraryHealthStatus = "healthy" | "fs_mismatch" | "mount_suspect";

export type LibraryHealth = {
  status: LibraryHealthStatus;
  reason: string | null;
  same_filesystem: boolean;
  download_sentinel_ok: boolean;
  external_sentinel_ok: boolean;
  last_good_raw_count: number;
  last_check_at: string | null;
};

export async function getLibraryHealth(): Promise<LibraryHealth | null> {
  const res = await fetch(`${basePath}/api/library/health`, {
    credentials: "include",
  });
  if (!res.ok) return null;
  return (await res.json()) as LibraryHealth;
}

export function isLibraryHealthy(health: LibraryHealth | null): boolean {
  return health?.status === "healthy";
}
