/* eslint-disable react-refresh/only-export-components */
import {
  fetchAuthStatus,
  loginRequest,
  logoutRequest,
  setupRequest,
  type AuthStatus,
} from "@/api/auth";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

type AuthContextValue = {
  ready: boolean;
  status: AuthStatus;
  login: (
    username: string,
    password: string,
    remember: boolean,
  ) => Promise<string | null>;
  setup: (
    username: string,
    password: string,
    confirmPassword: string,
  ) => Promise<string | null>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

const defaultStatus: AuthStatus = {
  enabled: false,
  authenticated: true,
  username: "",
  needsSetup: false,
  setupLocked: false,
  setupExpiresAt: "",
};

export function AuthProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [status, setStatus] = useState<AuthStatus>(defaultStatus);

  const refresh = useCallback(async () => {
    const next = await fetchAuthStatus();
    setStatus(next);
  }, []);

  useEffect(() => {
    let mounted = true;
    fetchAuthStatus()
      .then((next) => {
        if (mounted) setStatus(next);
      })
      .finally(() => {
        if (mounted) setReady(true);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const login = useCallback(
    async (username: string, password: string, remember: boolean) => {
      const result = await loginRequest(username, password, remember);
      if (!result.ok || !result.status) return result.error ?? "login failed";
      setStatus(result.status);
      return null;
    },
    [],
  );

  const setup = useCallback(
    async (username: string, password: string, confirmPassword: string) => {
      const result = await setupRequest(username, password, confirmPassword);
      if (!result.ok || !result.status) return result.error ?? "setup failed";
      setStatus(result.status);
      return null;
    },
    [],
  );

  const logout = useCallback(async () => {
    await logoutRequest();
    await refresh();
  }, [refresh]);

  const value = useMemo(
    () => ({ ready, status, login, setup, logout, refresh }),
    [ready, status, login, setup, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
