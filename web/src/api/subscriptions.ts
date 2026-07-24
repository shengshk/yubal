import { basePath } from "@/lib/base-path";
import { api } from "./client";
import { errorMessage } from "./errors";
import type { components } from "./schema";

export type Subscription = components["schemas"]["SubscriptionResponse"];
export type SchedulerStatus = components["schemas"]["SchedulerStatus"];
export type SyncMode = "incremental" | "mirror";
export type OfflineCleanupAction = "delete" | "archive";
export type MembershipStatus = "active" | "offline" | "blocked";
export type SubscriptionDeleteAction =
  | "keep"
  | "keep_list"
  | "delete"
  | "move_to_direct";

export type SubscriptionTrack = {
  id: string;
  subscription_id: string;
  video_id: string;
  catalog_video_id: string;
  title: string;
  artist: string;
  album_artist: string;
  position: number | null;
  membership_status: MembershipStatus;
  first_seen_at: string;
  last_seen_at: string;
  missing_since: string | null;
  updated_at: string;
};

export type SubscriptionUpdates = {
  enabled?: boolean;
  save_folder?: string;
  max_items?: number | null;
  sync_jitter_seconds?: number;
  sync_mode?: SyncMode;
  offline_marking_enabled?: boolean;
  offline_cleanup_enabled?: boolean;
  offline_cleanup_action?: OfflineCleanupAction;
  offline_cleanup_delay_hours?: number;
  confirm_folder_move?: boolean;
};

type AddSubscriptionResult =
  | { success: true; id: string }
  | { success: false; error: string };

type SyncResult =
  | { success: true; jobIds: string[] }
  | { success: false; error: string };

export type UpdateSubscriptionResult =
  | { success: true; subscription: Subscription }
  | { success: false; error: string; folderConflict?: boolean };

// --- Subscriptions ---

export async function listSubscriptions(): Promise<Subscription[]> {
  const { data, error } = await api.GET("/subscriptions");
  if (error) return [];
  return data.items;
}

export async function addSubscription(
  url: string,
  maxItems?: number,
): Promise<AddSubscriptionResult> {
  const { data, error, response } = await api.POST("/subscriptions", {
    body: { url, max_items: maxItems },
  });

  if (error) {
    if (response.status === 409) {
      return { success: false, error: "Subscription already exists" };
    }
    return {
      success: false,
      error: errorMessage(error, "Failed to add subscription"),
    };
  }

  return { success: true, id: data.id };
}

export async function updateSubscription(
  id: string,
  updates: SubscriptionUpdates,
): Promise<UpdateSubscriptionResult> {
  const { data, error, response } = await api.PATCH(
    "/subscriptions/{subscription_id}",
    {
      params: { path: { subscription_id: id } },
      body: updates,
    },
  );
  if (error) {
    const errObj = error as { error?: string; message?: string };
    const folderConflict =
      response.status === 409 && errObj.error === "folder_conflict";
    return {
      success: false,
      error: errorMessage(error, "Failed to update subscription"),
      folderConflict,
    };
  }
  return { success: true, subscription: data };
}

export async function deleteSubscription(
  id: string,
  fileAction: SubscriptionDeleteAction = "keep",
): Promise<boolean> {
  const res = await fetch(
    `${basePath}/api/subscriptions/${id}?file_action=${encodeURIComponent(fileAction)}`,
    { method: "DELETE", credentials: "include" },
  );
  return res.ok;
}

export async function clearSubscriptionOffline(
  id: string,
  mode: "delete" | "to_raw_delete" = "delete",
): Promise<{ cleared: number; moved: number; errors: number } | null> {
  const res = await fetch(
    `${basePath}/api/subscriptions/${id}/clear-offline?mode=${mode}`,
    { method: "POST", credentials: "include" },
  );
  if (!res.ok) return null;
  return (await res.json()) as {
    cleared: number;
    moved: number;
    errors: number;
  };
}

export async function listSubscriptionTracks(
  id: string,
  status?: MembershipStatus,
): Promise<SubscriptionTrack[]> {
  const query = status
    ? `?status=${encodeURIComponent(status)}`
    : "";
  const res = await fetch(
    `${basePath}/api/subscriptions/${id}/tracks${query}`,
    { credentials: "include" },
  );
  if (!res.ok) return [];
  const data = (await res.json()) as { items: SubscriptionTrack[] };
  return data.items ?? [];
}

export async function disposeSubscriptionTrack(
  id: string,
  videoId: string,
  action: OfflineCleanupAction,
): Promise<boolean> {
  const res = await fetch(
    `${basePath}/api/subscriptions/${id}/tracks/${encodeURIComponent(videoId)}/dispose`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    },
  );
  return res.ok;
}

export async function deleteSubscriptionTrackFile(
  id: string,
  videoId: string,
  block = false,
): Promise<boolean> {
  const res = await fetch(
    `${basePath}/api/subscriptions/${id}/tracks/${encodeURIComponent(videoId)}/delete-file`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ block }),
    },
  );
  return res.ok;
}

export async function unblockSubscriptionTrack(
  id: string,
  videoId: string,
): Promise<boolean> {
  const res = await fetch(
    `${basePath}/api/subscriptions/${id}/tracks/${encodeURIComponent(videoId)}/unblock`,
    { method: "POST", credentials: "include" },
  );
  return res.ok;
}

export async function removeSubscriptionTrackFromList(
  id: string,
  videoId: string,
): Promise<boolean> {
  const res = await fetch(
    `${basePath}/api/subscriptions/${id}/tracks/${encodeURIComponent(videoId)}/remove-from-list`,
    { method: "POST", credentials: "include" },
  );
  return res.ok;
}

export async function downloadSubscriptionTrack(
  id: string,
  videoId: string,
): Promise<SyncResult> {
  const res = await fetch(
    `${basePath}/api/subscriptions/${id}/tracks/${encodeURIComponent(videoId)}/download`,
    { method: "POST", credentials: "include" },
  );
  if (res.status === 409) {
    return { success: false, error: "Job queue is full or track is blacklisted" };
  }
  if (res.status === 404) {
    return { success: false, error: "Track not in subscription" };
  }
  if (!res.ok) {
    return { success: false, error: "Failed to start download" };
  }
  const data = (await res.json()) as { job_ids?: string[] };
  return { success: true, jobIds: data.job_ids ?? [] };
}

// --- Sync Jobs ---

export async function syncSubscription(id: string): Promise<SyncResult> {
  const { data, error, response } = await api.POST(
    "/subscriptions/{subscription_id}/sync",
    {
      params: { path: { subscription_id: id } },
    },
  );

  if (error) {
    if (response.status === 404) {
      return { success: false, error: "Subscription not found" };
    }
    if (response.status === 409) {
      return { success: false, error: "Job queue is full" };
    }
    return {
      success: false,
      error: errorMessage(error, "Failed to create sync job"),
    };
  }

  return { success: true, jobIds: data.job_ids };
}

export async function syncAll(): Promise<SyncResult> {
  const { data, error } = await api.POST("/subscriptions/sync");

  if (error) {
    return {
      success: false,
      error: errorMessage(error, "Failed to create sync jobs"),
    };
  }

  return { success: true, jobIds: data.job_ids };
}

// --- Status ---

export async function getStatus(): Promise<SchedulerStatus | null> {
  const { data, error } = await api.GET("/scheduler");
  if (error) return null;
  return data;
}
