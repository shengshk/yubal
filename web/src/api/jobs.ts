import { api } from "./client";
import { errorMessage } from "./errors";
import type { components } from "./schema";

export type Job = components["schemas"]["Job"];
export type JobStatus = components["schemas"]["JobStatus"];

export type JobEvent =
  | components["schemas"]["SnapshotEvent"]
  | components["schemas"]["CreatedEvent"]
  | components["schemas"]["UpdatedEvent"]
  | components["schemas"]["DeletedEvent"]
  | components["schemas"]["ClearedEvent"];

export type CreateJobResult =
  | {
      success: true;
      jobId: string;
    }
  | {
      success: false;
      error: string;
      code?: string;
      trackCount?: number;
      limit?: number;
      activeJobId?: string;
    };

export async function createJob(url: string): Promise<CreateJobResult> {
  const { data, error, response } = await api.POST("/jobs", {
    body: { url },
  });

  if (error) {
    const apiError = error as unknown as {
      error?: string;
      message?: string;
      track_count?: string | number;
      limit?: string | number;
    };
    if (apiError.error === "direct_download_limit_exceeded") {
      return {
        success: false,
        error: apiError.message ?? "Direct download limit exceeded",
        code: apiError.error,
        trackCount: Number(apiError.track_count),
        limit: Number(apiError.limit),
      };
    }
    if (response.status === 409) {
      // Job conflict - another job is running
      const conflict = error as { error: string; active_job_id?: string };
      return {
        success: false,
        error: conflict.error,
        activeJobId: conflict.active_job_id,
      };
    }
    if (response.status === 507) {
      return {
        success: false,
        error: errorMessage(error, "Not enough free disk space"),
      };
    }
    if (response.status === 422) {
      // Validation error - extract message from detail
      const validation = error as { detail?: { msg: string }[] };
      const message = validation.detail?.[0]?.msg ?? "Invalid URL";
      return { success: false, error: message };
    }
    return {
      success: false,
      error: errorMessage(error, "Failed to create job"),
    };
  }

  return { success: true, jobId: data.id };
}

export async function deleteJob(jobId: string): Promise<void> {
  const { error } = await api.DELETE("/jobs/{job_id}", {
    params: { path: { job_id: jobId } },
  });

  if (error) throw new Error("Failed to delete job");
}

export async function cancelJob(jobId: string): Promise<void> {
  const { error } = await api.POST("/jobs/{job_id}/cancel", {
    params: { path: { job_id: jobId } },
  });

  if (error) throw new Error("Failed to cancel job");
}
