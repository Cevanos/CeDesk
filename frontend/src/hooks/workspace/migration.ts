"use client";

import { useQuery } from "@tanstack/react-query";

import { ApiError, api } from "@/lib/api/client";
import type { Cutover, PreMigration } from "@/lib/api/differentiator-types";
import { queryKeys } from "@/lib/api/query-keys";

/** Both screens read and save nothing, so they only fetch when asked. */
const retry = (n: number, e: unknown) =>
  !(e instanceof ApiError && e.status >= 400 && e.status < 500) && n < 1;

/** GET /imports/connections/{id}/report: what would and would not move. */
export function usePreMigration(connectionId: string | null) {
  return useQuery({
    queryKey: queryKeys.migration.report(connectionId ?? ""),
    enabled: !!connectionId,
    queryFn: () => api.get<PreMigration>(`imports/connections/${connectionId}/report`),
    retry,
    staleTime: 0,
    gcTime: 0,
  });
}

/** GET /imports/runs/{id}/cutover: the go-live checklist. */
export function useCutover(runId: string | null) {
  return useQuery({
    queryKey: queryKeys.migration.cutover(runId ?? ""),
    enabled: !!runId,
    queryFn: () => api.get<Cutover>(`imports/runs/${runId}/cutover`),
    retry,
    staleTime: 0,
    gcTime: 0,
  });
}
