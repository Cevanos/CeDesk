"use client";

import { useInfiniteQuery, useMutation } from "@tanstack/react-query";

import { api } from "@/lib/api/client";

/** One row of GET /audit. `detail.changes` is { field: [before, after] }. */
export interface AuditEntry {
  id?: string;
  at: string;
  actor_name?: string | null;
  ip?: string | null;
  area: string;
  action: string;
  target?: string | null;
  detail?: { changes?: Record<string, [unknown, unknown]> } | null;
}

export interface AuditPage {
  entries: AuditEntry[];
  /** Pass as `before` for the next page; null when there are no older entries. */
  next_before: string | null;
  areas: string[];
}

export interface AuditVerifyResult {
  ok: boolean;
  checked: number;
  broken_at?: string | number | null;
  message?: string;
}

export function useAuditLog(area: string) {
  return useInfiniteQuery({
    queryKey: ["audit", area],
    initialPageParam: "",
    queryFn: ({ pageParam, signal }) =>
      api
        .get<Partial<AuditPage>>("audit", { area, limit: 50, before: pageParam }, signal)
        .then((d): AuditPage => ({
          entries: d.entries ?? [],
          next_before: d.next_before ?? null,
          areas: d.areas ?? [],
        })),
    getNextPageParam: (last) => last.next_before ?? undefined,
  });
}

export function useVerifyAudit() {
  return useMutation({
    meta: { silent: true },
    mutationFn: () => api.post<AuditVerifyResult>("audit/verify", {}),
  });
}
