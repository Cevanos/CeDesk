"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { AutoCloseSettings } from "@/lib/api/types";

export function useAutoClose() {
  return useQuery({
    queryKey: queryKeys.autoClose.all,
    queryFn: () => api.get<AutoCloseSettings>("auto-close"),
    staleTime: 60_000,
  });
}

/** PUT takes all three fields. */
export function useSaveAutoClose() {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: (v: { enabled: boolean; after_days: number; notice: string | null }) =>
      api.put<AutoCloseSettings>("auto-close", v),
    onSuccess: (data) => qc.setQueryData(queryKeys.autoClose.all, data),
  });
}
