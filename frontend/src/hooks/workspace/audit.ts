"use client";

import { useInfiniteQuery, useMutation } from "@tanstack/react-query";

import { api, listOf, qs } from "@/lib/api/client";

export interface AuditEntry {
  id?: string;
  at?: string;
  created_at?: string;
  actor?: string | { name?: string; email?: string } | null;
  actor_name?: string;
  area?: string;
  action?: string;
  summary?: string;
  target?: string;
  [key: string]: unknown;
}

export interface AuditPage {
  items: AuditEntry[];
  next: string | null;
}

export interface AuditVerifyResult {
  ok?: boolean;
  valid?: boolean;
  checked?: number;
  broken_at?: string | number | null;
  message?: string;
  [key: string]: unknown;
}

export function toAuditPage(d: unknown): AuditPage {
  const o = (d && typeof d === "object" ? d : {}) as Record<string, unknown>;
  const next = o.next_cursor ?? o.cursor ?? o.next;
  return { items: listOf<AuditEntry>(d), next: typeof next === "string" && next ? next : null };
}

export function useAuditLog(area: string) {
  return useInfiniteQuery({
    queryKey: ["audit", area],
    initialPageParam: "",
    queryFn: ({ pageParam, signal }) =>
      api.get<unknown>("audit", { area, cursor: pageParam, limit: 50 }, signal).then(toAuditPage),
    getNextPageParam: (last) => last.next ?? undefined,
  });
}

export function useVerifyAudit() {
  return useMutation({ mutationFn: () => api.post<AuditVerifyResult>("audit/verify", {}) });
}

export function auditExportUrl(area: string) {
  return `/api/audit/export.csv${qs({ area })}`;
}
