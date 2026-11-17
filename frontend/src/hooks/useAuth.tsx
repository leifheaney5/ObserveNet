import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from "react";

import { api, ApiError, setCsrfToken, setUnauthorizedHandler, type Me, type Meta } from "@/lib/api";
import { liveStore } from "@/lib/liveStore";

interface AuthState {
  me: Me | null;
  meta: Meta | undefined;
  loading: boolean;
  isAdmin: boolean;
  demoMode: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const meta = useQuery({ queryKey: ["meta"], queryFn: () => api.get<Meta>("/meta"), staleTime: 60_000 });
  const meQuery = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await api.get<Me>("/auth/me");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 5 * 60_000,
    retry: false,
  });
  const me = meQuery.data ?? null;

  useEffect(() => {
    setCsrfToken(me?.csrf_token ?? null);
    if (me) liveStore.connect();
    else liveStore.disconnect();
  }, [me]);

  useEffect(() => {
    setUnauthorizedHandler(() => qc.setQueryData(["me"], null));
    return () => setUnauthorizedHandler(null);
  }, [qc]);

  const login = useCallback(
    async (username: string, password: string) => {
      const res = await api.post<Me>("/auth/login", { username, password });
      setCsrfToken(res.csrf_token);
      qc.setQueryData(["me"], res);
    },
    [qc],
  );

  const logout = useCallback(async () => {
    try {
      await api.post("/auth/logout");
    } finally {
      qc.clear();
      qc.setQueryData(["me"], null);
    }
  }, [qc]);

  const value = useMemo<AuthState>(
    () => ({
      me,
      meta: meta.data,
      loading: meQuery.isLoading,
      isAdmin: me?.user.role === "admin",
      demoMode: me?.demo_mode ?? meta.data?.demo_mode ?? false,
      login,
      logout,
    }),
    [me, meta.data, meQuery.isLoading, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
