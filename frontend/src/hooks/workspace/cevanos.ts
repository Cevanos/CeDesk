"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { CevanosConnection } from "@/lib/api/types";

export function useCevanosConnection() {
  return useQuery({
    queryKey: queryKeys.cevanos.all,
    queryFn: () => api.get<CevanosConnection>("cevanos/connection"),
    staleTime: 30_000,
  });
}

/** Secrets are write-only: send them only when the Admin typed a new one. */
export function useSaveCevanos() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      api_key?: string | null;
      webhook_secret?: string | null;
      close_on_resolve?: boolean;
      team_id?: string | null;
    }) => api.put<CevanosConnection>("cevanos/connection", body),
    onSuccess: (data) => qc.setQueryData(queryKeys.cevanos.all, data),
  });
}

export function useDisconnectCevanos() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<void>("cevanos/connection"),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.cevanos.all }),
  });
}

export function useCheckCevanos() {
  return useMutation({
    mutationFn: () =>
      api.post<{ ok: boolean; missing_scopes: string[]; message: string }>(
        "cevanos/connection/check",
      ),
  });
}
