import { basePath } from "@/lib/base-path";
import { sharedJsonGet } from "./shared-get";

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

export type LibraryAudit = {
  ok: boolean;
  physical_count: number;
  hardlink_duplicate_count: number;
  catalog_location_count: number;
  missing_catalog_locations: number;
  repaired_catalog_locations: number;
  repaired_index_entries: number;
  untracked_physical_count: number;
};

export async function getLibraryHealth(): Promise<LibraryHealth | null> {
  const result = await sharedJsonGet<LibraryHealth>(
    `${basePath}/api/library/health`,
  );
  return result.ok ? result.data : null;
}

export function isLibraryHealthy(health: LibraryHealth | null): boolean {
  return health?.status === "healthy";
}

export async function auditLibrary(
  repair = false,
): Promise<LibraryAudit | { error: string }> {
  const res = await fetch(
    `${basePath}/api/library/audit?repair=${repair ? "true" : "false"}`,
    {
      method: "POST",
      credentials: "include",
    },
  );
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      detail?: string;
      message?: string;
    } | null;
    return { error: body?.detail ?? body?.message ?? "Library audit failed" };
  }
  return (await res.json()) as LibraryAudit;
}
