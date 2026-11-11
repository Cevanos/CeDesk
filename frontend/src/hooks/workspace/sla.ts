"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";
import type { SlaSettings, SlaTarget } from "@/lib/api/types";

/** Everyone can read the SLA settings; only Admin can change targets. */
export function useSla() {
  return useQuery({
    queryKey: queryKeys.sla.all,
    queryFn: () => api.get<SlaSettings>("sla"),
    staleTime: 60_000,
  });
}

export function useSaveSlaTargets() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (
      targets: Pick<SlaTarget, "priority" | "first_reply_minutes" | "resolve_minutes">[],
    ) => api.put<SlaSettings>("sla/targets", { targets }),
    onSuccess: (data) => qc.setQueryData(queryKeys.sla.all, data),
  });
}
