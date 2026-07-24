import { api } from "./client";

export type CookiesStatus = {
  configured: boolean;
  authenticated: boolean;
  auth_complete: boolean;
  expired: boolean;
  expiring_soon: boolean;
  expires_at: string | null;
  days_remaining: number | null;
  status: "missing" | "ok" | "expired" | "incomplete" | "expiring_soon";
  missing: string[];
};

const EMPTY_STATUS: CookiesStatus = {
  configured: false,
  authenticated: false,
  auth_complete: false,
  expired: false,
  expiring_soon: false,
  expires_at: null,
  days_remaining: null,
  status: "missing",
  missing: [],
};

export async function getCookiesStatus(): Promise<CookiesStatus> {
  const { data, error } = await api.GET("/cookies/status");
  if (error || !data) return EMPTY_STATUS;
  return {
    configured: Boolean(data.configured),
    authenticated: Boolean(data.authenticated ?? false),
    auth_complete: Boolean(data.auth_complete ?? false),
    expired: Boolean(data.expired ?? false),
    expiring_soon: Boolean(data.expiring_soon ?? false),
    expires_at: data.expires_at ?? null,
    days_remaining:
      typeof data.days_remaining === "number" ? data.days_remaining : null,
    status: (data.status as CookiesStatus["status"]) ?? "missing",
    missing: Array.isArray(data.missing) ? data.missing : [],
  };
}

export async function uploadCookies(content: string): Promise<boolean> {
  const { error } = await api.POST("/cookies", {
    body: { content },
  });
  return !error;
}

export async function deleteCookies(): Promise<boolean> {
  const { error } = await api.DELETE("/cookies");
  return !error;
}
