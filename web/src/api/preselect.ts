import { basePath } from "@/lib/base-path";

export type PreselectStatus = {
  running: boolean;
  phase: string;
  scanned: number;
  added: number;
  updated: number;
  removed: number;
  errors: number;
  total_indexed: number;
  last_error: string | null;
  finished_at: string | null;
  hardlink_ok: boolean | null;
};

export async function getPreselectStatus(): Promise<PreselectStatus> {
  const res = await fetch(`${basePath}/api/preselect/status`, {
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error("Failed to load preselect status");
  }
  return (await res.json()) as PreselectStatus;
}

export async function scanPreselect(forceAll = false): Promise<PreselectStatus> {
  const res = await fetch(`${basePath}/api/preselect/scan`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ force_all: forceAll }),
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      detail?: string;
      message?: string;
    } | null;
    throw new Error(body?.detail ?? body?.message ?? "Scan failed");
  }
  return (await res.json()) as PreselectStatus;
}

/** Run scan; optionally poll status while the request is in flight. */
export async function scanPreselectUntilDone(
  forceAll = false,
  onProgress?: (status: PreselectStatus) => void,
): Promise<PreselectStatus> {
  let stopped = false;
  const poll = window.setInterval(() => {
    if (stopped) return;
    void getPreselectStatus()
      .then((st) => onProgress?.(st))
      .catch(() => undefined);
  }, 400);
  try {
    const result = await scanPreselect(forceAll);
    onProgress?.(result);
    return result;
  } finally {
    stopped = true;
    window.clearInterval(poll);
  }
}

export type WashResult = {
  checked: number;
  upgraded: number;
  skipped: number;
  errors: number;
};

export async function runWash(): Promise<WashResult> {
  const res = await fetch(`${basePath}/api/preselect/wash`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const body = (await res.json().catch(() => null)) as {
      detail?: string;
      message?: string;
    } | null;
    throw new Error(body?.detail ?? body?.message ?? "Wash failed");
  }
  return (await res.json()) as WashResult;
}
