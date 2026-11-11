"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, request } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import { normalizeMe } from "@/lib/api/schemas";
import type { AgentStatus, Me, SignInResponse } from "@/lib/api/types";
import { hardNavigate } from "@/lib/navigation";
import { can } from "@/lib/permissions";

export function useMe() {
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: async (): Promise<Me> => normalizeMe(await api.get<Record<string, unknown>>("me")),
    staleTime: 5 * 60_000,
    retry: false,
  });
}

/** The signed-in user plus what their role allows. */
export function useCurrentUser() {
  const q = useMe();
  return { ...q, user: q.data, perms: can(q.data?.role) };
}

export function useSignIn() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { workspace: string; email: string; password: string }) =>
      request<SignInResponse>("auth/sign-in", { method: "POST", body: v }).then((r) => r.data),
    onSuccess: () => qc.clear(),
  });
}

export function useSignOut() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<void>("auth/sign-out"),
    // Whatever the server says, this browser is done: drop cached data.
    onSettled: () => {
      qc.clear();
      hardNavigate("/login");
    },
  });
}

export function useSetMyStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (status: AgentStatus) => api.put<void>("me/status", { status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.me });
      qc.invalidateQueries({ queryKey: queryKeys.agents.all });
    },
  });
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (v: { current_password: string; new_password: string }) =>
      api.post<void>("me/password", v),
  });
}

/**
 * Password reset by email. Forgot is always 202; reset is 204,
 * 410 (link used or expired) or 422 (password must be 10 to 128 characters).
 */
export function useForgotPassword() {
  return useMutation({
    meta: { silent: true },
    mutationFn: (v: { workspace: string; email: string }) =>
      api.post<void>("auth/forgot-password", v),
  });
}

export function useResetPassword() {
  return useMutation({
    meta: { silent: true },
    mutationFn: (v: { token: string; new_password: string }) =>
      api.post<void>("auth/reset-password", v),
  });
}
