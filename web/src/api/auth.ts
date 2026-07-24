import { basePath } from "@/lib/base-path";

export type AuthStatus = {
  enabled: boolean;
  authenticated: boolean;
  username: string;
  needsSetup: boolean;
  setupLocked: boolean;
  setupExpiresAt: string;
};

async function parseJson(res: Response): Promise<Record<string, unknown>> {
  try {
    return (await res.json()) as Record<string, unknown>;
  } catch {
    return {};
  }
}

function toStatus(data: Record<string, unknown>): AuthStatus {
  return {
    enabled: Boolean(data.enabled),
    authenticated: Boolean(data.authenticated),
    username: typeof data.username === "string" ? data.username : "",
    needsSetup: Boolean(data.needsSetup),
    setupLocked: Boolean(data.setupLocked),
    setupExpiresAt:
      typeof data.setupExpiresAt === "string" ? data.setupExpiresAt : "",
  };
}

export async function fetchAuthStatus(): Promise<AuthStatus> {
  const res = await fetch(`${basePath}/api/auth`, { credentials: "include" });
  const data = await parseJson(res);
  if (!res.ok) {
    return {
      enabled: true,
      authenticated: false,
      username: "",
      needsSetup: false,
      setupLocked: false,
      setupExpiresAt: "",
    };
  }
  return toStatus(data);
}

export async function loginRequest(
  username: string,
  password: string,
  remember: boolean,
): Promise<{ ok: boolean; status?: AuthStatus; error?: string }> {
  const res = await fetch(`${basePath}/api/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, remember }),
  });
  const data = await parseJson(res);
  if (!res.ok) {
    return {
      ok: false,
      error:
        typeof data.message === "string"
          ? data.message
          : typeof data.detail === "string"
            ? data.detail
            : "login failed",
    };
  }
  return { ok: true, status: toStatus(data) };
}

export async function setupRequest(
  username: string,
  password: string,
  confirmPassword: string,
): Promise<{ ok: boolean; status?: AuthStatus; error?: string }> {
  const res = await fetch(`${basePath}/api/auth/setup`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, confirmPassword }),
  });
  const data = await parseJson(res);
  if (!res.ok) {
    return {
      ok: false,
      error:
        typeof data.message === "string"
          ? data.message
          : typeof data.detail === "string"
            ? data.detail
            : "setup failed",
    };
  }
  return { ok: true, status: toStatus(data) };
}

export async function logoutRequest(): Promise<void> {
  await fetch(`${basePath}/api/logout`, {
    method: "POST",
    credentials: "include",
  });
}
