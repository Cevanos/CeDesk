"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api/client";
import { queryKeys } from "@/lib/api/query-keys";

export interface DemoResult {
  created?: number;
  deleted?: number;
}

function useDemoCall(path: "seed" | "samples" | "reset") {
  const qc = useQueryClient();
  return useMutation({
    meta: { silent: true },
    mutationFn: () => api.post<DemoResult | null>(`demo/${path}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.tickets.all }),
  });
}

/** 201 `{created, deleted}`. 403: demo is off here or this is production. 409: already loaded. */
export const useSeedDemo = () => useDemoCall("seed");
/** 201: six sample tickets. */
export const useLoadSamples = () => useDemoCall("samples");
/** Removes demo tickets only. Real and imported tickets stay. */
export const useResetDemo = () => useDemoCall("reset");
